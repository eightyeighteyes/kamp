// KAMP-776 spike (not for merge): React wrapper for the WebGL "Thalassa" engine.
// ThalassaGL mirrors BokehCanvas hook-for-hook so the comparison is like for
// like. When WebGL is unavailable, or the context is lost and never restored,
// it swaps to StillBackdrop: a static CSS gradient, NOT Bokeh — with hardware
// acceleration off, Bokeh's blur(28px) is software-composited and is likely the
// most expensive option there is.

import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../../store'
import { ThalassaEngine } from './thalassaEngine'
import { accentPalette, loadPalette, type Palette } from './palette'
import type { NowPlayingEngineProps } from './registry'

function accentHex(): string {
  return getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#7c86e1'
}

function bgHex(): string {
  return getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() || '#141414'
}

// Recompute the palette per track, guarding against skip races (as BokehCanvas).
function usePalette(artUrl: string, apply: (p: Palette) => void): void {
  const applyRef = useRef(apply)
  useEffect(() => {
    applyRef.current = apply
  })
  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    void loadPalette(artUrl, accentHex(), controller.signal).then((pal) => {
      if (!cancelled) applyRef.current(pal)
    })
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [artUrl])
}

export function ThalassaCanvas(props: NowPlayingEngineProps): React.JSX.Element {
  const [failed, setFailed] = useState(false)
  if (failed) return <StillBackdrop artUrl={props.artUrl} />
  return <ThalassaGL {...props} onFatal={() => setFailed(true)} />
}

function ThalassaGL({
  active,
  artUrl,
  onFatal
}: NowPlayingEngineProps & { onFatal: () => void }): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<ThalassaEngine | null>(null)
  const onFatalRef = useRef(onFatal)
  useEffect(() => {
    onFatalRef.current = onFatal
  })

  const [hidden, setHidden] = useState(() => document.hidden)
  const [reducedMotion] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )

  useEffect(() => {
    const onVis = (): void => setHidden(document.hidden)
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const engine = ThalassaEngine.create(canvas, accentPalette(accentHex()), (reason) => {
      console.warn(`[KAMP-776] WebGL background falling back: ${reason}`)
      onFatalRef.current()
    })
    if (!engine) return
    engineRef.current = engine
    engine.setBg(bgHex())
    engine.resize()
    const ro = new ResizeObserver(() => engine.resize())
    ro.observe(canvas)
    // Audio level: subscribe (not poll from the tick) so each ~20Hz sample is
    // stamped at arrival. Every audio.level message rewrites both channels, so a
    // change in either marks a new sample; an exactly repeated value is missed,
    // which only matters for a held tone longer than the follower's stale window.
    const unsub = useStore.subscribe((s, prev) => {
      if (s.leftDb === prev.leftDb && s.rightDb === prev.rightDb) return
      const l = s.leftDb ?? -Infinity
      const r = s.rightDb ?? -Infinity
      engine.pushLevel(Math.max(l, r), performance.now())
    })
    return () => {
      unsub()
      ro.disconnect()
      engine.destroy()
      engineRef.current = null
    }
  }, [])

  const selectedTheme = useStore((s) => s.selectedTheme)
  useEffect(() => {
    engineRef.current?.setBg(bgHex())
  }, [selectedTheme])

  usePalette(artUrl, (pal) => engineRef.current?.setPalette(pal))

  const shouldAnimate = active && !reducedMotion && !hidden
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    if (shouldAnimate) {
      engine.resize()
      engine.start()
      const pane = canvasRef.current?.closest('.now-playing') as HTMLElement | null
      if (!pane) return () => engine.stop()
      const onMove = (e: PointerEvent): void => {
        const rect = pane.getBoundingClientRect()
        if (rect.width === 0 || rect.height === 0) return
        engine.setPointer(
          ((e.clientX - rect.left) / rect.width) * 2 - 1,
          ((e.clientY - rect.top) / rect.height) * 2 - 1
        )
      }
      const onLeave = (): void => engine.setPointer(0, 0)
      pane.addEventListener('pointermove', onMove, { passive: true })
      pane.addEventListener('pointerleave', onLeave, { passive: true })
      return () => {
        pane.removeEventListener('pointermove', onMove)
        pane.removeEventListener('pointerleave', onLeave)
        engine.stop()
      }
    }
    engine.stop()
    if (active && reducedMotion) engine.resize()
    return undefined
  }, [shouldAnimate, active, reducedMotion])

  // No blur class: the shader output is already soft, and skipping the CSS
  // filter is part of what this engine is being measured on.
  return <canvas ref={canvasRef} className="now-playing-backdrop" aria-hidden="true" />
}

// Static, compositor-cheap fallback: two dominant swatches over the themed bg
// (the .now-playing-backdrop class supplies var(--bg) and the KAMP-631 backing).
function StillBackdrop({ artUrl }: { artUrl: string }): React.JSX.Element {
  const [pal, setPal] = useState<Palette>(() => accentPalette(accentHex()))
  usePalette(artUrl, setPal)
  const a = pal.colors[0] ?? { r: 40, g: 40, b: 48 }
  const b = pal.colors[1] ?? a
  const style: React.CSSProperties = {
    backgroundImage: `radial-gradient(ellipse at center, transparent 45%, color-mix(in srgb, var(--bg) 35%, transparent) 100%), linear-gradient(135deg, rgba(${a.r},${a.g},${a.b},0.22), rgba(${b.r},${b.g},${b.b},0.22))`
  }
  return <div className="now-playing-backdrop" style={style} aria-hidden="true" />
}
