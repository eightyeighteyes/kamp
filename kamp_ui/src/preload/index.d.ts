import { ElectronAPI } from '@electron-toolkit/preload'
import type { KampAPI } from '../shared/kampAPI'

declare global {
  interface Window {
    electron: ElectronAPI
    api: {
      openDirectory: () => Promise<string | null>
      onOpenPreferences: (callback: () => void) => () => void
      bandcamp: {
        beginLogin: () => Promise<{ ok: boolean; error?: string }>
      }
      getApiToken: () => string | null
    }
    KampAPI: KampAPI
  }
}
