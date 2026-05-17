/**
 * Oscilloscope — animated canvas waveform for StereoRackModule.
 *
 * Driven imperatively by the StereoRackContext rAF loop. Renders a
 * parametric waveform (3 sine waves) whose amplitude tracks the louder
 * of the two audio channels. Per-track phase offsets give each track a
 * consistent, recognizable shape. On pause the waveform decays toward
 * the zero-line over 800ms.
 *
 * Canvas is HiDPI-aware (DPR scaling) and adapts via ResizeObserver.
 */
import React, { useEffect, useRef } from 'react'
import { useStereoRack } from './StereoRackContext'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DB_MIN = -60
const DB_RANGE = 60

// Exponential approach to silence after pause.
const PAUSE_DECAY_MS = 800
const PAUSE_DECAY_TARGET = -120

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// djb2-variant hash → stable uint32 seed per track identity.
function hashString(s: string): number {
  let h = 5381
  for (let i = 0; i < s.length; i++) {
    h = (((h << 5) + h) ^ s.charCodeAt(i)) >>> 0
  }
  return h
}

// ---------------------------------------------------------------------------
// Oscilloscope
// ---------------------------------------------------------------------------

export function Oscilloscope(): React.JSX.Element {
  const { registerDraw, unregisterDraw, isPaused, trackMeta } = useStereoRack()

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const ctxRef = useRef<CanvasRenderingContext2D | null>(null)
  const sizeRef = useRef({ w: 0, h: 0 })
  const accentRef = useRef<string>('rgba(255,255,255,0.85)')

  // Pause decay — pauseStartTsRef < 0 means "not yet initialized this pause".
  const isPausedRef = useRef<boolean>(false)
  const pauseStartTsRef = useRef<number>(-1)
  const levelAtPauseRef = useRef<number>(DB_MIN)

  // Seed for per-track waveform character.
  const seedRef = useRef<number>(0)

  // Sync isPaused → ref; arm the decay on the next draw frame.
  useEffect(() => {
    if (isPaused) pauseStartTsRef.current = -1
    isPausedRef.current = isPaused
  }, [isPaused])

  // Recompute seed when track identity changes.
  useEffect(() => {
    seedRef.current = trackMeta ? hashString(trackMeta.artist + trackMeta.title) : 0
  }, [trackMeta])

  // HiDPI canvas setup + ResizeObserver.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const setup = (): void => {
      const dpr = window.devicePixelRatio || 1
      const rect = canvas.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) return
      canvas.width = Math.round(rect.width * dpr)
      canvas.height = Math.round(rect.height * dpr)
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.scale(dpr, dpr)
      ctxRef.current = ctx
      sizeRef.current = { w: rect.width, h: rect.height }
    }

    setup()

    // Read accent color via the `color` CSS property set on .oscilloscope.
    // canvas.getContext calls above may reset ctx, so read style after setup.
    const raw = getComputedStyle(canvas).color
    const m = raw.match(/\d+/g)
    if (m && m.length >= 3) {
      accentRef.current = `rgba(${m[0]},${m[1]},${m[2]},0.85)`
    }

    const ro = new ResizeObserver(setup)
    ro.observe(canvas)
    return () => ro.disconnect()
  }, [])

  // Register the per-frame draw callback with the rAF loop.
  useEffect(() => {
    let lastLiveLevel = DB_MIN

    registerDraw('oscilloscope', (leftDb, rightDb, timestamp) => {
      const ctx = ctxRef.current
      if (!ctx) return
      const { w, h } = sizeRef.current
      if (w === 0 || h === 0) return

      // --- Effective level ---
      let levelDb: number
      if (isPausedRef.current) {
        // Initialize decay start on the first paused frame.
        if (pauseStartTsRef.current < 0) {
          pauseStartTsRef.current = timestamp
          levelAtPauseRef.current = lastLiveLevel
        }
        const elapsed = timestamp - pauseStartTsRef.current
        const t = Math.min(elapsed / PAUSE_DECAY_MS, 1)
        levelDb = levelAtPauseRef.current + (PAUSE_DECAY_TARGET - levelAtPauseRef.current) * t
      } else {
        // Use the louder channel so mono content still drives the display.
        levelDb = Math.max(leftDb, rightDb)
        lastLiveLevel = levelDb
      }

      // --- Map dBFS → amplitude (pixels) ---
      const maxAmp = h / 2 - 4
      const amp = Math.max(0, Math.min(maxAmp, ((levelDb - DB_MIN) / DB_RANGE) * maxAmp))

      // --- Clear ---
      ctx.clearRect(0, 0, w, h)

      // --- Zero-line ---
      ctx.save()
      ctx.strokeStyle = 'rgba(255,255,255,0.08)'
      ctx.lineWidth = 1
      ctx.setLineDash([4, 6])
      ctx.beginPath()
      ctx.moveTo(0, h / 2)
      ctx.lineTo(w, h / 2)
      ctx.stroke()
      ctx.restore()

      // --- Parametric waveform (3 sine waves, seed-stable per track) ---
      const seed = seedRef.current
      const now = performance.now() * 0.001

      // Frequencies: base ± small seed-driven offset for per-track character.
      const f0 = 0.8 + ((seed & 0xf) / 0xf) * 0.4 // 0.80–1.20 Hz
      const f1 = 1.7 + (((seed >> 4) & 0xf) / 0xf) * 0.6 // 1.70–2.30 Hz
      const f2 = 3.1 + (((seed >> 8) & 0xf) / 0xf) * 0.4 // 3.10–3.50 Hz

      // Phase offsets seeded per track so each track has a distinct shape.
      const p0 = ((seed & 0xff) / 255) * Math.PI * 2
      const p1 = (((seed >> 8) & 0xff) / 255) * Math.PI * 2
      const p2 = (((seed >> 16) & 0xff) / 255) * Math.PI * 2

      ctx.save()
      ctx.strokeStyle = accentRef.current
      ctx.lineWidth = 1.5
      ctx.setLineDash([])
      ctx.beginPath()

      const midY = h / 2
      for (let x = 0; x <= w; x++) {
        const xf = x / w
        const y =
          0.5 * Math.sin(2 * Math.PI * f0 * xf + p0 + now * f0) +
          0.3 * Math.sin(2 * Math.PI * f1 * xf + p1 + now * f1) +
          0.2 * Math.sin(2 * Math.PI * f2 * xf + p2 + now * f2)
        const py = midY - y * amp
        if (x === 0) ctx.moveTo(x, py)
        else ctx.lineTo(x, py)
      }

      ctx.stroke()
      ctx.restore()
    })

    return () => unregisterDraw('oscilloscope')
  }, [registerDraw, unregisterDraw])

  return <canvas ref={canvasRef} className="oscilloscope" />
}
