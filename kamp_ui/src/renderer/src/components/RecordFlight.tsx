// The record travelling between the crate and the deck (KAMP-668).
//
// This is the one genuinely cross-container motion in the whole feature. The
// sleeve lives inside the bin's stacking context and the deck is elsewhere in
// the tree, so nothing that transforms the sleeve in place can reach across —
// hence a single travelling element, positioned over the page, that neither
// container owns.
//
// It is a FLIP: the element is laid out at its DESTINATION, then given an
// inverting transform that maps it back onto the source rect, then that
// transform is removed so it animates to where it already is. Doing it this way
// keeps the whole animation on `transform` — no width/height/top/left tweening,
// which is the project rule and also the only way this stays cheap.
//
// It is inert by construction: aria-hidden, pointer-events: none, never focused,
// never part of the listbox. The record it represents keeps its place in the bin
// the entire time; only the picture of it moves.
import React, { useEffect, useState } from 'react'

export type FlightRect = { left: number; top: number; width: number; height: number }

export function RecordFlight({
  from,
  to,
  artUrl,
  onDone
}: {
  from: FlightRect
  to: FlightRect
  artUrl: string | null
  onDone: () => void
}): React.JSX.Element {
  const [settled, setSettled] = useState(false)

  useEffect(() => {
    // Two frames, not one. The browser has to paint the inverted transform
    // before it is removed, or it coalesces both into a single style change and
    // there is no transition at all — the record simply appears at the deck.
    let second = 0
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setSettled(true))
    })
    return () => {
      cancelAnimationFrame(first)
      cancelAnimationFrame(second)
    }
  }, [])

  useEffect(() => {
    // transitionend is the normal path, but it never fires if the two rects
    // happen to match (nothing to animate) — and a traveller stuck on screen
    // would leave the crate looking permanently short a record. A timeout longer
    // than the transition guarantees cleanup either way.
    const bail = window.setTimeout(onDone, 900)
    return () => window.clearTimeout(bail)
  }, [onDone])

  // transform-origin is top left so the translate and the scale compose from the
  // same corner; with the default centre origin the two disagree and the record
  // arrives offset by half the size difference.
  const dx = from.left - to.left
  const dy = from.top - to.top
  const sx = to.width > 0 ? from.width / to.width : 1
  const sy = to.height > 0 ? from.height / to.height : 1

  return (
    <div
      className="crate-flight"
      aria-hidden="true"
      style={{
        left: `${to.left}px`,
        top: `${to.top}px`,
        width: `${to.width}px`,
        height: `${to.height}px`,
        transform: settled ? 'none' : `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`
      }}
      onTransitionEnd={onDone}
    >
      {artUrl && <img className="crate-flight-art" src={artUrl} alt="" />}
    </div>
  )
}
