import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
import { readFileSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'
import { buildKampAPI } from './kampAPI'

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
  openDirectory: (): Promise<string | null> => ipcRenderer.invoke('open-directory'),
  onOpenPreferences: (callback: () => void): (() => void) => {
    const handler = (): void => callback()
    ipcRenderer.on('open-preferences', handler)
    // Return a cleanup function so the caller can unsubscribe.
    return () => ipcRenderer.off('open-preferences', handler)
  },
  bandcamp: {
    /** Open the Bandcamp login BrowserWindow. Resolves when login succeeds or the window is closed. */
    beginLogin: (): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke('bandcamp:begin-login')
  },
  // Re-reads from disk so Electron picks up a fresh token after daemon restart.
  getApiToken: (): string | null => _readKampToken()
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
