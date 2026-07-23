import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
import { readFileSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'
import { buildKampAPI, onBandcampSyncStatus, onPipelineStage } from './kampAPI'

// In packaged apps the preload runs from inside app.asar; in dev it runs
// directly from the filesystem. The .asar extension in __dirname is definitive.
const isPackaged: boolean = __dirname.includes('.asar')

// Read the app version from package.json at preload init time. __dirname in
// the compiled preload is always two levels below the package root (out/preload/),
// so ../../package.json resolves correctly in both dev and packaged modes.
const appVersion: string = (
  JSON.parse(readFileSync(join(__dirname, '../../package.json'), 'utf8')) as {
    version: string
  }
).version

function _kampTokenFilePath(): string {
  if (process.platform === 'win32') {
    return join(process.env.LOCALAPPDATA ?? join(homedir(), 'AppData', 'Local'), 'kamp', '.token')
  }
  return join(homedir(), '.local', 'share', 'kamp', '.token')
}

function _readKampToken(): string | null {
  try {
    return readFileSync(_kampTokenFilePath(), 'utf8').trim()
  } catch {
    return null
  }
}

// Custom APIs for renderer
const api = {
  isPackaged,
  appVersion,
  openDirectory: (): Promise<string | null> => ipcRenderer.invoke('open-directory'),
  onOpenPreferences: (callback: () => void): (() => void) => {
    const handler = (): void => callback()
    ipcRenderer.on('open-preferences', handler)
    // Return a cleanup function so the caller can unsubscribe.
    return () => ipcRenderer.off('open-preferences', handler)
  },
  // View cycling (KAMP-560): the main process forwards Ctrl+Tab / Ctrl+Shift+Tab
  // here as a direction so the renderer can advance through the top-level tabs.
  onCycleView: (callback: (direction: 'next' | 'prev') => void): (() => void) => {
    const handler = (_: Electron.IpcRendererEvent, direction: 'next' | 'prev'): void =>
      callback(direction)
    ipcRenderer.on('cycle-view', handler)
    return () => ipcRenderer.off('cycle-view', handler)
  },
  bandcamp: {
    /** Open the Bandcamp login BrowserWindow. Resolves when login succeeds or the window is closed. */
    beginLogin: (): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke('bandcamp:begin-login'),
    /** Subscribe to sync status changes pushed via WebSocket. Returns an unsubscribe function. */
    onSyncStatus: onBandcampSyncStatus,
    /** Trigger a manual Bandcamp sync. Returns immediately; progress arrives via onSyncStatus. */
    triggerSync: (): Promise<{ ok: boolean }> => {
      const token = _readKampToken()
      return fetch('http://127.0.0.1:47483/api/v1/bandcamp/sync', {
        method: 'POST',
        headers: token ? { 'X-Kamp-Token': token } : {}
      }).then((r) => r.json())
    },
    /** Clear sync state and re-download all Bandcamp purchases. Returns immediately. */
    triggerSyncAll: (): Promise<{ ok: boolean }> => {
      const token = _readKampToken()
      return fetch('http://127.0.0.1:47483/api/v1/bandcamp/sync-all', {
        method: 'POST',
        headers: token ? { 'X-Kamp-Token': token } : {}
      }).then((r) => r.json())
    }
  },
  pipeline: {
    onStage: onPipelineStage
  },
  onUpdateAvailable: (
    callback: (data: { version: string; notes: string }) => void
  ): (() => void) => {
    const handler = (
      _: Electron.IpcRendererEvent,
      data: { version: string; notes: string }
    ): void => callback(data)
    ipcRenderer.on('update:available', handler)
    // Also pull any update that resolved before this listener was registered.
    void ipcRenderer
      .invoke('update:get-pending')
      .then((pending: { version: string; notes: string } | null) => {
        if (pending) callback(pending)
      })
    return () => ipcRenderer.off('update:available', handler)
  },
  dismissUpdate: (version: string): Promise<void> => ipcRenderer.invoke('update:dismiss', version),
  // Re-reads from disk so Electron picks up a fresh token after daemon restart.
  getApiToken: (): string | null => _readKampToken(),
  showItemInFolder: (filePath: string): void =>
    ipcRenderer.send('shell:show-item-in-folder', filePath),
  // KAMP-558: open a folder (e.g. the Music Library) in Finder/Explorer.
  openPath: (path: string): void => ipcRenderer.send('shell:open-path', path),
  openExternal: (url: string): void => ipcRenderer.send('shell:open-external', url),
  // KAMP-631: sync native window chrome (background + Windows titlebar overlay)
  // to a theme. The main process derives the colors from the shared themes table
  // so there is one source of truth; the renderer just names the active theme.
  syncThemeChrome: (name: string): void => ipcRenderer.send('kamp:sync-theme-chrome', name)
}

const kampAPI = buildKampAPI()

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
    contextBridge.exposeInMainWorld('KampAPI', kampAPI)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
  // @ts-ignore (define in dts)
  window.KampAPI = kampAPI
}
