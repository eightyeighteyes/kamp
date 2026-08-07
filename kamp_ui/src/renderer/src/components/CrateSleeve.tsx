// One sleeve in the crate rail (KAMP-650).
//
// Sleeves are deliberately small and quiet: the rail is for orientation, the
// focus card is where you actually read. State is conveyed by opacity, border
// weight and a corner glyph rather than by hue — there is exactly one --accent
// token and it swings from indigo to magenta to green across the eight themes,
// so four hue-coded states could not stay legible in all of them.
import React, { useState } from 'react'
import { crateArtUrl } from '../api/client'
import type { CrateItem } from '../api/client'

// Static per-slot tilt so a rail of sleeves reads as records in a bin rather
// than a grid. Derived from the index, not random, so it never jumps between
// renders. Alternating sign, ~1-2 degrees.
function tiltFor(index: number): number {
  const magnitude = 1 + (index % 3) * 0.5
  return index % 2 === 0 ? -magnitude : magnitude
}

export function CrateSleeve({
  item,
  index,
  total,
  focused,
  passed,
  onFocus
}: {
  item: CrateItem
  index: number
  total: number
  focused: boolean
  // Passed locally but not yet committed — the 5s Undo window.
  passed: boolean
  onFocus: (index: number) => void
}): React.JSX.Element {
  const [artFailed, setArtFailed] = useState(false)
  const showArt = Boolean(item.art_url) && !artFailed

  const classes = ['crate-sleeve']
  if (focused) classes.push('crate-sleeve--focused')
  if (passed || item.state === 'dismissed') classes.push('crate-sleeve--passed')
  if (item.state === 'wishlisted') classes.push('crate-sleeve--wishlisted')
  if (item.state !== 'fresh' && item.state !== 'dismissed') classes.push('crate-sleeve--dug')

  return (
    <li
      className={classes.join(' ')}
      role="option"
      aria-selected={focused}
      aria-setsize={total}
      aria-posinset={index + 1}
      aria-label={`${item.title} by ${item.artist}`}
      // Roving tabindex: only the focused sleeve is reachable by Tab, and the
      // arrow keys move which one that is. Keeping real DOM focus in the rail is
      // what lets the view scope its key handling to its own container instead
      // of listening on document (which would pre-empt every modal's Escape).
      tabIndex={focused ? 0 : -1}
      onClick={() => onFocus(index)}
      style={{ '--crate-tilt': `${tiltFor(index)}deg` } as React.CSSProperties}
    >
      <div className={`crate-sleeve-art${showArt ? ' has-art' : ''}`}>
        {showArt && (
          <img
            className="crate-sleeve-art-img"
            src={crateArtUrl(item.id)}
            alt=""
            loading="lazy"
            onError={() => setArtFailed(true)}
          />
        )}
      </div>
      {(passed || item.state === 'dismissed') && (
        <span className="crate-sleeve-badge" aria-hidden="true">
          ✕
        </span>
      )}
      {item.state === 'wishlisted' && (
        <span className="crate-sleeve-badge" aria-hidden="true">
          ♥
        </span>
      )}
    </li>
  )
}

// An unfilled slot, shown while a crate is being dug so the rail has its full
// shape from the first moment rather than growing a sleeve at a time.
export function CrateSlot({ index }: { index: number }): React.JSX.Element {
  return (
    <li
      className="crate-sleeve crate-sleeve--empty"
      aria-hidden="true"
      style={{ '--crate-tilt': `${tiltFor(index)}deg` } as React.CSSProperties}
    >
      <div className="crate-sleeve-art" />
    </li>
  )
}
