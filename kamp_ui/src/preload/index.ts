import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'

// Custom APIs for renderer
const api = {
  openDirectory: (): Promise<string | null> => ipcRenderer.invoke('open-directory'),
  onOpenPreferences: (callback: () => void): (() => void) => {
    const handler = (): void => callback()
    ipcRenderer.on('open-preferences', handler)
    // Return a cleanup function so the caller can unsubscribe.
    return () => ipcRenderer.off('open-preferences', handler)
  }
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
}
