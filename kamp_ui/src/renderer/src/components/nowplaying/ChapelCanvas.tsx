// KAMP-775: React wrapper for the Chapel background. Same lifecycle as
// BokehCanvas and ShaderCanvas (the registry's engine contract): the loop runs
// only while the pane is active, the window is visible and reduced-motion is off;
// reduced-motion paints the composition at rest. Chapel adds three inputs:
//   * the current track id, because artUrl is per album and a track change
//     inside an album must still re-proportion the fields;
//   * whether the art (and so the title) is visible (KAMP-769), which decides the
//     dim band behind the title;
//   * pointerleave as its own event, so a seam glow cools instead of jumping to
//     whichever seam is nearest the pane centre.

import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../../store'
import { ChapelEngine } from './chapelEngine'
import { accentPalette } from './palette'
import type { NowPlayingEngineProps } from './registry'
import { accentHex, bgHex, useAlbumPalette } from './webgl/themeColors'

export function ChapelCanvas({ active, artUrl }: NowPlayingEngineProps): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<ChapelEngine | null>(null)

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
    const engine = new ChapelEngine(canvas, accentPalette(accentHex()), { reducedMotion })
    engineRef.current = engine
    engine.setBg(bgHex())
    engine.resize()
    const ro = new ResizeObserver(() => engine.resize())
    ro.observe(canvas)
    return () => {
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
  useEffect(() => {
    engineRef.current?.retarget()
  }, [trackId])

  const artVisible = useStore((s) => s.nowPlayingArtVisible)
  useEffect(() => {
    engineRef.current?.setArtVisible(artVisible)
  }, [artVisible])

  const shouldAnimate = active && !reducedMotion && !hidden
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    if (shouldAnimate) {
      engine.resize()
      engine.start()
      // Pointer over the pane only, so nothing outside it warms a seam.
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

  // No engine class: no filter (feathering is in the gradients, KAMP-776/777).
  return <canvas ref={canvasRef} className="now-playing-backdrop" aria-hidden="true" />
}
