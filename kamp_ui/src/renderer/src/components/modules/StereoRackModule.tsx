/**
 * StereoRackModule — shell for the KAMP-318 visualizer.
 *
 * Owns:
 *  - StereoRackContext provider (discrete playback state + imperative draw registry)
 *  - A single rAF loop that reads levelDb/peakDb from the Zustand store via
 *    getState() and calls every registered draw function each frame.
 *  - visibilitychange wiring so the loop pauses when the tab is hidden.
 *
 * Per-frame mutable state (level, peak, decay accumulators, whimsy timers)
 * lives in the child components' useRefs — this shell intentionally holds none.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../../store'
import type { ModuleProps } from './registry'
import {
  StereoRackContext,
  type StereoRackContextValue,
  type TrackMeta,
  type WhimsyFlags,
  type DrawFn
} from './StereoRackContext'
import { VUMeterPair } from './VUMeter'

// displayStyle is required by ModuleProps but unused — StereoRack has a fixed layout.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function StereoRackModule({ displayStyle: _ds }: ModuleProps): React.JSX.Element {
  // Draw registry — child components register imperative draw callbacks here.
  // Map keyed by a stable id string supplied by the child.
  const drawsRef = useRef<Map<string, DrawFn>>(new Map())

  const registerDraw = useCallback((id: string, fn: DrawFn) => {
    drawsRef.current.set(id, fn)
  }, [])

  const unregisterDraw = useCallback((id: string) => {
    drawsRef.current.delete(id)
  }, [])

  // rAF loop — reads store state directly each frame (no subscription needed;
  // getState() is synchronous and always returns the latest snapshot).
  const rafIdRef = useRef<number>(0)

  useEffect(() => {
    const frame = (): void => {
      const { levelDb, peakDb } = useStore.getState()
      const level = levelDb ?? -Infinity
      const peak = peakDb ?? -Infinity
      drawsRef.current.forEach((draw) => draw(level, peak))
      rafIdRef.current = requestAnimationFrame(frame)
    }

    rafIdRef.current = requestAnimationFrame(frame)

    const onVisibilityChange = (): void => {
      if (document.hidden) {
        cancelAnimationFrame(rafIdRef.current)
      } else {
        rafIdRef.current = requestAnimationFrame(frame)
      }
    }

    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      cancelAnimationFrame(rafIdRef.current)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [])

  // Discrete playback state — changes are infrequent so React state is fine here.
  const player = useStore((s) => s.player)
  const isPlaying = player.playing
  const isPaused = !player.playing && player.current_track !== null

  const trackMeta = useMemo((): TrackMeta | null => {
    const t = player.current_track
    if (!t) return null
    return {
      artist: t.artist || t.album_artist,
      title: t.title,
      year: t.year,
      format: t.ext.replace(/^\./, '').toUpperCase(),
      duration: player.duration
    }
  }, [player.current_track, player.duration])

  // Whimsy flags — updated discretely by delight-layer tasks (KAMP-321+).
  const [whimsyFlags, setWhimsyFlagsState] = useState<WhimsyFlags>({
    coldBootDone: false,
    konamiActive: false,
    firstTrackOfDayShown: false
  })

  const setWhimsyFlags = useCallback((patch: Partial<WhimsyFlags>) => {
    setWhimsyFlagsState((prev) => ({ ...prev, ...patch }))
  }, [])

  const contextValue = useMemo<StereoRackContextValue>(
    () => ({
      isPlaying,
      isPaused,
      trackMeta,
      whimsyFlags,
      setWhimsyFlags,
      registerDraw,
      unregisterDraw
    }),
    [isPlaying, isPaused, trackMeta, whimsyFlags, setWhimsyFlags, registerDraw, unregisterDraw]
  )

  return (
    <StereoRackContext.Provider value={contextValue}>
      <div className="stereo-rack-module">
        <div className="stereo-rack-top">
          <VUMeterPair />
          {/* Oscilloscope mounts between the meters in KAMP-324 */}
        </div>
        {/* TrackDisplay mounts here in KAMP-325 */}
      </div>
    </StereoRackContext.Provider>
  )
}
