/**
 * Builds the window.KampAPI object that is exposed to the renderer via contextBridge.
 *
 * This module runs in the preload context (has Node.js / Electron APIs) but the
 * returned object contains only plain values and functions — no Node.js objects
 * leak into the renderer.
 */

import { ipcRenderer } from 'electron'
import type { KampAPI, PanelManifest, ExtensionInstallResult } from '../shared/kampAPI'

const panelRegistry: PanelManifest[] = []
// Callbacks registered by the renderer via panels.onRegister().
// contextBridge wraps renderer functions so they can be called from the preload.
const registerCallbacks = new Set<(manifest: PanelManifest) => void>()

export function buildKampAPI(): KampAPI {
  return {
    serverUrl: 'http://127.0.0.1:8000',

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
    }
  }
}
