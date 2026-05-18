/**
 * StereoRackContext — types, context, and useStereoRack hook.
 *
 * Kept in a separate file from StereoRackModule so react-refresh can
 * fast-reload the module component without tripping over non-component exports.
 */
import { createContext, useContext } from 'react'
import type { MutableRefObject } from 'react'

export type TrackMeta = {
  artist: string
  title: string
  year: string
  format: string
  duration: number
}

export type WhimsyFlags = {
  coldBootDone: boolean
  /** True while the cold-boot calibration animation is running (~2.2s). */
  coldBoot: boolean
  konamiActive: boolean
  firstTrackOfDayShown: boolean
}

/** Called by the rAF loop each frame — child components register one of these. */
export type DrawFn = (levelDb: number, peakDb: number, timestamp: number) => void

export type StereoRackContextValue = {
  isPlaying: boolean
  isPaused: boolean
  trackMeta: TrackMeta | null
  whimsyFlags: WhimsyFlags
  setWhimsyFlags: (patch: Partial<WhimsyFlags>) => void
  /**
   * Mutable ref tracking the cold-boot animation start timestamp.
   * `startTs === null`  → not active
   * `startTs === -1`    → armed, waiting for first rAF tick to stamp real ts
   * `startTs >= 0`      → active; elapsed = current timestamp − startTs
   */
  coldBootRef: MutableRefObject<{ startTs: number | null }>
  /** Register a per-frame draw callback. Call from a useEffect on mount. */
  registerDraw: (id: string, fn: DrawFn) => void
  /** Unregister a draw callback. Call from the useEffect cleanup. */
  unregisterDraw: (id: string) => void
}

export const StereoRackContext = createContext<StereoRackContextValue | null>(null)

export function useStereoRack(): StereoRackContextValue {
  const ctx = useContext(StereoRackContext)
  if (!ctx) throw new Error('useStereoRack must be used inside StereoRackModule')
  return ctx
}
