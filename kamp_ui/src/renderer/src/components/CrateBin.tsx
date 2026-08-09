// The bin (KAMP-656) — a three-quarter crate you flip through.
//
// PROTOTYPE. This is the checkpoint the ticket mandates: build the flip first,
// and if it reads as cover-flow pastiche after tuning, fall back to the flat
// "Counter" treatment. CrateSleeve and its styles are deliberately left intact
// so that fallback is a one-line switch in CrateView rather than a rewrite.
//
// The whole thing is a re-skin of state that already existed. `focusIndex` is
// KAMP-650's; records before it lie on the flipped pile at the crate lip,
// records at and after it stand tilted in the bin. So "flip" is just `,`/`.`
// under a different rendering — no new interaction, no new state, nothing added
// to the store. That is also why riffling keeps up: a CSS transition on
// transform retargets mid-flight, where a @keyframes animation would restart.
//
// Accessibility is NOT part of the skin. This is still the listbox KAMP-650
// shipped — roving tabindex, aria-setsize/aria-posinset, arrows inside the
// widget — and the perspective sits on top of that contract rather than
// replacing it.
import React, { useState } from 'react'
import { crateArtUrl } from '../api/client'
import type { CrateItem } from '../api/client'

// Depth stagger between standing sleeves, in px. Small: enough for art slivers
// to tease the next few records, not so much that the bin becomes a fan.
const DEPTH = 14
// How far back the standing sleeves lean. The ticket asks for 72-78 degrees off
// the horizontal, which is 12-18 off vertical.
const LEAN = 15

function BinSleeve({
  item,
  index,
  total,
  focused,
  flipped,
  passed,
  onFocus
}: {
  item: CrateItem
  index: number
  total: number
  focused: boolean
  // Already flipped past: lying on the pile at the crate lip.
  flipped: boolean
  passed: boolean
  onFocus: (index: number) => void
}): React.JSX.Element {
  const [artFailed, setArtFailed] = useState(false)
  const showArt = Boolean(item.art_url) && !artFailed

  const classes = ['bin-sleeve']
  if (focused) classes.push('bin-sleeve--focused')
  if (flipped) classes.push('bin-sleeve--flipped')
  if (passed || item.state === 'dismissed') classes.push('bin-sleeve--passed')
  if (item.state === 'wishlisted') classes.push('bin-sleeve--wishlisted')
  if (item.state === 'purchased') classes.push('bin-sleeve--purchased')

  // Standing records step back into the bin; flipped ones stack forward at the
  // lip. Both are expressed as one custom property so the transform itself stays
  // in CSS, where the transition and its overshoot live.
  const depth = flipped ? index - total : index

  return (
    <li
      className={classes.join(' ')}
      role="option"
      aria-selected={focused}
      aria-setsize={total}
      aria-posinset={index + 1}
      aria-label={`${item.title} by ${item.artist}`}
      // Roving tabindex, exactly as the rail had it: only the focused sleeve is
      // reachable by Tab, and the arrows move which one that is. Keeping real
      // DOM focus in the widget is what lets CrateView scope its key handling to
      // its own container instead of listening on document.
      tabIndex={focused ? 0 : -1}
      onClick={() => onFocus(index)}
      style={
        {
          '--bin-depth': depth,
          // Later records paint behind earlier ones; flipped ones stack on top
          // of the pile in the order they landed.
          zIndex: flipped ? total + index : total - index
        } as React.CSSProperties
      }
    >
      <div className={`bin-sleeve-art${showArt ? ' has-art' : ''}`}>
        {showArt && (
          <img
            className="bin-sleeve-art-img"
            src={crateArtUrl(item.id)}
            alt=""
            loading="lazy"
            onError={() => setArtFailed(true)}
          />
        )}
      </div>
      {(passed || item.state === 'dismissed') && (
        <span className="bin-sleeve-badge" aria-hidden="true">
          ✕
        </span>
      )}
      {item.state === 'wishlisted' && (
        <span className="bin-sleeve-badge" aria-hidden="true">
          ♥
        </span>
      )}
      {item.state === 'purchased' && (
        <span className="bin-sleeve-badge" aria-hidden="true">
          ◆
        </span>
      )}
    </li>
  )
}

export function CrateBin({
  items,
  focusIndex,
  pendingDismissals,
  spineName,
  railRef,
  onFocus
}: {
  items: CrateItem[]
  focusIndex: number
  pendingDismissals: number[]
  spineName: string
  railRef: React.RefObject<HTMLUListElement | null>
  onFocus: (index: number) => void
}): React.JSX.Element {
  return (
    <div
      className="crate-bin"
      style={
        { '--bin-lean': `${LEAN}deg`, '--bin-depth-step': `${DEPTH}px` } as React.CSSProperties
      }
    >
      <ul
        className="crate-bin-records"
        role="listbox"
        aria-label="Crate"
        ref={railRef}
        tabIndex={-1}
      >
        {items.map((item, index) => (
          <BinSleeve
            key={item.id}
            item={item}
            index={index}
            total={items.length}
            focused={index === focusIndex}
            flipped={index < focusIndex}
            passed={pendingDismissals.includes(item.id)}
            onFocus={onFocus}
          />
        ))}
      </ul>
      {/* The divider card at the back of the bin. Written in marker, so it is
          the one place a handwritten-adjacent face is allowed — small, and once. */}
      {spineName && <p className="crate-bin-spine">{spineName}</p>}
    </div>
  )
}
