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
//   → end-hold (1s) → scrolling-2 (wrap to copy-B start) → idle (1.2s) → ...
type ScrollPhase =
  | 'idle'
  | 'ellipsis-1'
  | 'ellipsis-2'
  | 'ellipsis-3'
  | 'scrolling-1'
  | 'end-hold'
  | 'scrolling-2'

const SCROLL_RATE = 40 // px/s
const IDLE_DELAY_MS = 1200
const ELLIPSIS_STEP_MS = 400
const END_HOLD_MS = 1000
// Gap between the two text copies. Must stay in sync with .track-gap width in CSS.
const MARQUEE_GAP_PX = 60

// Cold boot blink sequence: 2 off/on cycles at 150ms each = 600ms total.
const COLD_BOOT_BLINK_MS = 150

type TrackLeftProps = {
  artist: string
  title: string
  // Freeze scrolling when whimsy replaces left-cluster content (wired in KAMP-321).
  whimsyActive?: boolean
  // Triggers cold-boot blink + INIT stamp animation (KAMP-328).
  coldBoot?: boolean
}

function TrackLeft({
  artist,
  title,
  whimsyActive = false,
  coldBoot = false
}: TrackLeftProps): React.JSX.Element {
  const containerRef = useRef<HTMLSpanElement | null>(null)
  const scrollRef = useRef<HTMLSpanElement | null>(null)
  const ellipsisRef = useRef<HTMLSpanElement | null>(null)
  const copyBRef = useRef<HTMLSpanElement | null>(null)
  const initOverlayRef = useRef<HTMLSpanElement | null>(null)

  const phaseRef = useRef<ScrollPhase>('idle')
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  // Distance to scroll in phase 1: end of copy A aligns with container right edge.
  const overflowPxRef = useRef<number>(0)
  // Actual rendered width of one copy — measured from scrollWidth with copy B hidden.
  // Stored so the phase-2 transitionend handler can compute the exact target.
  const textWidthRef = useRef<number>(0)

  // Cold boot timer and "was active" gate to prevent double-start on re-renders.
  const coldBootTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const coldBootWasActiveRef = useRef(false)

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
    if (copyBRef.current) copyBRef.current.style.display = 'none'
  }, [])

  const start = useCallback((): void => {
    const container = containerRef.current
    const scrollEl = scrollRef.current
    if (!container || !scrollEl) return

    // With copy B hidden, scrollWidth = copyA_width + gap.
    // Reading scrollWidth directly from the layout avoids the sub-pixel discrepancy
    // that arises from measuring a flat-text span vs. multiple inline-flex children.
    const containerW = container.getBoundingClientRect().width
    const scrollW = scrollEl.scrollWidth // integer, copy B must be display:none here
    const textW = scrollW - MARQUEE_GAP_PX

    if (textW <= containerW) {
      // Fits — leave copy B hidden, no scroll needed.
      return
    }

    // Do NOT show copy B here. Showing it early (before the transition fires) adds
    // width to the flex container and causes a layout reflow during the idle/ellipsis
    // period. When the scrolling-1 transition is then applied, the element's layout
    // origin has shifted — producing a visible one-frame jump at scroll start.
    // Copy B is shown just before the transition fires (see innermost timer below).
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
            // Show copy B now, immediately before the transition. Force a synchronous
            // layout flush via getBoundingClientRect() so the browser commits the
            // copy-B reflow before we set the transition property. Without the flush,
            // the transition may start from a stale pre-reflow paint position,
            // producing the same jump we avoided above.
            if (copyBRef.current) copyBRef.current.style.display = 'inline-flex'
            void scrollEl.getBoundingClientRect()
            // Defer the transition one rAF so the committed reflow is the "from"
            // state the compositor sees when it starts interpolating.
            requestAnimationFrame(() => {
              if (!scrollRef.current) return
              const dur = overflowPxRef.current / SCROLL_RATE
              scrollEl.style.transition = `transform ${dur}s linear`
              scrollEl.style.transform = `translateX(-${overflowPxRef.current}px)`
            })
          }, ELLIPSIS_STEP_MS)
        }, ELLIPSIS_STEP_MS)
      }, ELLIPSIS_STEP_MS)
    }, IDLE_DELAY_MS)
  }, [])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    const onEnd = (e: TransitionEvent): void => {
      // Guard against bubbled transitionend events from child elements (e.g. the
      // ellipsis span or future descendants that may acquire transform transitions).
      if (e.target !== el) return
      if (e.propertyName !== 'transform') return

      if (phaseRef.current === 'scrolling-1') {
        // Reached end of copy A — hold, then continue into copy B.
        phaseRef.current = 'end-hold'
        timerRef.current = setTimeout(() => {
          const s = scrollRef.current
          if (!s) return
          phaseRef.current = 'scrolling-2'
          // Target: textWidth + gap. At this position, copy B's left edge is exactly
          // at the container's left edge — computed from the same scrollWidth measurement
          // used in start(), so the target is guaranteed to align with no overshoot.
          const target = textWidthRef.current + MARQUEE_GAP_PX
          const remaining = target - overflowPxRef.current
          s.style.transition = `transform ${remaining / SCROLL_RATE}s linear`
          s.style.transform = `translateX(-${target}px)`
        }, END_HOLD_MS)
      } else if (phaseRef.current === 'scrolling-2') {
        // Copy B's start is at the left edge — same visual as origin, so snap is
        // imperceptible. Hide copy B before start() so scrollWidth reads correctly.
        el.style.transition = 'none'
        el.style.transform = 'translateX(0)'
        if (copyBRef.current) copyBRef.current.style.display = 'none'
        phaseRef.current = 'idle'
        // rAF gap: browser must commit the reset before the next transition starts.
        requestAnimationFrame(() => start())
      }
    }

    el.addEventListener('transitionend', onEnd)
    return () => el.removeEventListener('transitionend', onEnd)
  }, [start])

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

  useEffect(() => {
    if (whimsyActive) cancel()
  }, [whimsyActive, cancel])

  // Cold boot: blink twice then show INIT HH:MM for ~1.6s.
  // Sequence driven by the frame loop; we just react to the flag transitions.
  useEffect(() => {
    if (coldBoot) {
      coldBootWasActiveRef.current = true
      cancel()

      const scrollEl = scrollRef.current
      const initEl = initOverlayRef.current
      if (!scrollEl) return

      // 2 off/on blink cycles (4 × 150ms = 600ms), then INIT display.
      scrollEl.style.opacity = '0'
      coldBootTimerRef.current = setTimeout(() => {
        scrollEl.style.opacity = '1'
        coldBootTimerRef.current = setTimeout(() => {
          scrollEl.style.opacity = '0'
          coldBootTimerRef.current = setTimeout(() => {
            scrollEl.style.opacity = '1'
            coldBootTimerRef.current = setTimeout(() => {
              // INIT phase: swap scroll inner for overlay text.
              scrollEl.style.display = 'none'
              scrollEl.style.opacity = '1' // reset for restore
              if (initEl) {
                const now = new Date()
                const hh = now.getHours().toString().padStart(2, '0')
                const mm = now.getMinutes().toString().padStart(2, '0')
                initEl.textContent = `INIT ${hh}:${mm}`
                initEl.style.display = 'inline-flex'
              }
              // Holds until coldBoot transitions to false (frame loop at ~2200ms).
            }, COLD_BOOT_BLINK_MS)
          }, COLD_BOOT_BLINK_MS)
        }, COLD_BOOT_BLINK_MS)
      }, COLD_BOOT_BLINK_MS)
    } else if (coldBootWasActiveRef.current) {
      // Cold boot ended — restore scroll inner and restart scroll machine.
      coldBootWasActiveRef.current = false
      clearTimeout(coldBootTimerRef.current)
      coldBootTimerRef.current = undefined
      const scrollEl = scrollRef.current
      const initEl = initOverlayRef.current
      if (initEl) {
        initEl.style.display = 'none'
        initEl.textContent = ''
      }
      if (scrollEl) {
        scrollEl.style.display = ''
        scrollEl.style.opacity = '1'
      }
      start()
    }
  }, [coldBoot, cancel, start])

  // Cleanup cold boot timers on unmount.
  useEffect(() => () => clearTimeout(coldBootTimerRef.current), [])

  const content = (withEllipsis: boolean): React.JSX.Element =>
    artist ? (
      <>
        <span className="track-artist">{artist}</span>
        <span className="track-sep"> &mdash; </span>
        <span className="track-title">
          {title}
          {withEllipsis && <span ref={ellipsisRef} />}
        </span>
      </>
    ) : (
      <span className="track-title">
        {title}
        {withEllipsis && <span ref={ellipsisRef} />}
      </span>
    )

  return (
    <span className="track-left" ref={containerRef}>
      <span className="track-scroll-inner" ref={scrollRef}>
        <span className="track-copy">{content(true)}</span>
        <span className="track-gap" />
        <span className="track-copy" aria-hidden="true" ref={copyBRef} style={{ display: 'none' }}>
          {content(false)}
        </span>
      </span>
      <span ref={initOverlayRef} className="track-init-overlay" style={{ display: 'none' }} />
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
  const { isPlaying, trackMeta, whimsyFlags } = useStereoRack()
  const position = useStore((s) => s.player.position)

  return (
    <div className={`track-display${isPlaying ? '' : ' is-idle'}`}>
      {trackMeta && (
        <>
          <TrackLeft
            artist={trackMeta.artist}
            title={trackMeta.title}
            coldBoot={whimsyFlags.coldBoot}
          />
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
