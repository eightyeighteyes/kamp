/**
 * Oscilloscope — scrolling waveform history canvas for StereoRackModule.
 *
 * Maintains a Float32Array ring buffer (one sample per logical pixel column).
 * Each rAF frame a new synthesized sample is pushed at the right edge and
 * the history shifts left, building up a scrolling waveform. Amplitude is
 * driven by levelDb; a seeded phase rate gives each track a distinct feel.
 * On pause the amplitude decays toward zero over 800ms so the trace sinks
 * to the zero-line.
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

const PAUSE_DECAY_MS = 800
const PAUSE_DECAY_TARGET = -120

// Base phase advance per frame — ~1 oscillation per second at 60fps.
const PHASE_RATE_BASE = (2 * Math.PI) / 60

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hashString(s: string): number {
  let h = 5381
  for (let i = 0; i < s.length; i++) {
    h = (((h << 5) + h) ^ s.charCodeAt(i)) >>> 0
  }
  return h
}

function resizeBuffer(prev: Float32Array | null, newW: number): Float32Array {
  const buf = new Float32Array(newW)
  if (prev && prev.length > 0) {
    const copyLen = Math.min(prev.length, newW)
    buf.set(prev.subarray(prev.length - copyLen), newW - copyLen)
  }
  return buf
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

  // Scrolling history: one amplitude sample per logical pixel column.
  const bufferRef = useRef<Float32Array | null>(null)
  // Phase accumulator for synthesized oscillation.
  const phaseRef = useRef<number>(0)

  // Pause decay
  const isPausedRef = useRef<boolean>(false)
  const pauseStartTsRef = useRef<number>(-1)
  const levelAtPauseRef = useRef<number>(DB_MIN)

  const seedRef = useRef<number>(0)

  useEffect(() => {
    if (isPaused) pauseStartTsRef.current = -1
    isPausedRef.current = isPaused
  }, [isPaused])

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
      // Use integer logical dimensions so typed-array indices are always integers.
      const w = Math.floor(rect.width)
      const h = Math.floor(rect.height)
      sizeRef.current = { w, h }
      // Resize ring buffer, preserving as much history as possible.
      if (!bufferRef.current || bufferRef.current.length !== w) {
        bufferRef.current = resizeBuffer(bufferRef.current, w)
      }
    }

    setup()

    // Read accent color via the CSS `color` property set on .oscilloscope.
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

      // Ensure the buffer matches the current canvas width.
      if (!bufferRef.current || bufferRef.current.length !== w) {
        bufferRef.current = resizeBuffer(bufferRef.current, w)
      }
      const buf = bufferRef.current

      // --- Effective level ---
      let levelDb: number
      if (isPausedRef.current) {
        if (pauseStartTsRef.current < 0) {
          pauseStartTsRef.current = timestamp
          levelAtPauseRef.current = lastLiveLevel
        }
        const elapsed = timestamp - pauseStartTsRef.current
        const t = Math.min(elapsed / PAUSE_DECAY_MS, 1)
        levelDb = levelAtPauseRef.current + (PAUSE_DECAY_TARGET - levelAtPauseRef.current) * t
      } else {
        levelDb = Math.max(leftDb, rightDb)
        lastLiveLevel = levelDb
      }

      // --- Map dBFS → amplitude (pixels) ---
      const maxAmp = h / 2 - 4
      const amp = Math.max(0, Math.min(maxAmp, ((levelDb - DB_MIN) / DB_RANGE) * maxAmp))

      // --- Synthesize one new sample and push onto the right edge ---
      const seed = seedRef.current
      // Per-track oscillation rate: base ± 20% for variety.
      const phaseRate = PHASE_RATE_BASE * (0.8 + ((seed & 0xf) / 0xf) * 0.4)
      // Seeded harmonic phase offsets.
      const p0 = ((seed & 0xff) / 255) * Math.PI * 2
      const p1 = (((seed >> 8) & 0xff) / 255) * Math.PI * 2
      const phase = phaseRef.current
      const sample =
        (0.6 * Math.sin(phase) + 0.3 * Math.sin(2 * phase + p0) + 0.1 * Math.sin(3 * phase + p1)) *
        amp

      // Shift history left by one pixel, append new sample at the right.
      buf.copyWithin(0, 1)
      buf[w - 1] = sample
      phaseRef.current = phase + phaseRate

      // --- Draw ---
      ctx.clearRect(0, 0, w, h)

      // Zero-line
      ctx.save()
      ctx.strokeStyle = 'rgba(255,255,255,0.08)'
      ctx.lineWidth = 1
      ctx.setLineDash([4, 6])
      ctx.beginPath()
      ctx.moveTo(0, h / 2)
      ctx.lineTo(w, h / 2)
      ctx.stroke()
      ctx.restore()

      // Waveform history polyline
      ctx.save()
      ctx.strokeStyle = accentRef.current
      ctx.lineWidth = 1.5
      ctx.setLineDash([])
      ctx.beginPath()
      const midY = h / 2
      for (let x = 0; x < w; x++) {
        const py = midY - buf[x]
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
