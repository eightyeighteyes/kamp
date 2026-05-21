import { ElectronAPI } from '@electron-toolkit/preload'
import type { KampAPI } from '../shared/kampAPI'

declare global {
  interface Window {
    electron: ElectronAPI
    api: {
      isPackaged: boolean
      openDirectory: () => Promise<string | null>
      onOpenPreferences: (callback: () => void) => () => void
      bandcamp: {
        beginLogin: () => Promise<{ ok: boolean; error?: string }>
        onSyncStatus: (callback: (state: 'idle' | 'syncing') => void) => () => void
        triggerSync: () => Promise<{ ok: boolean }>
        triggerSyncAll: () => Promise<{ ok: boolean }>
      }
      pipeline: {
        onStage: (callback: (stage: string) => void) => () => void
      }
      onUpdateAvailable: (
        callback: (data: { version: string; notes: string }) => void
      ) => () => void
      dismissUpdate: (version: string) => Promise<void>
      getApiToken: () => string | null
      showItemInFolder: (filePath: string) => void
    }
    KampAPI: KampAPI
  }
}
