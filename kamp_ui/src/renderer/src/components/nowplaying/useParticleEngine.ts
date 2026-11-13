// KAMP-768: the shared React lifecycle for the Canvas 2D particle backgrounds
// (Sparks, Wintermute, Castalia). Each background supplies only its engine factory; the
// wiring below is the registry's engine contract and must not be duplicated per
// background, or a fix to one (track-change gating, pointer scoping, re-measuring)
// silently misses the other.
//
// Same lifecycle as ChapelCanvas and ShaderCanvas: the loop runs only while the
// pane is active, the window is visible and reduced motion is off; reduced motion
// paints the designed still frame. From ShaderCanvas it also takes:
//   * the track-change gating: only an id-to-different-id change is a track
//     change, so a track-change moment never fires on mount, StrictMode's second
//     mount or the app's null -> id restore;
//   * re-measuring the title and art on a track change (a new title rewraps) and on
//     the art toggle (KAMP-769), which the canvas ResizeObserver doesn't see;
//   * the __kampGL dev target, so the measurement variants can be run.

import { useEffect, useRef, useState, type RefObject } from 'react'
import { playbackOf, type RunoutPlayback } from '../../../../shared/runoutPlayback'
import { albumKeyOf, trackKeyOf } from '../../../../shared/trackIdentity'
import { useStore } from '../../store'
import { measureContent } from './contentRects'
import { accentPalette, type Palette } from './palette'
import type { NowPlayingEngineProps } from './registry'
import { registerGLTarget, type GLDevTarget } from './webgl/devHooks'
import type { ContentRect } from './webgl/shaderEngine'
import { accentHex, bgHex, useAlbumPalette } from './webgl/themeColors'

/** What a particle engine must expose for the shared lifecycle to drive it. */
export interface ParticleEngine {
  setBg(hex: string): void
  setAccent(hex: string): void
  setPalette(next: Palette): void
  trackChanged(): void
  /**
   * Optional (KAMP-773): the playing track's album and track keys. Called on
   * mount and always before trackChanged(), in the same effect, so an engine
   * can tell a skip within an album from a new album.
   */
  setIdentity?(albumKey: string, trackKey: string): void
  /**
   * Optional (KAMP-771): playback state, pushed once on mount and then on every
   * player or queue snapshot. Only an engine that implements it is subscribed.
   * It may arrive before or after a track change's setIdentity/trackChanged.
   */
  setPlayback?(p: RunoutPlayback): void
  setPointer(nx: number, ny: number): void
  pointerLeave(): void
  setContentRects(title: ContentRect | null, art: ContentRect | null): void
  resize(): void
  start(): void
  stop(): void
  destroy(): void
  renderStatic(): void
  devTarget(): GLDevTarget
}

export type ParticleEngineFactory = (
  canvas: HTMLCanvasElement,
  initial: Palette,
  opts: { reducedMotion: boolean }
) => ParticleEngine

function measureInto(engine: ParticleEngine, canvas: HTMLCanvasElement): void {
  const { title, art } = measureContent(canvas)
  engine.setContentRects(title, art)
}

/**
 * Drive a particle engine for the lifetime of the calling component. Returns the
 * ref for its canvas. `create` is read once, at mount.
 */
export function useParticleEngine(
  create: ParticleEngineFactory,
  { active, artUrl }: NowPlayingEngineProps
): RefObject<HTMLCanvasElement | null> {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<ParticleEngine | null>(null)

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

  // Build once per mount; reducedMotion and the factory are stable for its lifetime.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const engine = create(canvas, accentPalette(accentHex()), { reducedMotion })
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
    // KAMP-771: playback, for an engine that wants it. A store subscription rather
    // than a selector, so the ~4 Hz position ticks never re-render this component.
    // subscribe() doesn't fire on subscribe, and applyServerState sets nothing
    // while paused, so push the current state once or a paused launch never
    // arrives. Filtering on the slice references ignores every unrelated set().
    let unsubscribe = (): void => {}
    if (engine.setPlayback) {
      const push = (): void => {
        const { player, queue } = useStore.getState()
        engine.setPlayback?.(playbackOf(player, queue))
      }
      push()
      unsubscribe = useStore.subscribe((s, prev) => {
        if (s.player !== prev.player || s.queue !== prev.queue) push()
      })
    }
    return () => {
      unsubscribe()
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
    // Identity first, in this one effect: a second effect would lose the order.
    // Read from the store here rather than subscribing, so it can't re-run apart
    // from the id.
    const track = useStore.getState().player.current_track
    if (track && engine.setIdentity) engine.setIdentity(albumKeyOf(track), trackKeyOf(track))
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
      // Pointer over the pane only, so nothing outside it disturbs the particles.
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

  return canvasRef
}
