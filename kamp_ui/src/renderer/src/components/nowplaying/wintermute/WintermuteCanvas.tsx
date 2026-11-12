// KAMP-768: React wrapper for the Wintermute background. A copy of SparksCanvas
// (KAMP-767) with the engine swapped. Same lifecycle as ChapelCanvas and
// ShaderCanvas (the registry's engine contract): the loop runs only while the pane
// is active, the window is visible and reduced motion is off; reduced motion
// paints the designed still frame. From ShaderCanvas it also takes:
//   * the track-change gating: only an id-to-different-id change is a track
//     change, so the gust never fires on mount, StrictMode's second mount or the
//     app's null -> id restore;
//   * re-measuring the title and art on a track change (a new title rewraps) and on
//     the art toggle (KAMP-769), which the canvas ResizeObserver doesn't see;
//   * the __kampGL dev target, so the no-blur and near-layer-off variants can be
//     measured.

import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../../../store'
import { measureContent } from '../contentRects'
import { accentPalette } from '../palette'
import type { NowPlayingEngineProps } from '../registry'
import { registerGLTarget } from '../webgl/devHooks'
import { accentHex, bgHex, useAlbumPalette } from '../webgl/themeColors'
import { WintermuteEngine } from './wintermuteEngine'

function measureInto(engine: WintermuteEngine, canvas: HTMLCanvasElement): void {
  const { title, art } = measureContent(canvas)
  engine.setContentRects(title, art)
}

export function WintermuteCanvas({ active, artUrl }: NowPlayingEngineProps): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<WintermuteEngine | null>(null)

  const [hidden, setHidden] = useState(() => document.hidden)
  // One-shot read, as BokehCanvas; a live OS toggle mid-session is an accepted gap.
  const [reducedMotion] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )

  useEffect(() => {
    const onVis = (): void => setHidden(document.hidden)
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [])

  // Build once per mount; reducedMotion is stable for the component's lifetime.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const engine = new WintermuteEngine(canvas, accentPalette(accentHex()), { reducedMotion })
    engineRef.current = engine
    engine.setBg(bgHex())
    measureInto(engine, canvas)
    engine.resize()
    const ro = new ResizeObserver(() => {
      measureInto(engine, canvas)
      engine.resize()
    })
    ro.observe(canvas)
    const unregister = registerGLTarget(engine.devTarget())
    return () => {
      unregister()
      ro.disconnect()
      engine.destroy()
      engineRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selectedTheme = useStore((s) => s.selectedTheme)
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    engine.setAccent(accentHex())
    engine.setBg(bgHex())
  }, [selectedTheme])

  useAlbumPalette(artUrl, (pal) => engineRef.current?.setPalette(pal))

  const trackId = useStore((s) => s.player.current_track?.id ?? null)
  const prevTrackId = useRef<number | null>(null)
  useEffect(() => {
    const prev = prevTrackId.current
    prevTrackId.current = trackId
    const engine = engineRef.current
    const canvas = canvasRef.current
    if (!engine || !canvas) return
    measureInto(engine, canvas)
    if (prev != null && trackId != null && prev !== trackId) engine.trackChanged()
  }, [trackId])

  // Effects run after commit, so the title is already in (or out of) the DOM.
  const artVisible = useStore((s) => s.nowPlayingArtVisible)
  useEffect(() => {
    const engine = engineRef.current
    const canvas = canvasRef.current
    if (!engine || !canvas) return
    measureInto(engine, canvas)
  }, [artVisible])

  const shouldAnimate = active && !reducedMotion && !hidden
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    if (shouldAnimate) {
      engine.resize()
      engine.start()
      // Pointer over the pane only, so nothing outside it stirs the snow.
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
      const onLeave = (): void => engine.pointerLeave()
      pane.addEventListener('pointermove', onMove, { passive: true })
      pane.addEventListener('pointerleave', onLeave, { passive: true })
      return () => {
        pane.removeEventListener('pointermove', onMove)
        pane.removeEventListener('pointerleave', onLeave)
        engine.stop()
      }
    }
    engine.stop()
    if (active && reducedMotion) {
      engine.resize()
      engine.renderStatic()
    }
    return undefined
  }, [shouldAnimate, active, reducedMotion])

  // No engine class: no CSS filter. The blur is baked into the sprites (KAMP-776/777).
  return <canvas ref={canvasRef} className="now-playing-backdrop" aria-hidden="true" />
}
