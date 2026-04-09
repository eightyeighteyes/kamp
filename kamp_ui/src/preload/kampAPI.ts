/**
 * Builds the window.KampAPI object that is exposed to the renderer via contextBridge.
 *
 * This module runs in the preload context (has Node.js / Electron APIs) but the
 * returned object contains only plain values and functions — no Node.js objects
 * leak into the renderer.
 */

import { ipcRenderer } from 'electron'
import type { KampAPI, PanelManifest, ExtensionInstallResult, PlayerState } from '../shared/kampAPI'

const SERVER_URL = 'http://127.0.0.1:8000'
const WS_URL = 'ws://127.0.0.1:8000/api/v1/ws'

const panelRegistry: PanelManifest[] = []
// Callbacks registered by the renderer via panels.onRegister().
// contextBridge wraps renderer functions so they can be called from the preload.
const registerCallbacks = new Set<(manifest: PanelManifest) => void>()

// Push-event subscribers. Populated by onTrackChange / onPlayStateChange.
// A single shared WebSocket is lazily created and fans out to all subscribers.
const trackChangeCallbacks = new Set<(state: PlayerState) => void>()
const playStateChangeCallbacks = new Set<(state: PlayerState) => void>()
let _pushWs: WebSocket | null = null

function ensurePushWs(): void {
  if (_pushWs && _pushWs.readyState < WebSocket.CLOSING) return
  const ws = new WebSocket(WS_URL)
  ws.addEventListener('message', (evt) => {
    try {
      const msg = JSON.parse(evt.data as string) as { type: string } & PlayerState
      if (msg.type === 'track.changed') {
        trackChangeCallbacks.forEach((cb) => cb(msg))
      } else if (msg.type === 'play_state.changed') {
        playStateChangeCallbacks.forEach((cb) => cb(msg))
      }
    } catch {
      // Ignore malformed messages
    }
  })
  ws.addEventListener('close', () => {
    // Reconnect after a short delay so subscribers survive server restarts.
    setTimeout(() => {
      if (trackChangeCallbacks.size > 0 || playStateChangeCallbacks.size > 0) {
        ensurePushWs()
      }
    }, 2000)
  })
  _pushWs = ws
}

export function buildKampAPI(): KampAPI {
  return {
    panels: {
      register(manifest: PanelManifest): void {
        // Idempotent: skip duplicate registrations (e.g. React StrictMode re-runs).
        if (panelRegistry.some((p) => p.id === manifest.id)) return
        panelRegistry.push(manifest)
        // Notify all renderer-side subscribers via their contextBridge-proxied callbacks.
        registerCallbacks.forEach((cb) => cb(manifest))
      },
      getAll(): PanelManifest[] {
        return [...panelRegistry]
      },
      onRegister(callback): () => void {
        registerCallbacks.add(callback)
        return () => registerCallbacks.delete(callback)
      }
    },

    extensions: {
      getAll() {
        return ipcRenderer.invoke('kamp:get-extensions')
      },
      install(source: 'npm' | 'local', nameOrPath: string): Promise<ExtensionInstallResult> {
        return ipcRenderer.invoke('kamp:install-extension', source, nameOrPath)
      },
      uninstall(id: string): Promise<ExtensionInstallResult> {
        return ipcRenderer.invoke('kamp:uninstall-extension', id)
      }
    },

    player: {
      async getState(): Promise<PlayerState> {
        const res = await fetch(`${SERVER_URL}/api/v1/player/state`)
        if (!res.ok) throw new Error(`player.getState failed: ${res.status}`)
        return res.json() as Promise<PlayerState>
      },
      onTrackChange(callback: (state: PlayerState) => void): () => void {
        ensurePushWs()
        trackChangeCallbacks.add(callback)
        return () => trackChangeCallbacks.delete(callback)
      },
      onPlayStateChange(callback: (state: PlayerState) => void): () => void {
        ensurePushWs()
        playStateChangeCallbacks.add(callback)
        return () => playStateChangeCallbacks.delete(callback)
      }
    },

    library: {
      getAlbumArtUrl(albumArtist: string, album: string): string {
        return (
          `${SERVER_URL}/api/v1/album-art` +
          `?album_artist=${encodeURIComponent(albumArtist)}` +
          `&album=${encodeURIComponent(album)}`
        )
      }
    }
  }
}
