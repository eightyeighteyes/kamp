import React, { useCallback, useEffect, useRef } from 'react'
import { useStore } from '../../store'
import { useStereoRack } from './StereoRackContext'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTime(seconds: number): string {
  const s = Math.floor(seconds)
  const mm = Math.floor(s / 60)
    .toString()
    .padStart(2, '0')
  const ss = (s % 60).toString().padStart(2, '0')
  return `${mm}:${ss}`
}

// ---------------------------------------------------------------------------
// TrackLeft — wrap-around marquee scroll state machine
// ---------------------------------------------------------------------------

// Full cycle:
//   idle (1.2s) → ellipsis-1/2/3 (400ms each) → scrolling-1 (to end of text)
//   → end-hold (1s) → scrolling-2 (wrap to copy-B start, imperceptible snap)
//   → idle (1.2s) → ...
type ScrollPhase =
  | 'idle'
  | 'ellipsis-1'
  | 'ellipsis-2'
  | 'ellipsis-3'
  | 'scrolling-1'
  | 'end-hold'
  | 'scrolling-2'

const SCROLL_RATE = 40 // px/s
const IDLE_DELAY_MS = 1200 // ms idle before ellipsis phase
const ELLIPSIS_STEP_MS = 400 // ms per dot
const END_HOLD_MS = 1000 // ms to rest at end before wrap continues
// Gap between the two text copies. Must stay in sync with .track-gap width in CSS.
const MARQUEE_GAP_PX = 60

type TrackLeftProps = {
  artist: string
  title: string
  // Freeze scrolling when whimsy replaces left-cluster content (wired in KAMP-321).
  whimsyActive?: boolean
}

function TrackLeft({ artist, title, whimsyActive = false }: TrackLeftProps): React.JSX.Element {
  const containerRef = useRef<HTMLSpanElement | null>(null)
  const scrollRef = useRef<HTMLSpanElement | null>(null)
  const measureRef = useRef<HTMLSpanElement | null>(null)
  const ellipsisRef = useRef<HTMLSpanElement | null>(null)

  const phaseRef = useRef<ScrollPhase>('idle')
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  // Distance from origin to "end of text aligned with right edge of container"
  const overflowPxRef = useRef<number>(0)
  // Full single-copy text width — needed for the phase-2 scroll target
  const textWidthRef = useRef<number>(0)

  const cancel = useCallback((): void => {
    clearTimeout(timerRef.current)
    timerRef.current = undefined
    phaseRef.current = 'idle'
    const el = scrollRef.current
    if (el) {
      el.style.transition = 'none'
      el.style.transform = 'translateX(0)'
    }
    if (ellipsisRef.current) ellipsisRef.current.textContent = ''
  }, [])

  const start = useCallback((): void => {
    const container = containerRef.current
    const measure = measureRef.current
    const scrollEl = scrollRef.current
    if (!container || !measure || !scrollEl) return

    const containerW = container.getBoundingClientRect().width
    const textW = measure.getBoundingClientRect().width
    if (textW <= containerW) return // fits — no scroll needed

    textWidthRef.current = textW
    overflowPxRef.current = textW - containerW

    timerRef.current = setTimeout(() => {
      if (ellipsisRef.current) ellipsisRef.current.textContent = '.'
      phaseRef.current = 'ellipsis-1'
      timerRef.current = setTimeout(() => {
        if (ellipsisRef.current) ellipsisRef.current.textContent = '..'
        phaseRef.current = 'ellipsis-2'
        timerRef.current = setTimeout(() => {
          if (ellipsisRef.current) ellipsisRef.current.textContent = '...'
          phaseRef.current = 'ellipsis-3'
          timerRef.current = setTimeout(() => {
            if (!scrollRef.current) return
            phaseRef.current = 'scrolling-1'
            if (ellipsisRef.current) ellipsisRef.current.textContent = ''
            const dur = overflowPxRef.current / SCROLL_RATE
            scrollEl.style.transition = `transform ${dur}s linear`
            scrollEl.style.transform = `translateX(-${overflowPxRef.current}px)`
          }, ELLIPSIS_STEP_MS)
        }, ELLIPSIS_STEP_MS)
      }, ELLIPSIS_STEP_MS)
    }, IDLE_DELAY_MS)
  }, [])

  // Handle both transition ends — phase 1 (end of text) and phase 2 (wrap seam).
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    const onEnd = (e: TransitionEvent): void => {
      if (e.propertyName !== 'transform') return

      if (phaseRef.current === 'scrolling-1') {
        // Reached end of text — hold, then continue scrolling into the copy-B start.
        phaseRef.current = 'end-hold'
        timerRef.current = setTimeout(() => {
          const s = scrollRef.current
          if (!s) return
          // Phase 2: scroll from end-of-text to copy-B start (the imperceptible seam).
          phaseRef.current = 'scrolling-2'
          const target = textWidthRef.current + MARQUEE_GAP_PX
          const remaining = target - overflowPxRef.current
          s.style.transition = `transform ${remaining / SCROLL_RATE}s linear`
          s.style.transform = `translateX(-${target}px)`
        }, END_HOLD_MS)
      } else if (phaseRef.current === 'scrolling-2') {
        // Reached copy-B start — visually identical to origin, so snap is imperceptible.
        el.style.transition = 'none'
        el.style.transform = 'translateX(0)'
        phaseRef.current = 'idle'
        // rAF gap: ensure the browser commits the reset before the next transition starts.
        requestAnimationFrame(() => start())
      }
    }

    el.addEventListener('transitionend', onEnd)
    return () => el.removeEventListener('transitionend', onEnd)
  }, [start])

  // Restart on title/artist change and on container resize.
  useEffect(() => {
    cancel()
    start()
    const container = containerRef.current
    if (!container) return
    const ro = new ResizeObserver(() => {
      cancel()
      start()
    })
    ro.observe(container)
    return () => {
      ro.disconnect()
      cancel()
    }
  }, [title, artist, cancel, start])

  // Preempt scrolling when whimsy replaces left-cluster content.
  useEffect(() => {
    if (whimsyActive) cancel()
  }, [whimsyActive, cancel])

  const fullText = artist ? `${artist} — ${title}` : title

  const primaryContent = artist ? (
    <>
      <span className="track-artist">{artist}</span>
      <span className="track-sep"> &mdash; </span>
      <span className="track-title">
        {title}
        <span ref={ellipsisRef} />
      </span>
    </>
  ) : (
    <span className="track-title">
      {title}
      <span ref={ellipsisRef} />
    </span>
  )

  const copyContent = artist ? (
    <>
      <span className="track-artist">{artist}</span>
      <span className="track-sep"> &mdash; </span>
      <span className="track-title">{title}</span>
    </>
  ) : (
    <span className="track-title">{title}</span>
  )

  return (
    <span className="track-left" ref={containerRef}>
      <span className="track-scroll-inner" ref={scrollRef}>
        <span className="track-copy">{primaryContent}</span>
        <span className="track-gap" />
        <span className="track-copy" aria-hidden="true">
          {copyContent}
        </span>
      </span>
      {/* Measuring span — sits outside overflow:hidden so its width is unclipped */}
      <span className="track-measure" ref={measureRef} aria-hidden="true">
        {fullText}
      </span>
    </span>
  )
}

// ---------------------------------------------------------------------------
// TrackRight
// ---------------------------------------------------------------------------

type TrackRightProps = { position: number; duration: number; year: string; format: string }

function TrackRight({ position, duration, year, format }: TrackRightProps): React.JSX.Element {
  return (
    <span className="track-right">
      <span className="track-time">{formatTime(position)}</span>
      <span className="track-timesep">/</span>
      <span className="track-time">{formatTime(duration)}</span>
      {year && <span className="track-year">{year}</span>}
      {format && <span className="track-format">{format}</span>}
    </span>
  )
}

// ---------------------------------------------------------------------------
// TrackDisplay
// ---------------------------------------------------------------------------

export function TrackDisplay(): React.JSX.Element {
  const { isPlaying, trackMeta } = useStereoRack()
  const position = useStore((s) => s.player.position)

  return (
    <div className={`track-display${isPlaying ? '' : ' is-idle'}`}>
      {trackMeta && (
        <>
          <TrackLeft artist={trackMeta.artist} title={trackMeta.title} />
          <TrackRight
            position={position}
            duration={trackMeta.duration}
            year={trackMeta.year}
            format={trackMeta.format}
          />
        </>
      )}
    </div>
  )
}
