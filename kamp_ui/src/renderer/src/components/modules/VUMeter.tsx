/**
 * VUMeter — 24-segment Dorrough-style horizontal bar.
 *
 * Static layout only (KAMP-321). Imperative draw logic (KAMP-322) and
 * peak hold (KAMP-323) will be added via useImperativeHandle in the next task.
 *
 * Zone coloring is pure CSS via :nth-child — no JS color logic here.
 * The .active class on each segment is toggled by the draw function.
 * The .vu-peak-hold element is positioned absolutely inside .vu-bar and
 * will be driven imperatively in KAMP-323.
 */
import React from 'react'
import { useStereoRack } from './StereoRackContext'

// Pre-built segment array — stable reference, avoids re-creating on every render.
const SEGMENTS = Array.from({ length: 24 }, (_, i) => i)

type VUMeterProps = {
  channel: 'L' | 'R'
}

export function VUMeter({ channel }: VUMeterProps): React.JSX.Element {
  const { isPlaying } = useStereoRack()
  const isIdle = !isPlaying

  return (
    <div className={`vu-meter vu-meter--${channel.toLowerCase()}${isIdle ? ' is-idle' : ''}`}>
      <div className="vu-bar">
        {SEGMENTS.map((i) => (
          <span key={i} className="vu-segment" />
        ))}
        {/* Peak hold floats over the bar; positioned imperatively in KAMP-323 */}
        <div className="vu-peak-hold" />
      </div>
      <span className="vu-label">{channel}</span>
    </div>
  )
}

/**
 * VUMeterPair — renders L and R meters with an optional slot between them.
 * Pass <Oscilloscope /> as children when KAMP-324 lands.
 */
export function VUMeterPair({ children }: { children?: React.ReactNode }): React.JSX.Element {
  return (
    <>
      <VUMeter channel="L" />
      {children}
      <VUMeter channel="R" />
    </>
  )
}
