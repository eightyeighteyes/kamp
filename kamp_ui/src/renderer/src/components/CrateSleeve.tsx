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
  onFocus
}: {
  item: CrateItem
  index: number
  total: number
  focused: boolean
  onFocus: (index: number) => void
}): React.JSX.Element {
  const [artFailed, setArtFailed] = useState(false)
  const showArt = Boolean(item.art_url) && !artFailed

  const classes = ['crate-sleeve']
  if (focused) classes.push('crate-sleeve--focused')
  if (item.state === 'wishlisted') classes.push('crate-sleeve--wishlisted')
  if (item.state === 'purchased') classes.push('crate-sleeve--purchased')
  // 'dismissed' is a legacy state with no writer (KAMP-674), so it no longer
  // gets an exemption here — a record carrying it counts as dug like any other.
  if (item.state !== 'fresh') classes.push('crate-sleeve--dug')

  return (
    <li
      className={classes.join(' ')}
      data-crate-sleeve=""
      // Plain list item rather than role="option", matching the bin — the
      // listbox pattern promises arrow-key navigation and the arrows belong to
      // the transport now (KAMP-672). , and . move the selection.
      aria-current={focused ? 'true' : undefined}
      aria-setsize={total}
      aria-posinset={index + 1}
      aria-label={`${item.title} by ${item.artist}`}
      // Roving tabindex: only the focused sleeve is reachable by Tab. Keeping
      // real DOM focus in the rail is what lets the view scope its key handling
      // to its own container instead of listening on document (which would
      // pre-empt every modal's Escape).
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
      {item.state === 'wishlisted' && (
        <span className="crate-sleeve-badge" aria-hidden="true">
          ♥
        </span>
      )}
      {/* Outranks the heart rather than sitting beside it: `state` is a
          single-slot rank cache, so a purchased pick reports only 'purchased'
          even if it was wishlisted first. Showing the record you own is the
          truer of the two anyway. */}
      {item.state === 'purchased' && (
        <span className="crate-sleeve-badge" aria-hidden="true">
          ◆
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
