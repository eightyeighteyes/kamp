// KAMP-780: `window.__kampGL`, the DevTools handle for exercising and measuring
// shader backgrounds. Installed only when the app runs with KAMP_DIAGNOSTICS set.
//
// Gated on the diagnostics IPC, NOT import.meta.env.DEV: `npm start` is
// `electron-vite preview`, a production build, and that is the build the
// powermetrics runs measure (KAMP-776 lesson).
//
// The global is installed once, at app level, and delegates to whichever engine
// is currently registered. A per-engine global would let an overlapping mount and
// unmount (fallback swap, StrictMode) delete a newer engine's hook or leave one
// pointing at a destroyed engine.

import { useEffect } from 'react'
import { useStore } from '../../../store'

export interface GLDevTarget {
  lose(): void
  restore(): void
  /** Measurement knobs: backing-store scale (fraction of DPR) and frame cap. */
  tune(opts: { scale?: number; fps?: number }): void
  /** Draw rate since the previous call (verifies the frame cap), and backing size. */
  stats(): { fps: number; backing: string }
}

interface KampGLHooks extends Omit<GLDevTarget, 'stats'> {
  stats(): ReturnType<GLDevTarget['stats']> | undefined
  /** Lose, then restore after `ms` — inside the 2 s fallback timeout by default. */
  cycle(ms?: number): void
  /** Show the flat reference background (not persisted). */
  reference(): void
  /** Return to the user's chosen animation. */
  clear(): void
}

declare global {
  interface Window {
    __kampGL?: KampGLHooks
  }
}

let current: GLDevTarget | null = null

/**
 * Make `target` the engine __kampGL drives. Returns an unregister function that
 * only clears the slot if `target` still owns it, so a late unmount can't
 * unregister the engine that replaced it.
 */
export function registerGLTarget(target: GLDevTarget): () => void {
  current = target
  return () => {
    if (current === target) current = null
  }
}

function withTarget<T>(fn: (t: GLDevTarget) => T): T | undefined {
  if (current) return fn(current)
  console.warn('[KAMP-780] __kampGL: no WebGL background is mounted')
  return undefined
}

function install(): () => void {
  const hooks: KampGLHooks = {
    lose: () => withTarget((t) => t.lose()),
    restore: () => withTarget((t) => t.restore()),
    cycle: (ms = 500) =>
      withTarget((t) => {
        t.lose()
        window.setTimeout(() => t.restore(), ms)
      }),
    tune: (opts) => withTarget((t) => t.tune(opts)),
    stats: () => withTarget((t) => t.stats()),
    reference: () => useStore.getState().setNowPlayingOverride('webgl-reference'),
    clear: () => useStore.getState().setNowPlayingOverride(null)
  }
  window.__kampGL = hooks
  return () => {
    if (window.__kampGL === hooks) delete window.__kampGL
  }
}

/** Install window.__kampGL for the app's lifetime, if diagnostics are enabled. */
export function useGLDevHooks(): void {
  useEffect(() => {
    let uninstall: (() => void) | null = null
    let cancelled = false
    window.api
      ?.diagnosticsEnabled?.()
      .then((enabled) => {
        if (enabled && !cancelled) uninstall = install()
      })
      .catch(() => {})
    return () => {
      cancelled = true
      uninstall?.()
    }
  }, [])
}
