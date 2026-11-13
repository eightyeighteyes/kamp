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

// KAMP-767: Canvas 2D backgrounds register here too, so the hooks that only make
// sense for one kind are optional. `lose`/`restore` exist only for WebGL; `blur`
// and `forcePop` only for the particle backgrounds (Sparks, Wintermute). A
// missing hook warns instead of throwing.
export interface GLDevTarget {
  lose?(): void
  restore?(): void
  /**
   * Measurement knobs: backing-store scale (fraction of DPR), frame cap, and for
   * the particle backgrounds the baked sprite blur in CSS px (0 = the no-blur
   * variant; Wintermute reads it as the near layer's blur and scales the others).
   * `layers` (Wintermute) draws only the first N depth layers, far first, so
   * `layers: 2` is the near-layer-off variant. `mode` (Castalia) rings the plate
   * onto that entry of its mode table, to review the figures one by one. `plate`
   * (Castalia) isolates the backdrop's cost: 'solid' is the flat --bg, 'static'
   * paints the gradient once and never again, 'live' is the shipped breathing one.
   * `texture` (Chapel, KAMP-781) turns the fields' texture off: the effect-off
   * variant for measuring its cost.
   */
  tune(opts: {
    scale?: number
    fps?: number
    blur?: number
    layers?: number
    mode?: number
    plate?: 'solid' | 'static' | 'live'
    texture?: 'on' | 'off'
  }): void
  /** Draw rate since the previous call (verifies the frame cap), and backing size. */
  stats(): { fps: number; backing: string }
  /**
   * Launch the rare moment now, for tuning: Sparks' high spark, Wintermute's close
   * flake, Castalia's shimmer.
   */
  forcePop?(): void
}

interface KampGLHooks {
  lose(): void
  restore(): void
  tune: GLDevTarget['tune']
  forcePop(): void
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
  console.warn('[KAMP-780] __kampGL: no tunable background is mounted')
  return undefined
}

/** Call an optional hook, or say the mounted background doesn't have it. */
function optional(t: GLDevTarget, name: 'lose' | 'restore' | 'forcePop'): void {
  const fn = t[name]
  if (fn) fn.call(t)
  else console.warn(`[KAMP-767] __kampGL.${name}: not supported by this background`)
}

function install(): () => void {
  const hooks: KampGLHooks = {
    lose: () => withTarget((t) => optional(t, 'lose')),
    restore: () => withTarget((t) => optional(t, 'restore')),
    cycle: (ms = 500) =>
      withTarget((t) => {
        if (!t.lose || !t.restore) return optional(t, 'lose')
        t.lose()
        window.setTimeout(() => optional(t, 'restore'), ms)
      }),
    forcePop: () => withTarget((t) => optional(t, 'forcePop')),
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
