import { app, shell, BrowserWindow, ipcMain, dialog, globalShortcut } from 'electron'
import { join, resolve } from 'path'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { spawn, ChildProcess } from 'child_process'
import * as http from 'http'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { theme } from '../shared/theme'

// ---------------------------------------------------------------------------
// Server lifecycle
// ---------------------------------------------------------------------------

let serverProcess: ChildProcess | null = null

function findKampBinary(): string | null {
  // app.getAppPath() returns the kamp_ui directory; .venv lives one level up (repo root)
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

  // detached: true puts the server in its own process group so that
  // stopServer() can kill the group (server + uvicorn + mpv) all at once.
  serverProcess = spawn(binary, ['server'], {
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe']
  })

  serverProcess.stdout?.on('data', (d) => console.log('[kamp server]', String(d).trimEnd()))
  serverProcess.stderr?.on('data', (d) => console.error('[kamp server]', String(d).trimEnd()))
  serverProcess.on('exit', (code) => {
    console.log(`[kamp server] exited (${code})`)
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

function createWindow(): void {
  const bounds = loadWindowBounds()

  // Create the browser window.
  const mainWindow = new BrowserWindow({
    ...bounds,
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
  // Set app user model id for windows
  electronApp.setAppUserModelId('com.electron')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // IPC test
  ipcMain.on('ping', () => console.log('pong'))

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
