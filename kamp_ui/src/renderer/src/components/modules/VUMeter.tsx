/**
 * VUMeter — 24-segment Dorrough-style horizontal bar with imperative draw.
 *
 * All per-frame mutations go through the draw() handle — React's render cycle
 * is never involved in segment toggling or decay calculation.
 *
 * Layout / zone coloring: KAMP-321 (CSS)
 * Imperative draw + decay: KAMP-322 (this file)
 * Peak hold:               KAMP-323 (extends VUMeterHandle.draw)
 */
import React, { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { useStereoRack } from './StereoRackContext'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NUM_SEGMENTS = 24

// Linear mapping: -60 dBFS → 0 segments, 0 dBFS → 24 segments.
// Calibration against real audio happens in KAMP-327 (integration task).
const DB_MIN = -60
const DB_RANGE = 60 // 0 - (-60)

// 18 dB/sec linear decay rate (per spec).
const DECAY_DB_PER_SEC = 18

// Pre-built index array — stable reference avoids re-creating on every render.
const SEGMENT_INDICES = Array.from({ length: NUM_SEGMENTS }, (_, i) => i)

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Imperative handle exposed to VUMeterPair via useImperativeHandle. */
export type VUMeterHandle = {
  /**
   * Called every rAF frame by VUMeterPair's registered draw callback.
   * levelDb: current loudness reading (LUFS ≈ dBFS), -Infinity when idle.
   * peakDb:  reserved for KAMP-323 peak hold; accepted but unused here.
   * timestamp: DOMHighResTimeStamp from requestAnimationFrame.
   */
  draw: (levelDb: number, peakDb: number, timestamp: number) => void
}

type VUMeterProps = {
  channel: 'L' | 'R'
}

// ---------------------------------------------------------------------------
// VUMeter
// ---------------------------------------------------------------------------

export const VUMeter = forwardRef<VUMeterHandle, VUMeterProps>(function VUMeter({ channel }, ref) {
  const { isPlaying } = useStereoRack()
  const isIdle = !isPlaying

  // Refs to DOM segment elements for direct class manipulation.
  const segmentRefs = useRef<(HTMLSpanElement | null)[]>(Array(NUM_SEGMENTS).fill(null))

  // Per-frame mutable state — never in React state.
  const displayLevelRef = useRef<number>(-Infinity)
  const lastTimestampRef = useRef<number>(0)

  useImperativeHandle(ref, () => ({
    draw(levelDb, _peakDb, timestamp) {
      // --- Decay / snap-up ---
      const lastTs = lastTimestampRef.current
      // Skip decay on the very first frame to avoid a huge initial delta.
      const delta = lastTs === 0 ? 0 : (timestamp - lastTs) / 1000
      lastTimestampRef.current = timestamp

      const prev = displayLevelRef.current
      const decayed = prev - DECAY_DB_PER_SEC * delta
      // Snap up instantly when signal rises; decay linearly when it falls.
      displayLevelRef.current = levelDb > decayed ? levelDb : Math.max(levelDb, decayed)

      // --- Map dB → segment count ---
      const count = Math.max(
        0,
        Math.min(
          NUM_SEGMENTS,
          Math.round(((displayLevelRef.current - DB_MIN) / DB_RANGE) * NUM_SEGMENTS)
        )
      )

      // --- Toggle .active directly on DOM elements (no React state) ---
      const segs = segmentRefs.current
      for (let i = 0; i < NUM_SEGMENTS; i++) {
        const el = segs[i]
        if (!el) continue
        if (i < count) {
          el.classList.add('active')
        } else {
          el.classList.remove('active')
        }
      }
    }
  }))

  return (
    <div className={`vu-meter vu-meter--${channel.toLowerCase()}${isIdle ? ' is-idle' : ''}`}>
      <div className="vu-bar">
        {SEGMENT_INDICES.map((i) => (
          <span
            key={i}
            className="vu-segment"
            ref={(el) => {
              segmentRefs.current[i] = el
            }}
          />
        ))}
        {/* Peak hold floats over the bar; driven imperatively in KAMP-323 */}
        <div className="vu-peak-hold" />
      </div>
      <span className="vu-label">{channel}</span>
    </div>
  )
})

// ---------------------------------------------------------------------------
// VUMeterPair
// ---------------------------------------------------------------------------

/**
 * Renders L and R meters with an optional slot between them for the Oscilloscope
 * (passed as children, wired in KAMP-324).
 *
 * Registers a single 'vu-meters' draw callback with StereoRackModule's rAF
 * loop via context. The callback forwards each frame to both meter handles.
 */
export function VUMeterPair({ children }: { children?: React.ReactNode }): React.JSX.Element {
  const lRef = useRef<VUMeterHandle>(null)
  const rRef = useRef<VUMeterHandle>(null)
  const { registerDraw, unregisterDraw } = useStereoRack()

  useEffect(() => {
    // The rAF loop passes leftDb as the first arg and rightDb as the second.
    // Route each to the correct meter so L and R show their own channels.
    registerDraw('vu-meters', (leftDb, rightDb, timestamp) => {
      lRef.current?.draw(leftDb, rightDb, timestamp)
      rRef.current?.draw(rightDb, leftDb, timestamp)
    })
    return () => unregisterDraw('vu-meters')
  }, [registerDraw, unregisterDraw])

  return (
    <>
      <VUMeter ref={lRef} channel="L" />
      {children}
      <VUMeter ref={rRef} channel="R" />
    </>
  )
}
