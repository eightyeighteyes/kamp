// KAMP-561: React wrapper mounting the bokeh engine behind the Now Playing art.
// The only React-aware piece; the engine + palette math live in framework-free
// modules. Gated hard on visibility so the rAF loop never runs while the pane is
// display:none, the toggle is off, the window is hidden, or reduced-motion is set.

import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../../store'
import { BokehEngine } from './bokehEngine'
import { accentPalette, loadPalette } from './palette'

interface Props {
  active: boolean
  artUrl: string
}

function accentHex(): string {
  return getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#7c86e1'
}

// Inner component: only mounted when the toggle is on, so "off" means zero canvas
// and zero GPU. All effect hooks live here (always run while mounted).
function BokehCanvas({ active, artUrl }: Props): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<BokehEngine | null>(null)

  const [hidden, setHidden] = useState(() => document.hidden)
  // One-shot read (matches useNewArrivalHighlight); a live OS toggle mid-session is
  // an accepted gap.
  const [reducedMotion] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )

  // Window hide/show — the rAF must stop when the window is occluded/minimized.
  useEffect(() => {
    const onVis = (): void => setHidden(document.hidden)
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [])

  // Create the engine once; tear down only on unmount. ResizeObserver handles
  // window resizes; the run/pause effect re-sizes explicitly on becoming active.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const engine = new BokehEngine(canvas, accentPalette(accentHex()))
    engineRef.current = engine
    engine.resize()
    const ro = new ResizeObserver(() => engine.resize())
    ro.observe(canvas)
    return () => {
      ro.disconnect()
      engine.destroy()
      engineRef.current = null
    }
  }, [])

  // Recompute the palette on each track/art change; crossfade in the engine. Guard
  // against skip races (abort the in-flight fetch, ignore a stale resolution).
  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    void loadPalette(artUrl, accentHex(), controller.signal).then((pal) => {
      if (!cancelled) engineRef.current?.setPalette(pal)
    })
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [artUrl])

  // Run the loop only when actually visible and motion is wanted; otherwise paint
  // the static gradient (reduced-motion) or fully stop (inactive/hidden).
  const shouldAnimate = active && !reducedMotion && !hidden
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    if (shouldAnimate) {
      engine.resize()
      engine.start()
      // Cursor parallax: drive the eased offset from motion over the pane only
      // (scoped to .now-playing, not window, so nothing outside it moves the field).
      // The handlers close over the null-checked local `engine`, so a late event
      // after teardown is inert. Add + remove are symmetric within this branch.
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
    // Reduced-motion (still active) gets a static repaint; inactive/hidden paints
    // nothing (it's not visible anyway).
    if (active && reducedMotion) engine.resize()
    return undefined
  }, [shouldAnimate, active, reducedMotion])

  return <canvas ref={canvasRef} className="now-playing-bokeh" aria-hidden="true" />
}

export function BokehBackground(props: Props): React.JSX.Element | null {
  const enabled = useStore((s) => s.nowPlayingGlowEnabled)
  if (!enabled) return null
  return <BokehCanvas {...props} />
}
