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
import { ShaderEngine, type ShaderSpec } from './shaderEngine'
import { StillBackdrop } from './StillBackdrop'
import { accentHex, bgHex, useAlbumPalette } from './themeColors'

interface ShaderCanvasProps<R> extends NowPlayingEngineProps {
  /** Must be a module-level constant: the engine is built once per mount. */
  spec: ShaderSpec<R>
}

export function ShaderCanvas<R>(props: ShaderCanvasProps<R>): React.JSX.Element {
  const [failed, setFailed] = useState(false)
  if (failed) return <StillBackdrop artUrl={props.artUrl} />
  return <ShaderGL {...props} onFatal={() => setFailed(true)} />
}

function ShaderGL<R>({
  spec,
  active,
  artUrl,
  onFatal
}: ShaderCanvasProps<R> & { onFatal: () => void }): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<ShaderEngine<R> | null>(null)
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
    engine.resize()
    const ro = new ResizeObserver(() => engine.resize())
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
