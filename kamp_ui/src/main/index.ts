import {
  app,
  shell,
  BrowserWindow,
  ipcMain,
  dialog,
  Menu,
  session,
  net,
  screen as electronScreen
} from 'electron'
import { join, resolve } from 'path'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { homedir } from 'os'
import { spawn, ChildProcess } from 'child_process'
import { createInterface } from 'readline'
import * as http from 'http'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { theme } from '../shared/theme'
import { discoverExtensions, installExtension, uninstallExtension } from './extensions'
import { readManifest } from './communityManifest'

// ---------------------------------------------------------------------------
// Auth token
// ---------------------------------------------------------------------------

function kampTokenFilePath(): string {
  if (process.platform === 'win32') {
    return join(process.env.LOCALAPPDATA ?? join(homedir(), 'AppData', 'Local'), 'kamp', '.token')
  }
  return join(homedir(), '.local', 'share', 'kamp', '.token')
}

// Re-read on every call so a daemon restart's fresh token is always used,
// matching the same strategy used by the preload and renderer.
function readKampToken(): string | null {
  try {
    return readFileSync(kampTokenFilePath(), 'utf8').trim()
  } catch {
    return null
  }
}

/** Return headers with the auth token attached. */
function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = readKampToken()
  return token ? { 'X-Kamp-Token': token, ...extra } : { ...extra }
}

// Set the app name before the app is ready so the macOS menu bar and all
// default menu items ("About Kamp", "Quit Kamp") reflect the correct name.
app.setName('Kamp')

// ---------------------------------------------------------------------------
// Swift Now Playing helper
// ---------------------------------------------------------------------------
// macOS routes media keys to the process that owns MPNowPlayingInfoCenter.
// Chromium's Media Session API only activates that ownership when Chromium itself
// is producing audio output — which Kamp does not (audio goes through mpv).
// A dedicated Swift helper runs as a hidden AppKit application, which is the
// minimum requirement for MPRemoteCommandCenter to receive media key callbacks.
// The helper owns the Now Playing session; Electron relays player state to it
// via stdin and receives media key events back on stdout.

let _helper: ChildProcess | null = null
let _isPlaying = false // tracks current playback state to correctly handle togglePlayPause
// Cache artwork as base64 strings keyed by "album_artist|album" so we only
// fetch once per album rather than on every position-tick update.
const _artworkCache = new Map<string, string>()
// Key of the album currently loaded in the player ("album_artist|album"),
// used to guard deferred artwork re-sends against stale in-flight fetches.
let _currentAlbumKey = ''

function findNowPlayingBinary(): string | null {
  if (app.isPackaged) {
    const bundled = join(process.resourcesPath, 'now-playing-helper')
    if (existsSync(bundled)) return bundled
  }
  // Dev: built binary lives at kamp_ui/resources/now-playing-helper
  const dev = resolve(app.getAppPath(), 'resources/now-playing-helper')
  if (existsSync(dev)) return dev
  return null
}

function sendToHelper(msg: object): void {
  if (!_helper?.stdin?.writable) return
  _helper.stdin.write(JSON.stringify(msg) + '\n')
}

function postToPlayer(path: string): void {
  net
    .fetch(`http://127.0.0.1:8000${path}`, { method: 'POST', headers: authHeaders() })
    .catch(() => {})
}

function startNowPlayingHelper(): void {
  const binary = findNowPlayingBinary()
  if (!binary) {
    console.warn('[kamp] now-playing-helper binary not found — media keys disabled')
    return
  }
  _helper = spawn(binary, [], { stdio: ['pipe', 'pipe', 'inherit'] })
  _helper.on('exit', () => {
    _helper = null
  })

  // Parse newline-delimited JSON events emitted by the helper to stdout.
  const rl = createInterface({ input: _helper.stdout! })
  rl.on('line', (line) => {
    try {
      const evt = JSON.parse(line) as { event: string }
      switch (evt.event) {
        case 'play':
          postToPlayer('/api/v1/player/resume')
          break
        case 'pause':
          postToPlayer('/api/v1/player/pause')
          break
        case 'togglePlayPause':
          // Physical play/pause key fires togglePlayPause — check actual state to toggle.
          postToPlayer(_isPlaying ? '/api/v1/player/pause' : '/api/v1/player/resume')
          break
        case 'next':
          postToPlayer('/api/v1/player/next')
          break
        case 'prev':
          postToPlayer('/api/v1/player/prev')
          break
      }
    } catch {
      // Ignore malformed lines
    }
  })
}

function stopNowPlayingHelper(): void {
  sendToHelper({ cmd: 'stop' })
  _helper?.kill()
  _helper = null
}

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
  const spawnEnv: NodeJS.ProcessEnv = { ...process.env }
  if (mpvBin) spawnEnv['KAMP_MPV_BIN'] = mpvBin
  // Tell the daemon it's running in dev mode so it allows the Vite dev server
  // origin (http://localhost:5173) in CORS — the renderer loads from there.
  if (is.dev) spawnEnv['KAMP_DEV'] = '1'
  serverProcess = spawn(binary, ['daemon'], {
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: spawnEnv
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
          headers: authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(payload)
        })
        if (!res.ok) throw new Error(`login-complete returned ${res.status}`)
        // Cookies are now persisted in the DB.  Remove them from the Electron
        // session store so they don't linger as plaintext in the Chromium
        // profile on disk.
        // NOTE: in the PyInstaller bundle, _ProxySession routes requests via
        // net.fetch which uses session.defaultSession — those requests will fail
        // until the next startup reloads cookies from DB into the session.
        // This is acceptable; the frozen-bundle cookie-reload path is tracked
        // separately as a future improvement.
        for (const c of payload.cookies) {
          await session.defaultSession.cookies.remove('https://bandcamp.com', c.name)
        }
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
  const { width: sw, height: sh } = electronScreen.getPrimaryDisplay().workAreaSize
  const w = 900
  const h = 1000
  const defaults: WindowBounds = {
    x: Math.round((sw - w) / 2),
    y: Math.round((sh - h) / 2),
    width: w,
    height: h
  }
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

  // Forward player state from the preload to the Now Playing helper.
  // The preload sends this on every track.changed / play_state.changed WS event
  // and once on WS open (initial fetch), so the helper stays in sync.
  ipcMain.on(
    'player:state-changed',
    (
      _event,
      state: {
        current_track: {
          title: string
          artist: string
          album_artist: string
          album: string
          embedded_art: boolean
        } | null
        playing: boolean
        position: number
        duration: number
      }
    ) => {
      _isPlaying = state.playing
      if (!state.current_track) {
        _currentAlbumKey = ''
        sendToHelper({ cmd: 'stop' })
        return
      }
      const track = state.current_track

      // Build the update message synchronously from whatever artwork we already
      // have cached, then dispatch immediately so position/playback state stays
      // in sync without waiting for a network round-trip.
      const artKey = `${track.album_artist}|${track.album}`
      _currentAlbumKey = artKey
      const cachedArtwork = _artworkCache.get(artKey)

      sendToHelper({
        cmd: 'update',
        title: track.title,
        artist: track.artist,
        album: track.album,
        position: state.position,
        duration: state.duration,
        playing: state.playing,
        ...(cachedArtwork !== undefined ? { artworkBase64: cachedArtwork } : {})
      })

      // Fetch artwork in the background on cache miss so subsequent updates for
      // the same album include it.  net.fetch routes through Chromium's stack
      // (session.defaultSession) which already has the kamp server in its allow
      // list and avoids the Cloudflare TLS fingerprint issue for local requests.
      if (track.embedded_art && cachedArtwork === undefined) {
        const url =
          `http://127.0.0.1:8000/api/v1/album-art` +
          `?album_artist=${encodeURIComponent(track.album_artist)}` +
          `&album=${encodeURIComponent(track.album)}`
        net
          .fetch(url, { headers: authHeaders() })
          .then((res) => {
            if (!res.ok) return
            return res.arrayBuffer()
          })
          .then((buf) => {
            if (!buf) return
            const b64 = Buffer.from(buf).toString('base64')
            _artworkCache.set(artKey, b64)
            // Re-send the full update (not just artworkBase64) because
            // applyUpdate in the Swift helper always replaces nowPlayingInfo
            // from scratch — a partial message would drop title/artist/etc.
            // Guard against a track change that arrived while fetch was
            // in-flight by comparing against the live _currentAlbumKey.
            if (_currentAlbumKey === artKey) {
              sendToHelper({
                cmd: 'update',
                title: track.title,
                artist: track.artist,
                album: track.album,
                position: state.position,
                duration: state.duration,
                playing: state.playing,
                artworkBase64: b64
              })
            }
          })
          .catch(() => {
            // Mark cache so we don't retry on every position tick.
            _artworkCache.set(artKey, '')
          })
      }
    }
  )

  ipcMain.handle('kamp:get-extensions', () => discoverExtensions())

  ipcMain.handle('bandcamp:begin-login', () => openBandcampLogin())

  type BandcampCookie = {
    name: string
    value: string
    domain: string
    path: string
    expires: number
    httpOnly: boolean
    secure: boolean
    sameSite: string
  }

  ipcMain.handle(
    'bandcamp:proxy-fetch',
    async (
      _event,
      req: {
        id: string
        url: string
        method: string
        headers: Record<string, string>
        body: string | null
      }
    ) => {
      // In the PyInstaller bundle, cookies are stored in library.db rather than
      // in session.defaultSession (cleared after login to avoid plaintext on disk).
      // Fetch them from the daemon endpoint rather than reading from the WS payload
      // so auth cookies are never broadcast to all WS clients.
      const cookieResp = await net.fetch('http://127.0.0.1:8000/api/v1/bandcamp/session-cookies', {
        headers: authHeaders()
      })
      const { cookies } = (await cookieResp.json()) as { cookies: BandcampCookie[] }
      const injectedNames: string[] = []
      for (const c of cookies) {
        const sameSiteLower = c.sameSite?.toLowerCase()
        const sameSite = (['lax', 'strict', 'no_restriction', 'unspecified'] as const).includes(
          sameSiteLower as never
        )
          ? (sameSiteLower as 'unspecified' | 'no_restriction' | 'lax' | 'strict')
          : 'unspecified'
        try {
          await session.defaultSession.cookies.set({
            url: `https://${c.domain.replace(/^\./, '')}`,
            name: c.name,
            value: c.value,
            domain: c.domain,
            path: c.path,
            secure: c.secure,
            httpOnly: c.httpOnly,
            expirationDate: c.expires > 0 ? c.expires : undefined,
            sameSite
          })
          injectedNames.push(c.name)
        } catch {
          // Non-fatal: skip cookies that fail to set (e.g. malformed domain).
        }
      }

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

      // Remove injected cookies so they don't linger in the session store between syncs.
      for (const name of injectedNames) {
        await session.defaultSession.cookies.remove('https://bandcamp.com', name)
      }

      await net.fetch('http://127.0.0.1:8000/api/v1/bandcamp/fetch-result', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
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

  // Inject X-Kamp-Token on every outgoing request to the local API — including
  // <img src> image loads, which cannot carry custom headers from JS.
  // This keeps art URLs free of the token so the browser HTTP cache is stable
  // across daemon restarts (the token changes each time, but the URL doesn't).
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: ['http://127.0.0.1:8000/*', 'ws://127.0.0.1:8000/*'] },
    (details, callback) => {
      const token = readKampToken()
      const requestHeaders = { ...details.requestHeaders }
      if (token) requestHeaders['X-Kamp-Token'] = token
      callback({ requestHeaders })
    }
  )

  // Start the Now Playing helper after the server so it can accept key events
  // immediately. The preload primes it with current player state on WS connect.
  startNowPlayingHelper()

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

// Kill the server and Now Playing helper when the app exits.
// `will-quit` handles graceful quit; `process.on('exit')` is the synchronous
// fallback that fires even if the event loop is torn down abruptly.
app.on('will-quit', () => {
  stopNowPlayingHelper()
  stopServer()
})
process.on('exit', stopServer)

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
