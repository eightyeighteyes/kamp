import { app, shell, BrowserWindow, ipcMain, dialog, globalShortcut, Menu, session, net } from 'electron'
import { join, resolve } from 'path'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { spawn, ChildProcess } from 'child_process'
import * as http from 'http'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { theme } from '../shared/theme'
import { discoverExtensions, installExtension, uninstallExtension } from './extensions'
import { readManifest } from './communityManifest'

// Set the app name before the app is ready so the macOS menu bar and all
// default menu items ("About Kamp", "Quit Kamp") reflect the correct name.
app.setName('Kamp')

// ---------------------------------------------------------------------------
// Server lifecycle
// ---------------------------------------------------------------------------

let serverProcess: ChildProcess | null = null

function findKampBinary(): string | null {
  if (app.isPackaged) {
    // Inside Kamp.app, electron-builder places extraResources directly under
    // Contents/Resources/. The PyInstaller onedir bundle's executable is at
    // Resources/kamp/kamp.
    const bundled = join(process.resourcesPath, 'kamp', 'kamp')
    if (existsSync(bundled)) return bundled
  }
  // Dev fallback: app.getAppPath() returns the kamp_ui directory;
  // .venv lives one level up (repo root).
  const venvBin = resolve(app.getAppPath(), '../.venv/bin/kamp')
  if (existsSync(venvBin)) return venvBin
  return null
}

function isServerRunning(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get('http://127.0.0.1:8000/api/v1/player/state', (res) => {
      res.resume() // discard body
      resolve(res.statusCode !== undefined)
    })
    req.on('error', () => resolve(false))
    req.setTimeout(1000, () => {
      req.destroy()
      resolve(false)
    })
  })
}

async function startServer(): Promise<void> {
  if (await isServerRunning()) return

  const binary = findKampBinary()
  if (!binary) {
    console.error('[kamp] kamp binary not found — start the server manually')
    return
  }

  // When running from the .app bundle, point the server at the bundled mpv
  // binary so it works on machines without Homebrew or mpv in PATH.
  const mpvBin = app.isPackaged ? join(process.resourcesPath, 'mpv') : undefined

  // detached: true puts the daemon in its own process group so that
  // stopServer() can kill the group (daemon + mpv) all at once.
  serverProcess = spawn(binary, ['daemon'], {
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: mpvBin ? { ...process.env, KAMP_MPV_BIN: mpvBin } : process.env
  })

  serverProcess.stdout?.on('data', (d) => console.log('[kamp daemon]', String(d).trimEnd()))
  serverProcess.stderr?.on('data', (d) => console.error('[kamp daemon]', String(d).trimEnd()))
  serverProcess.on('exit', (code) => {
    console.log(`[kamp daemon] exited (${code})`)
    serverProcess = null
  })
}

function stopServer(): void {
  if (serverProcess?.pid) {
    try {
      // Negative PID kills the entire process group (server + all children).
      process.kill(-serverProcess.pid, 'SIGKILL')
    } catch {
      // Process may have already exited.
    }
    serverProcess = null
  }
}

// ---------------------------------------------------------------------------
// Bandcamp login via BrowserWindow
// ---------------------------------------------------------------------------

type BandcampLoginResult = { ok: true } | { ok: false; error: string }

/**
 * Open a BrowserWindow pointing at bandcamp.com/login.  Poll the default
 * session's cookie jar once per second; when ``js_logged_in=1`` appears,
 * collect all .bandcamp.com cookies, format them as a Playwright
 * storage_state payload, and POST to the Python daemon's login-complete
 * endpoint so it can persist bandcamp_session.json.
 *
 * Returns { ok: true } on success or { ok: false, error } if the user closes
 * the window without completing login or if the HTTP call fails.
 */
async function openBandcampLogin(): Promise<BandcampLoginResult> {
  return new Promise((resolve) => {
    const loginWin = new BrowserWindow({
      width: 820,
      height: 720,
      title: 'Log in to Bandcamp',
      // No preload — this is a plain browser tab, not a Kamp UI window.
      webPreferences: { sandbox: true }
    })

    loginWin.loadURL('https://bandcamp.com/login')

    let settled = false
    // Mutable ref — clearInterval needs to see the id assigned below.
    const poll = { timer: undefined as ReturnType<typeof setInterval> | undefined }

    const settle = async (result: BandcampLoginResult): Promise<void> => {
      if (settled) return
      settled = true
      clearInterval(poll.timer)
      if (!loginWin.isDestroyed()) loginWin.destroy()
      resolve(result)
    }

    const sendLoginComplete = async (): Promise<void> => {
      const cookies = await session.defaultSession.cookies.get({ url: 'https://bandcamp.com' })
      const payload = {
        cookies: cookies.map((c) => ({
          name: c.name,
          value: c.value,
          domain: c.domain ?? '.bandcamp.com',
          path: c.path ?? '/',
          expires: c.expirationDate ?? -1,
          httpOnly: c.httpOnly ?? false,
          secure: c.secure ?? false,
          sameSite: c.sameSite ?? 'Lax'
        })),
        origins: []
      }
      try {
        const res = await net.fetch('http://127.0.0.1:8000/api/v1/bandcamp/login-complete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        })
        if (!res.ok) throw new Error(`login-complete returned ${res.status}`)
        await settle({ ok: true })
      } catch (err) {
        await settle({ ok: false, error: String(err) })
      }
    }

    poll.timer = setInterval(async () => {
      if (settled) return
      const cookies = await session.defaultSession.cookies.get({ url: 'https://bandcamp.com' })
      const loggedIn = cookies.some((c) => c.name === 'js_logged_in' && c.value === '1')
      if (loggedIn) await sendLoginComplete()
    }, 1000)

    loginWin.on('closed', () => {
      void settle({ ok: false, error: 'Login window closed before completing sign-in.' })
    })
  })
}

// ---------------------------------------------------------------------------
// Bandcamp HTTP proxy relay
// ---------------------------------------------------------------------------
// bandcamp.py in the PyInstaller bundle cannot reach bandcamp.com directly
// because PyInstaller's OpenSSL has a different TLS fingerprint that Cloudflare
// flags.  The server broadcasts a "bandcamp.proxy-fetch" WebSocket event; the
// preload receives it and calls ipcRenderer.invoke("bandcamp:proxy-fetch"),
// which is handled here.  net.fetch uses session.defaultSession so Chromium's
// TLS stack (real browser fingerprint, cf_clearance cookie) handles the request.

// net.fetch accepts a `session` option at runtime (Chromium extension) but the
// Electron TypeScript type only exposes the base RequestInit.  Cast via unknown
// so we can pass the defaultSession without disabling the whole call site.
type NetFetchOptions = Parameters<typeof net.fetch>[1] & { session?: Electron.Session }

type WindowBounds = { x: number; y: number; width: number; height: number }

function loadWindowBounds(): WindowBounds {
  const defaults: WindowBounds = { x: 0, y: 0, width: 900, height: 670 }
  try {
    const file = join(app.getPath('userData'), 'window-state.json')
    return JSON.parse(readFileSync(file, 'utf8')) as WindowBounds
  } catch {
    return defaults
  }
}

function saveWindowBounds(win: BrowserWindow): void {
  try {
    const file = join(app.getPath('userData'), 'window-state.json')
    writeFileSync(file, JSON.stringify(win.getBounds()))
  } catch {
    // Non-critical — ignore write errors.
  }
}

function buildAppMenu(): void {
  // Build a minimal application menu that exposes Preferences (Cmd+,) under
  // the app name menu on macOS, while preserving standard Edit and Window items.
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: app.name,
      submenu: [
        {
          label: 'Preferences…',
          accelerator: 'CmdOrCtrl+,',
          click: () => {
            // Send an IPC message to the focused renderer so it opens the dialog.
            const win = BrowserWindow.getFocusedWindow()
            win?.webContents.send('open-preferences')
          }
        },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' }
      ]
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' }
      ]
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    },
    {
      label: 'Window',
      role: 'window',
      submenu: [{ role: 'minimize' }, { role: 'zoom' }, { role: 'close' }]
    }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function createWindow(): void {
  const bounds = loadWindowBounds()

  // Create the browser window.
  const mainWindow = new BrowserWindow({
    ...bounds,
    minWidth: 800,
    minHeight: 600,
    show: false,
    autoHideMenuBar: true,
    // Match the app's dark background so the native window surface never
    // shows through as white gutters during resize or repaint.
    backgroundColor: theme.bg,
    // Remove the native title bar on macOS — the view-tabs nav takes its place
    // and acts as the drag region. Traffic lights remain visible at top-left.
    ...(process.platform === 'darwin' ? { titleBarStyle: 'hidden' as const } : {}),
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  // Persist bounds on every move/resize so the next launch restores them.
  mainWindow.on('moved', () => saveWindowBounds(mainWindow))
  mainWindow.on('resized', () => saveWindowBounds(mainWindow))

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(async () => {
  electronApp.setAppUserModelId('com.kamp.app')

  buildAppMenu()

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // IPC test
  ipcMain.on('ping', () => console.log('pong'))

  ipcMain.handle('kamp:get-extensions', () => discoverExtensions())

  ipcMain.handle('bandcamp:begin-login', () => openBandcampLogin())

  ipcMain.handle(
    'bandcamp:proxy-fetch',
    async (
      _event,
      req: { id: string; url: string; method: string; headers: Record<string, string>; body: string | null }
    ) => {
      let status = 502
      let body = 'net.fetch error'
      let contentType = 'text/plain'

      try {
        const opts: NetFetchOptions = {
          method: req.method,
          headers: req.headers as HeadersInit,
          body: req.body ?? undefined,
          session: session.defaultSession
        }
        const fetchResp = await net.fetch(req.url, opts as Parameters<typeof net.fetch>[1])
        status = fetchResp.status
        body = await fetchResp.text()
        contentType = fetchResp.headers.get('content-type') ?? 'text/html'
      } catch (err) {
        body = String(err)
      }

      await net.fetch('http://127.0.0.1:8000/api/v1/bandcamp/fetch-result', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: req.id, status, body, content_type: contentType })
      })
    }
  )

  ipcMain.handle('kamp:install-extension', (_event, source: 'npm' | 'local', nameOrPath: string) =>
    installExtension(source, nameOrPath)
  )

  ipcMain.handle('kamp:uninstall-extension', (_event, id: string) => uninstallExtension(id))

  ipcMain.handle('open-directory', async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory'],
      title: 'Choose Music Library Folder'
    })
    return result.canceled ? null : result.filePaths[0]
  })

  // Register next/prev media keys. mpv handles play/pause natively; we
  // only need to route next/prev since our queue lives outside mpv.
  // globalShortcut works at OS level so it fires even when the window is hidden.
  if (process.platform === 'darwin') {
    globalShortcut.register('MediaNextTrack', () => {
      http
        .request({ hostname: '127.0.0.1', port: 8000, path: '/api/v1/player/next', method: 'POST' })
        .end()
    })
    globalShortcut.register('MediaPreviousTrack', () => {
      http
        .request({ hostname: '127.0.0.1', port: 8000, path: '/api/v1/player/prev', method: 'POST' })
        .end()
    })
  }

  // Reinstall community extensions from the manifest. This ensures they are
  // present in node_modules in packaged builds where node_modules may not
  // survive between launches. Already-installed packages are a fast no-op.
  const manifest = readManifest()
  if (manifest.length > 0) {
    const results = await Promise.allSettled(
      manifest.map((entry) =>
        entry.source === 'local'
          ? installExtension('local', entry.path)
          : installExtension('npm', entry.name)
      )
    )
    results.forEach((r, i) => {
      if (r.status === 'fulfilled' && !r.value.ok) {
        console.warn(`[kamp] failed to reinstall extension "${manifest[i].name}": ${r.value.error}`)
      } else if (r.status === 'rejected') {
        console.warn(`[kamp] failed to reinstall extension "${manifest[i].name}":`, r.reason)
      }
    })
  }

  // Start the kamp server if it isn't already running. The renderer's
  // existing reconnect loop handles the brief gap while the server starts up.
  await startServer()

  createWindow()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// Unregister global shortcuts before quitting — they persist for the process lifetime otherwise.
app.on('will-quit', () => globalShortcut.unregisterAll())

// Kill the server we launched when the app exits.
// `will-quit` handles graceful quit; `process.on('exit')` is the synchronous
// fallback that fires even if the event loop is torn down abruptly.
app.on('will-quit', stopServer)
process.on('exit', stopServer)

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
