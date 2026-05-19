/**
 * Oscilloscope — standing-wave canvas for StereoRackModule.
 *
 * Each pixel in the buffer evolves independently via an AR(1) (autoregressive)
 * temporal update every frame. Spatial smoothing turns the raw per-pixel noise
 * into smooth wave crests — but only above an amplitude threshold, so silence
 * retains the fine-grained noise texture rather than collapsing to a flat line.
 *
 * Signal character comes from real measurements:
 *   Amplitude  → RMS dBFS (perceptual power-curve mapping)
 *   Tightness  → Crest factor (percussive = loose/spiky; sustained = smooth)
 *   Smoothing  → Amplitude-adaptive (quiet = 2 passes; loud = 1 pass; noise floor = 0)
 *
 * Phosphor glow: the path is stroked twice — a wide dim outer bloom followed by
 * a narrow bright inner core — to mimic a CRT phosphor trace.
 *
 * Canvas is HiDPI-aware (DPR scaling) and adapts via ResizeObserver.
 */
import React, { useEffect, useRef } from 'react'
import { useStore } from '../../store'
import { useStereoRack } from './StereoRackContext'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Perceptual amplitude mapping.
const DB_FLOOR = -40.0
const DB_CEIL = -3.0
const AMP_GAMMA = 2.0

const PAUSE_DECAY_MS = 800
const PAUSE_DECAY_TARGET = -120

// Duration of the cold boot VU sweep — oscilloscope shows a clean sine here.
const COLD_BOOT_VU_MS = 800

// Cycles per pixel for the cold boot sine — 4 full cycles across 240px.
const COLD_BOOT_WAVELENGTH_PX = 60

// Per-frame AR(1) tightness driven by crest factor.
// At 60fps: 0.984^60 ≈ 0.38 (memory half-life ~1.6s, compressed/sustained)
//           0.970^60 ≈ 0.16 (memory half-life ~0.75s, percussive/dynamic)
const TIGHT_MIN_FRAME = 0.97
const TIGHT_MAX_FRAME = 0.984

// Crest factor reference points for the tightness mapping.
const CREST_LOW = 8.0
const CREST_HIGH = 30.0
const DEFAULT_CREST = 14.0

// Innovation scale: per-pixel step = (rand-0.5)*2 * effectiveAmp * INNOVATION_SCALE
const INNOVATION_SCALE = 0.15

// Minimum noise floor (pixels). Ensures the trace never fully flattens at
// silence — preserves the CRT noise texture during dead air and pause.
const NOISE_FLOOR_AMP = 1.5

// Amplitude-adaptive smoothing thresholds.
// Below SMOOTH_THRESHOLD: 0 passes (raw noise floor texture)
// SMOOTH_THRESHOLD..55% of maxAmp: 2 passes (smooth waveform)
// Above 55% of maxAmp: 1 pass (retains texture for "loud and noisy" feel)
const SMOOTH_THRESHOLD = 3.0
const SMOOTH_PASSES = 2

// Phosphor glow: two strokes on the same path.
// Parameters differ by trace style — the hot path reads these at draw time.
const TRACE_LINE_WIDTH = 1.5
const TRACE_ALPHA = 0.88

type TraceParams = { glowWidth: number; glowAlpha: number }
const STYLE_PARAMS: Record<string, TraceParams> = {
  clean: { glowWidth: 0, glowAlpha: 0 }, // no bloom
  glowy: { glowWidth: 4, glowAlpha: 0.15 }, // default — wide dim bloom
  trippy: { glowWidth: 12, glowAlpha: 0.08 } // very wide dim bloom + echoes
}

// Trippy echo ring buffer — snapshots of y-positions saved every N frames.
const TRIPPY_SNAPSHOT_FRAMES = 4 // one snapshot per 4 frames ≈ 15fps at 60fps
const TRIPPY_MAX_SNAPSHOTS = 12 // ~800ms of history at 15fps

// Peak follower decay per frame at 60fps.
// 0.92^60 ≈ 0.007 — visible rhythmic pulse: 73% at 100ms, ~20% at 500ms.
const PEAK_DECAY = 0.92

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function resizeBuffers(
  prevBuf: Float32Array | null,
  prevSmooth: Float32Array | null,
  newW: number
): [Float32Array, Float32Array] {
  const buf = new Float32Array(newW)
  if (prevBuf && prevBuf.length > 0) {
    const copyLen = Math.min(prevBuf.length, newW)
    buf.set(prevBuf.subarray(prevBuf.length - copyLen), newW - copyLen)
  }
  // Reuse the scratch buffer if it already has the right length.
  const smooth = prevSmooth?.length === newW ? prevSmooth : new Float32Array(newW)
  return [buf, smooth]
}

// ---------------------------------------------------------------------------
// Oscilloscope
// ---------------------------------------------------------------------------

export function Oscilloscope(): React.JSX.Element {
  const { registerDraw, unregisterDraw, isPaused, coldBootRef, deadAirRef } = useStereoRack()

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const ctxRef = useRef<CanvasRenderingContext2D | null>(null)
  const sizeRef = useRef({ w: 0, h: 0 })
  // Kept for any consumers that read accentRef directly (e.g., CSS compat).
  const accentRef = useRef<string>('rgba(255,255,255,0.85)')
  // Parsed RGB components for hot-path use (avoids per-frame string build from CSS).
  const accentRgbRef = useRef({ r: 255, g: 255, b: 255 })

  // Standing-wave buffer: one normalized value [-1, 1] per logical pixel column.
  // Amplitude is applied at draw time (buf[x] * displayAmp) so a transient makes
  // the entire waveform scale up in a single frame rather than building over many.
  const bufferRef = useRef<Float32Array | null>(null)
  // Scratch buffer for spatial smoothing — avoids directional bias from in-place updates.
  const smoothBufRef = useRef<Float32Array | null>(null)

  // Per-frame tightness [TIGHT_MIN_FRAME, TIGHT_MAX_FRAME], updated from crest factor.
  const tightnessRef = useRef<number>(0.978)
  // Peak follower: instant attack, frame-by-frame decay. Captures transients that
  // RMS averaging would dilute (e.g. a snare hit in a 50ms window).
  const peakEnvRef = useRef<number>(0)

  // Pause decay
  const isPausedRef = useRef<boolean>(false)
  const pauseStartTsRef = useRef<number>(-1)
  const levelAtPauseRef = useRef<number>(DB_FLOOR)

  // Trippy echo ring buffer — y-position snapshots saved every N frames.
  const echoSnapshotsRef = useRef<Float32Array[]>([])
  const echoTimestampsRef = useRef<number[]>([])
  const echoFrameCountRef = useRef<number>(0)

  useEffect(() => {
    if (isPaused) pauseStartTsRef.current = -1
    isPausedRef.current = isPaused
  }, [isPaused])

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
      const w = Math.floor(rect.width)
      const h = Math.floor(rect.height)
      sizeRef.current = { w, h }
      if (!bufferRef.current || bufferRef.current.length !== w) {
        ;[bufferRef.current, smoothBufRef.current] = resizeBuffers(
          bufferRef.current,
          smoothBufRef.current,
          w
        )
      }
    }

    setup()

    const raw = getComputedStyle(canvas).color
    const m = raw.match(/\d+/g)
    if (m && m.length >= 3) {
      accentRef.current = `rgba(${m[0]},${m[1]},${m[2]},0.85)`
      accentRgbRef.current = { r: +m[0], g: +m[1], b: +m[2] }
    }

    const ro = new ResizeObserver(setup)
    ro.observe(canvas)
    return () => ro.disconnect()
  }, [])

  // Register the per-frame draw callback with the rAF loop.
  useEffect(() => {
    let lastLiveLevel = DB_FLOOR

    registerDraw('oscilloscope', (leftDb, rightDb, timestamp) => {
      const ctx = ctxRef.current
      if (!ctx) return
      const { w, h } = sizeRef.current
      if (w === 0 || h === 0) return

      if (!bufferRef.current || bufferRef.current.length !== w) {
        ;[bufferRef.current, smoothBufRef.current] = resizeBuffers(
          bufferRef.current,
          smoothBufRef.current,
          w
        )
      }
      const buf = bufferRef.current
      const smooth = smoothBufRef.current!

      // --- Dead air: thermal noise polyline, bypass ring buffer entirely ---
      if (deadAirRef.current) {
        ctx.clearRect(0, 0, w, h)
        ctx.save()
        ctx.strokeStyle = 'rgba(255,255,255,0.08)'
        ctx.lineWidth = 1
        ctx.setLineDash([4, 6])
        ctx.beginPath()
        ctx.moveTo(0, h / 2)
        ctx.lineTo(w, h / 2)
        ctx.stroke()
        ctx.restore()
        ctx.save()
        ctx.strokeStyle = accentRef.current
        ctx.lineWidth = 1.5
        ctx.setLineDash([])
        ctx.beginPath()
        for (let x = 0; x <= w; x++) {
          const py = h / 2 + (Math.random() - 0.5) * 2
          if (x === 0) ctx.moveTo(x, py)
          else ctx.lineTo(x, py)
        }
        ctx.stroke()
        ctx.restore()
        return
      }

      // --- Cold boot sine override ---
      const cbStart = coldBootRef.current.startTs
      const inColdBoot = cbStart !== null && cbStart >= 0 && timestamp - cbStart < COLD_BOOT_VU_MS

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
      // RMS level → pixel amplitude (used for zero-line opacity).
      const rmsNorm = Math.max(0, Math.min(1, (levelDb - DB_FLOOR) / (DB_CEIL - DB_FLOOR)))
      const rmsAmp = Math.pow(rmsNorm, AMP_GAMMA) * maxAmp

      // Peak follower: instant attack, per-frame decay.
      // During pause, mpv stops emitting so the store's peakDb stays stale —
      // use the decaying levelDb instead so the envelope falls with the pause decay.
      const peakDb = isPausedRef.current ? levelDb : (useStore.getState().peakDb ?? levelDb)
      const peakNorm = Math.max(0, Math.min(1, (peakDb - DB_FLOOR) / (DB_CEIL - DB_FLOOR)))
      const rawPeakAmp = Math.pow(peakNorm, AMP_GAMMA) * maxAmp
      const envAmp = Math.max(peakEnvRef.current * PEAK_DECAY, rawPeakAmp)
      peakEnvRef.current = envAmp

      // --- AR(1) per-pixel update ---
      if (inColdBoot) {
        // Stateless normalized sine — draw step applies envAmp as scale,
        // so the trace fades in as the cold boot VU sweep ramps up.
        for (let i = 0; i < w; i++) {
          buf[i] = Math.sin((i / COLD_BOOT_WAVELENGTH_PX) * 2 * Math.PI)
        }
        // The sine is already band-limited — no spatial smoothing needed.
      } else {
        // Crest-driven tightness: high crest (percussive) → loose/spiky;
        // low crest (compressed/sustained) → smooth/slow.
        const crest = useStore.getState().crestDb ?? DEFAULT_CREST
        const crestT = Math.max(0, Math.min(1, (crest - CREST_LOW) / (CREST_HIGH - CREST_LOW)))
        const tightness = TIGHT_MAX_FRAME - crestT * (TIGHT_MAX_FRAME - TIGHT_MIN_FRAME)
        tightnessRef.current = tightness

        // Buffer is normalized to [-1, 1]; amplitude is applied at draw time.
        // Fixed innovation keeps the trace textured at all amplitudes — including
        // silence — preserving the dead-air noise texture.
        for (let i = 0; i < w; i++) {
          let v = buf[i] * tightness + (Math.random() - 0.5) * 2 * INNOVATION_SCALE
          if (v > 1) v = 1
          if (v < -1) v = -1
          buf[i] = v
        }

        // Amplitude-adaptive spatial smoothing with a scratch buffer.
        //   envAmp < SMOOTH_THRESHOLD → 0 passes: raw noisy texture (silence/dead air)
        //   SMOOTH_THRESHOLD..55% maxAmp → 2 passes: smooth waveform (quiet signal)
        //   > 55% maxAmp → 1 pass: retains texture ("loud and noisy" feel)
        const smoothPasses =
          envAmp < SMOOTH_THRESHOLD ? 0 : envAmp > maxAmp * 0.55 ? 1 : SMOOTH_PASSES

        for (let pass = 0; pass < smoothPasses; pass++) {
          smooth[0] = buf[0] * 0.75 + buf[1] * 0.25
          smooth[w - 1] = buf[w - 2] * 0.25 + buf[w - 1] * 0.75
          for (let i = 1; i < w - 1; i++) {
            smooth[i] = buf[i - 1] * 0.25 + buf[i] * 0.5 + buf[i + 1] * 0.25
          }
          buf.set(smooth)
        }
      }

      // --- Pause drift ---
      // After the pause decay settles, breathe the trace with a slow 0.1 Hz
      // sinusoidal vertical offset — barely perceptible, more felt than seen.
      // Clears instantly when playback resumes.
      let driftY = 0
      if (isPausedRef.current && pauseStartTsRef.current >= 0) {
        const pauseElapsed = timestamp - pauseStartTsRef.current
        if (pauseElapsed >= PAUSE_DECAY_MS) {
          driftY = Math.sin(timestamp * 0.001 * 0.1 * 2 * Math.PI) * 1.8
        }
      }

      // --- Draw ---
      ctx.clearRect(0, 0, w, h)

      // Zero-line: brighter at silence (acts as resting visual), dim at full signal.
      const zeroOpacity = Math.max(0.04, 0.22 - (rmsAmp / maxAmp) * 0.18)
      ctx.save()
      ctx.strokeStyle = `rgba(255,255,255,${zeroOpacity})`
      ctx.lineWidth = 0.75
      ctx.setLineDash([3, 7])
      ctx.beginPath()
      ctx.moveTo(0, h / 2)
      ctx.lineTo(w, h / 2)
      ctx.stroke()
      ctx.restore()

      // Waveform: build path once, stroke twice for phosphor glow.
      // displayAmp scales normalized buf[-1,1] to pixels; NOISE_FLOOR_AMP
      // keeps the trace visible at silence rather than collapsing to a dot.
      const displayAmp = Math.max(envAmp, NOISE_FLOOR_AMP)
      const { r, g, b } = accentRgbRef.current
      const midY = h / 2 + driftY

      const traceStyle = useStore.getState().stereoRackTraceStyle
      const styleParams = STYLE_PARAMS[traceStyle] ?? STYLE_PARAMS.glowy

      // --- Trippy: maintain echo ring buffer and draw ghost traces ---
      if (traceStyle === 'trippy') {
        echoFrameCountRef.current++
        if (echoFrameCountRef.current % TRIPPY_SNAPSHOT_FRAMES === 0) {
          const snap = new Float32Array(w)
          for (let x = 0; x < w; x++) snap[x] = midY - buf[x] * displayAmp
          echoSnapshotsRef.current.push(snap)
          echoTimestampsRef.current.push(timestamp)
          if (echoSnapshotsRef.current.length > TRIPPY_MAX_SNAPSHOTS) {
            echoSnapshotsRef.current.shift()
            echoTimestampsRef.current.shift()
          }
        }

        ctx.save()
        ctx.setLineDash([])
        ctx.lineJoin = 'round'
        for (let e = 0; e < echoSnapshotsRef.current.length; e++) {
          const age = Math.min(1, (timestamp - echoTimestampsRef.current[e]) / 1000)
          const echoAlpha = (1 - age) * 0.35
          if (echoAlpha < 0.01) continue
          const snap = echoSnapshotsRef.current[e]
          ctx.strokeStyle = `rgba(${r},${g},${b},${echoAlpha})`
          ctx.lineWidth = 2
          ctx.beginPath()
          for (let x = 0; x < snap.length && x < w; x++) {
            if (x === 0) ctx.moveTo(x, snap[x])
            else ctx.lineTo(x, snap[x])
          }
          ctx.stroke()
        }
        ctx.restore()
      } else {
        // Clear stale echo state when switching away from trippy.
        if (echoSnapshotsRef.current.length > 0) {
          echoSnapshotsRef.current = []
          echoTimestampsRef.current = []
          echoFrameCountRef.current = 0
        }
      }

      // --- Main trace ---
      ctx.save()
      ctx.setLineDash([])
      ctx.lineJoin = 'round'
      ctx.beginPath()
      for (let x = 0; x < w; x++) {
        const py = midY - buf[x] * displayAmp
        if (x === 0) ctx.moveTo(x, py)
        else ctx.lineTo(x, py)
      }
      // Outer glow (skipped for 'clean')
      if (styleParams.glowWidth > 0) {
        ctx.strokeStyle = `rgba(${r},${g},${b},${styleParams.glowAlpha})`
        ctx.lineWidth = styleParams.glowWidth
        ctx.stroke()
      }
      // Inner trace (bright pinpoint core)
      ctx.strokeStyle = `rgba(${r},${g},${b},${TRACE_ALPHA})`
      ctx.lineWidth = TRACE_LINE_WIDTH
      ctx.stroke()
      ctx.restore()
    })

    return () => unregisterDraw('oscilloscope')
  }, [registerDraw, unregisterDraw, coldBootRef, deadAirRef])

  return <canvas ref={canvasRef} className="oscilloscope" />
}
