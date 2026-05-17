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
// TrackLeft — with marquee scroll state machine
// ---------------------------------------------------------------------------

// idle → ellipsis-1 → ellipsis-2 → ellipsis-3 → scrolling → end-hold → idle (loop)
type ScrollPhase = 'idle' | 'ellipsis-1' | 'ellipsis-2' | 'ellipsis-3' | 'scrolling' | 'end-hold'

// px/sec scroll rate
const SCROLL_RATE = 40
// ms in idle before ellipsis phase begins
const IDLE_DELAY_MS = 1200
// ms per ellipsis step
const ELLIPSIS_STEP_MS = 400
// ms to rest at the end of the string before snapping back
const END_HOLD_MS = 1000

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
  const overflowPxRef = useRef<number>(0)

  // Cancel all timers and snap back to the start position instantly.
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

  // Measure overflow and kick off the idle → ellipsis → scroll cycle.
  const start = useCallback((): void => {
    const container = containerRef.current
    const measure = measureRef.current
    const scrollEl = scrollRef.current
    if (!container || !measure || !scrollEl) return

    const containerW = container.getBoundingClientRect().width
    const textW = measure.getBoundingClientRect().width
    if (textW <= containerW) return // fits — no scroll needed

    overflowPxRef.current = textW - containerW

    // idle pause → ellipsis-1 → ellipsis-2 → ellipsis-3 → scroll
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
            phaseRef.current = 'scrolling'
            if (ellipsisRef.current) ellipsisRef.current.textContent = ''
            const duration = overflowPxRef.current / SCROLL_RATE
            scrollEl.style.transition = `transform ${duration}s linear`
            scrollEl.style.transform = `translateX(-${overflowPxRef.current}px)`
          }, ELLIPSIS_STEP_MS)
        }, ELLIPSIS_STEP_MS)
      }, ELLIPSIS_STEP_MS)
    }, IDLE_DELAY_MS)
  }, [])

  // Wire transitionend: hold at the end for END_HOLD_MS, then snap back and restart.
  // The rAF between the snap-back and the next start() ensures the browser sees the
  // reset and the next transition as separate paints — without it the snap-back animates.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onEnd = (e: TransitionEvent): void => {
      if (e.propertyName !== 'transform') return
      if (phaseRef.current !== 'scrolling') return
      phaseRef.current = 'end-hold'
      timerRef.current = setTimeout(() => {
        if (!scrollRef.current) return
        scrollRef.current.style.transition = 'none'
        scrollRef.current.style.transform = 'translateX(0)'
        if (ellipsisRef.current) ellipsisRef.current.textContent = ''
        phaseRef.current = 'idle'
        requestAnimationFrame(() => start())
      }, END_HOLD_MS)
    }
    el.addEventListener('transitionend', onEnd)
    return () => el.removeEventListener('transitionend', onEnd)
  }, [start])

  // Restart the machine on title/artist change and on container resize.
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

  // The measuring span holds the full unclipped text so its width can be compared
  // against the container without the container's overflow:hidden affecting the result.
  const fullText = artist ? `${artist} — ${title}` : title

  return (
    <span className="track-left" ref={containerRef}>
      <span className="track-scroll-inner" ref={scrollRef}>
        {artist ? (
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
        )}
      </span>
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
