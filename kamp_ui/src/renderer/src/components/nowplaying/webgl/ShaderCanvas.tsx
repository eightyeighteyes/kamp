// KAMP-780: the generic React wrapper for every shader background. A background's
// registry Engine is just `(props) => <ShaderCanvas spec={SPEC} {...props} />`.
//
// Hook-for-hook the same lifecycle as BokehCanvas (the registry's engine
// contract): the loop runs only while the pane is active, the window is visible
// and reduced-motion is off; reduced-motion paints a still frame. On top of that:
//   * the audio level is fed from useStore.subscribe, stamped at arrival;
//   * if the engine can't be built, or loses its context for good, the canvas is
//     replaced by StillBackdrop (a static CSS gradient, not Bokeh).

import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../../../store'
import { accentPalette } from '../palette'
import type { NowPlayingEngineProps } from '../registry'
import { registerGLTarget } from './devHooks'
import { ShaderEngine, type ContentRect, type ShaderSpec } from './shaderEngine'
import { StillBackdrop } from './StillBackdrop'
import { accentHex, bgHex, useAlbumPalette } from './themeColors'

interface ShaderCanvasProps<R, S> extends NowPlayingEngineProps {
  /** Must be a module-level constant: the engine is built once per mount. */
  spec: ShaderSpec<R, S>
}

/** `t` relative to the canvas box `c`, normalised (DOM y-down), shrunk by `inset` px. */
function normalise(c: DOMRect, t: DOMRect, inset = 0): ContentRect | null {
  if (t.width <= 2 * inset || t.height <= 2 * inset) return null
  return {
    x0: (t.left + inset - c.left) / c.width,
    x1: (t.right - inset - c.left) / c.width,
    y0: (t.top + inset - c.top) / c.height,
    y1: (t.bottom - inset - c.top) / c.height
  }
}

/**
 * Measure the title and the art relative to the canvas and hand both to the
 * engine. Each is null while hidden (they hide together, KAMP-769).
 *
 * The title is its TEXT box, not the element: `.now-playing-title` and
 * `.now-playing-meta` are full-width, so their boxes span the whole pane and
 * anything muted "behind the title" would be muted everywhere.
 *
 * The art is the element, inset by its corner radius so the rounded corners,
 * where the canvas shows through, stay outside it. Shaders skip the work inside
 * it (KAMP-772): the art is opaque, so those pixels are never seen.
 */
function measureContent<R, S>(engine: ShaderEngine<R, S>, canvas: HTMLCanvasElement): void {
  const pane = canvas.closest('.now-playing')
  const c = canvas.getBoundingClientRect()
  if (!pane || c.width === 0 || c.height === 0) {
    engine.setContentRects(null, null)
    return
  }
  let title: ContentRect | null = null
  const titleEl = pane.querySelector('.now-playing-title')
  if (titleEl) {
    const range = document.createRange()
    range.selectNodeContents(titleEl)
    title = normalise(c, range.getBoundingClientRect())
  }
  let art: ContentRect | null = null
  const artEl = pane.querySelector('.now-playing-art')
  if (artEl) {
    const radius = parseFloat(getComputedStyle(artEl).borderTopLeftRadius) || 0
    art = normalise(c, artEl.getBoundingClientRect(), radius)
  }
  engine.setContentRects(title, art)
}

export function ShaderCanvas<R, S>(props: ShaderCanvasProps<R, S>): React.JSX.Element {
  const [failed, setFailed] = useState(false)
  if (failed) return <StillBackdrop artUrl={props.artUrl} />
  return <ShaderGL {...props} onFatal={() => setFailed(true)} />
}

function ShaderGL<R, S>({
  spec,
  active,
  artUrl,
  onFatal
}: ShaderCanvasProps<R, S> & { onFatal: () => void }): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<ShaderEngine<R, S> | null>(null)
  const onFatalRef = useRef(onFatal)
  useEffect(() => {
    onFatalRef.current = onFatal
  })

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

  // Build the engine once per mount. `spec` and `reducedMotion` are stable for the
  // component's lifetime, so they are deliberately not dependencies.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const engine = ShaderEngine.create(canvas, spec, accentPalette(accentHex()), {
      reducedMotion,
      onFatal: (reason) => {
        console.warn(`[KAMP-780] ${spec.name}: falling back to the static backdrop: ${reason}`)
        onFatalRef.current()
      }
    })
    if (!engine) return
    engineRef.current = engine
    engine.setBg(bgHex())
    engine.setArtVisible(useStore.getState().nowPlayingArtVisible)
    measureContent(engine, canvas)
    engine.resize()
    const ro = new ResizeObserver(() => {
      measureContent(engine, canvas)
      engine.resize()
    })
    ro.observe(canvas)
    const unregister = registerGLTarget(engine.devTarget())
    // Subscribe (not poll from the tick) so each ~20Hz sample is stamped at
    // arrival. Every audio.level message rewrites both channels, so a change in
    // either marks a new sample. An exactly repeated value is missed; that only
    // matters for a held tone longer than the follower's stale window, so a
    // delight moment keyed to a long sustained level won't see it.
    const unsub = useStore.subscribe((s, prev) => {
      if (s.leftDb === prev.leftDb && s.rightDb === prev.rightDb) return
      const l = s.leftDb ?? -Infinity
      const r = s.rightDb ?? -Infinity
      engine.pushLevel(Math.max(l, r), performance.now())
    })
    return () => {
      unsub()
      unregister()
      ro.disconnect()
      engine.destroy()
      engineRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // The shader paints --bg itself (the canvas is opaque), so every theme switch
  // must reach it; the accent feeds the no-colors fallback palette.
  const selectedTheme = useStore((s) => s.selectedTheme)
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    engine.setAccent(accentHex())
    engine.setBg(bgHex())
  }, [selectedTheme])

  useAlbumPalette(artUrl, (pal) => engineRef.current?.setPalette(pal))

  // KAMP-774: only an id-to-different-id change is a track change. The first
  // value (mount, StrictMode's second mount) and null transitions (the app's
  // restore, "Nothing playing") are not, so a delight moment never fires on
  // opening the pane. The engine further ignores changes while stopped.
  const trackId = useStore((s) => s.player.current_track?.id ?? null)
  const prevTrackId = useRef<number | null>(null)
  useEffect(() => {
    const prev = prevTrackId.current
    prevTrackId.current = trackId
    const engine = engineRef.current
    const canvas = canvasRef.current
    if (!engine || !canvas) return
    // A new title has a new text width, and a wrapped one shrinks the art.
    measureContent(engine, canvas)
    if (prev != null && trackId != null && prev !== trackId) engine.trackChanged()
  }, [trackId])

  // The canvas ResizeObserver doesn't fire on an art toggle, so re-measure here.
  // Effects run after commit, so the title is already in (or out of) the DOM.
  const artVisible = useStore((s) => s.nowPlayingArtVisible)
  useEffect(() => {
    const engine = engineRef.current
    const canvas = canvasRef.current
    if (!engine || !canvas) return
    engine.setArtVisible(artVisible)
    measureContent(engine, canvas)
  }, [artVisible])

  const shouldAnimate = active && !reducedMotion && !hidden
  useEffect(() => {
    const engine = engineRef.current
    if (!engine) return
    if (shouldAnimate) {
      engine.resize()
      engine.start()
      // Cursor parallax over the pane only, as BokehCanvas.
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
      // resize() is a no-op when the size is unchanged, so paint the still explicitly.
      engine.resize()
      engine.renderStatic()
    }
    return undefined
  }, [shouldAnimate, active, reducedMotion])

  // No blur class: shader output is already soft, and skipping the CSS filter is
  // most of the GPU saving over Bokeh (KAMP-776).
  return <canvas ref={canvasRef} className="now-playing-backdrop" aria-hidden="true" />
}
