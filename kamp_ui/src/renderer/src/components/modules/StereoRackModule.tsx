/**
 * StereoRackModule — shell for the KAMP-318 visualizer.
 *
 * Owns:
 *  - StereoRackContext provider (discrete playback state + imperative draw registry)
 *  - A single rAF loop that reads levelDb/peakDb from the Zustand store via
 *    getState() and calls every registered draw function each frame.
 *  - visibilitychange wiring so the loop pauses when the tab is hidden.
 *  - Cold boot calibration sequence (KAMP-328): on the first play event after
 *    app launch, overrides levelDb with a 0→24→0 segment sweep for 800ms and
 *    signals child components to run their whimsy animations.
 *  - Dead air detection (KAMP-330): tracks pause idle duration per rAF frame;
 *    signals isDeadAir when no track is loaded or paused for >60s.
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
import { Oscilloscope } from './Oscilloscope'
import { TrackDisplay } from './TrackDisplay'

// VU sweep duration (ms): 0 → 24 → 0 segments via sin arc.
const COLD_BOOT_VU_MS = 800
// Total cold boot duration (ms): VU/oscilloscope end at 800ms, TrackDisplay
// INIT display ends at ~2100ms; 2200ms gives a 100ms settling buffer.
const COLD_BOOT_END_MS = 2200
const DB_MIN = -60
const DB_RANGE = 60

// Dead air activates after this many milliseconds of continuous pause.
const DEAD_AIR_IDLE_MS = 60_000

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

  const rafIdRef = useRef<number>(0)

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
    coldBoot: false,
    konamiActive: false,
    firstTrackOfDayShown: false
  })

  const setWhimsyFlags = useCallback((patch: Partial<WhimsyFlags>) => {
    setWhimsyFlagsState((prev) => ({ ...prev, ...patch }))
  }, [])

  // Cold boot calibration sequence (KAMP-328).
  // startTs === null   → not active
  // startTs === -1     → armed; first rAF tick stamps the real timestamp
  // startTs >= 0       → active; elapsed = timestamp − startTs
  const coldBootRef = useRef<{ startTs: number | null }>({ startTs: null })

  // Stable ref to setWhimsyFlags — lets the rAF closure call it without
  // being a dependency (avoids re-creating the loop on each render).
  const setWhimsyFlagsRef = useRef(setWhimsyFlags)
  useEffect(() => {
    setWhimsyFlagsRef.current = setWhimsyFlags
  }, [setWhimsyFlags])

  // Dead air state (KAMP-330): no track loaded OR paused for >60s.
  const [isDeadAir, setIsDeadAir] = useState(false)
  const deadAirRef = useRef(false)
  const setDeadAirRef = useRef(setIsDeadAir)
  useEffect(() => {
    setDeadAirRef.current = setIsDeadAir
  }, [])

  // rAF loop — reads store state directly each frame (no subscription needed;
  // getState() is synchronous and always returns the latest snapshot).
  useEffect(() => {
    // Pause idle tracking — local to this closure, no React state involved.
    let pauseStartTs = -1
    let idleElapsedMs = 0

    const frame = (timestamp: number): void => {
      // Stamp the real start timestamp on the first frame after arming.
      if (coldBootRef.current.startTs === -1) {
        coldBootRef.current = { startTs: timestamp }
      }

      const { leftDb, rightDb, player } = useStore.getState()
      let level = leftDb ?? -Infinity
      let peak = rightDb ?? -Infinity

      // --- Dead air idle tracking ---
      const paused = !player.playing && player.current_track !== null
      const noTrack = player.current_track === null
      if (paused) {
        if (pauseStartTs < 0) pauseStartTs = timestamp
        idleElapsedMs = timestamp - pauseStartTs
      } else {
        pauseStartTs = -1
        idleElapsedMs = 0
      }
      const shouldBeDeadAir = noTrack || idleElapsedMs > DEAD_AIR_IDLE_MS
      if (shouldBeDeadAir !== deadAirRef.current) {
        deadAirRef.current = shouldBeDeadAir
        setDeadAirRef.current(shouldBeDeadAir)
      }

      const cbStart = coldBootRef.current.startTs
      if (cbStart !== null) {
        const elapsed = timestamp - cbStart
        if (elapsed < COLD_BOOT_VU_MS) {
          // Sin arc maps [0, 800ms] → segment count [0, 24, 0].
          // Reverse-mapped to dBFS so VU draw fn produces the correct count.
          const t = elapsed / COLD_BOOT_VU_MS
          const sweepLevel = DB_MIN + Math.sin(t * Math.PI) * DB_RANGE
          level = sweepLevel
          peak = sweepLevel
        } else if (elapsed >= COLD_BOOT_END_MS) {
          coldBootRef.current = { startTs: null }
          setWhimsyFlagsRef.current({ coldBoot: false, coldBootDone: true })
        }
        // Between COLD_BOOT_VU_MS and COLD_BOOT_END_MS: real audio resumes for
        // VU/oscilloscope while TrackDisplay holds the INIT stamp.
      }

      drawsRef.current.forEach((draw) => draw(level, peak, timestamp))
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

  // Arm the cold boot sequence on the first play event after app launch.
  // Subscribes to the store directly (not via isPlaying) so setState is called
  // from a subscription callback rather than synchronously in the effect body.
  // localStorage gate ensures it fires once per app lifetime, not per session.
  useEffect(() => {
    let triggered = false
    const unsub = useStore.subscribe((state) => {
      if (triggered || !state.player.playing) return
      if (localStorage.getItem('stereo-rack:coldBootSeen')) return
      triggered = true
      localStorage.setItem('stereo-rack:coldBootSeen', '1')
      coldBootRef.current = { startTs: -1 }
      setWhimsyFlags({ coldBoot: true })
    })
    return unsub
  }, [setWhimsyFlags])

  const contextValue = useMemo<StereoRackContextValue>(
    () => ({
      isPlaying,
      isPaused,
      trackMeta,
      whimsyFlags,
      setWhimsyFlags,
      coldBootRef,
      isDeadAir,
      deadAirRef,
      registerDraw,
      unregisterDraw
    }),
    [
      isPlaying,
      isPaused,
      trackMeta,
      whimsyFlags,
      setWhimsyFlags,
      coldBootRef,
      isDeadAir,
      deadAirRef,
      registerDraw,
      unregisterDraw
    ]
  )

  return (
    <StereoRackContext.Provider value={contextValue}>
      <div className="stereo-rack-module">
        <div className="stereo-rack-top">
          <VUMeterPair>
            <Oscilloscope />
          </VUMeterPair>
        </div>
        <TrackDisplay />
      </div>
    </StereoRackContext.Provider>
  )
}
