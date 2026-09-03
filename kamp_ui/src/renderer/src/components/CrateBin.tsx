// The bin (KAMP-656) — a three-quarter crate you flip through.
//
// CrateSleeve and its styles are deliberately left intact: the flat "Counter"
// treatment is still the fallback if this ever needs pulling, and it is a
// one-line switch in CrateView rather than a rewrite. It is also what the build
// still renders while a crate streams in.
//
// The whole thing is a re-skin of state that already existed. `focusIndex` is
// KAMP-650's; records before it lie on the flipped pile at the crate lip,
// records at and after it stand tilted in the bin. So "flip" is just `,`/`.`
// under a different rendering — no new interaction, no new state, nothing added
// to the store. That is also why riffling keeps up: a CSS transition on
// transform retargets mid-flight, where a @keyframes animation would restart.
//
// Accessibility is not part of the skin, but the semantics DID change in
// KAMP-672: this was a role="listbox" until the arrows became the deck's
// transport keys. A listbox whose arrows do not move between options is a
// broken promise, so it is a plain list now — roving tabindex and
// aria-setsize/aria-posinset kept, aria-current in place of aria-selected, and
// the titles list carries the crate as real buttons.
import React, { useEffect, useRef, useState } from 'react'
import { crateArtUrl } from '../api/client'
import type { CrateItem } from '../api/client'

// The bin's geometry — sleeve size, lean and depth stagger — lives entirely in
// crate.css, derived from a single --bin-sleeve. It used to be set inline from
// constants here too, which silently won: an inline custom property beats the
// stylesheet, so the proportional depth step introduced with the sizing pass
// never actually applied and a hardcoded 14px kept being used. One home for it.

// What goes on the corner sticker. Only facts already on the row: the release
// year and the label. Deliberately no price and no scarcity claim — those would
// be invented, and an invented sticker is the one thing a record shop cannot do
// and stay trustworthy. Returns '' when there is nothing honest to print, and
// the caller renders no sticker at all rather than an empty one.
function sticker(item: CrateItem): string {
  const year = item.release_date ? item.release_date.slice(0, 4) : ''
  return [year, item.label].filter(Boolean).join(' · ')
}

function BinSleeve({
  item,
  index,
  total,
  focusIndex,
  focused,
  flipped,
  away,
  onFocus,
  onPlay
}: {
  item: CrateItem
  index: number
  total: number
  focusIndex: number
  focused: boolean
  // Already flipped past: lying on the pile at the crate lip.
  flipped: boolean
  // Out of the crate and on the deck.
  away: boolean
  onFocus: (index: number) => void
  // Put this record on (KAMP-679). Offered on the FOCUSED sleeve only: the
  // others are stacked in the same place behind a 7% depth step and a 15° lean,
  // so a back sleeve shows a ~12px crescent — small enough that aiming a
  // double-click at one is a coin flip with the sleeve in front of it. Clicking
  // a back sleeve focuses it, and then it is the front one.
  onPlay: (item: CrateItem) => void
}): React.JSX.Element {
  const [artFailed, setArtFailed] = useState(false)
  const showArt = Boolean(item.art_url) && !artFailed

  const classes = ['bin-sleeve']
  if (focused) classes.push('bin-sleeve--focused')
  if (flipped) classes.push('bin-sleeve--flipped')
  // On the deck. Rendered as a GHOST rather than hidden (KAMP-672): the record
  // out on the deck is the one you are most likely to act on next, and an empty
  // slot left nothing to aim at. Never unmounted under either treatment, so the
  // list semantics, roving tabindex and the focus recovery hold throughout
  // (KAMP-668) — only the look of the sleeve changes.
  if (away) classes.push('bin-sleeve--away')
  // No 'dismissed' branch: pass is gone (KAMP-674). A legacy dismissed row from
  // before the removal is an ordinary record now, which is the honest rendering
  // — nothing in the product acts on that state any more.
  if (item.state === 'wishlisted') classes.push('bin-sleeve--wishlisted')
  if (item.state === 'purchased') classes.push('bin-sleeve--purchased')

  // Two different numbers, because the pile and the bin are ordered differently.
  //
  // `rel` is distance from the record you are looking at, so the standing stack
  // advances as you flip rather than the focused record pulling away from a
  // bin that never moves.
  //
  // `order` is the absolute index, and it is what stacks the pile: records land
  // in index order, so record 0 is at the BOTTOM and each later one lands on top
  // of it. Using `rel` here would rebuild the pile upside down every flip.
  const rel = index - focusIndex
  const order = index

  return (
    <li
      className={classes.join(' ')}
      // How the flight finds its start and end rects. An attribute rather than a
      // ref per sleeve: the view needs to measure exactly one of these, once, at
      // the moment a flight begins.
      data-bin-item={item.id}
      data-crate-sleeve=""
      // A plain list item, NOT role="option" (KAMP-672). The listbox pattern
      // promises arrow-key navigation between options, and the arrows are the
      // deck's transport keys now — , and . are the only way to move the
      // selection. Claiming the role while not honouring its keyboard contract
      // would announce "listbox, 10 items" to a screen reader and then do nothing
      // when they pressed an arrow.
      //
      // aria-current marks the record on show; setsize/posinset are valid on a
      // listitem and still say where in the crate you are. The full crate is also
      // in the titles list as real buttons, which is the better way through it.
      aria-current={focused ? 'true' : undefined}
      aria-setsize={total}
      aria-posinset={index + 1}
      aria-label={`${item.title} by ${item.artist}`}
      // Roving tabindex: only the focused sleeve is reachable by Tab. Keeping
      // real DOM focus in the widget is what lets CrateView scope its key
      // handling to its own container instead of listening on document.
      tabIndex={focused ? 0 : -1}
      onClick={() => onFocus(index)}
      // `away` is the record already on the deck — putting it on again would
      // restart the album from track 1 for no reason the user asked for.
      onDoubleClick={focused && !away ? () => onPlay(item) : undefined}
      style={
        {
          '--bin-rel': rel,
          '--bin-order': order,
          // Authoritative, not a fallback. The records container is deliberately
          // FLAT rather than preserve-3d (see crate.css), so siblings composite
          // as layers in this order and can never intersect — which is what
          // stopped a record in motion showing through the one it moves toward.
          //
          // Standing records paint back-to-front (nearer index on top), and the
          // entire pile paints above all of them, newest landing highest.
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
      {/* Corner sticker, on the focused record only — a bin of ten stickers is
          a spreadsheet. Year and label straight off the row, and nothing else:
          no price, no rating, no invented "rare". A shop that lies on its
          stickers is precisely what this feature is defined against, so an
          absent field means no sticker rather than a placeholder.

          aria-hidden because both values are already in the focus card above;
          a screen reader should not hear the year twice. */}
      {focused && sticker(item) && (
        <span className="bin-sleeve-sticker" aria-hidden="true">
          {sticker(item)}
        </span>
      )}
    </li>
  )
}

// How long the stock-in cascade needs to finish: the last record's delay plus
// its own drop, with a little slack. Ten records at 70ms is 630ms + 260ms.
const STOCK_IN_MS = 1200

export function CrateBin({
  items,
  crateNo,
  focusIndex,
  awayItemId,
  spineName,
  railRef,
  onFocus,
  onPlay
}: {
  items: CrateItem[]
  crateNo: number | null
  focusIndex: number
  // The record currently out of the crate and on the deck, if any.
  awayItemId: number | null
  spineName: string
  railRef: React.RefObject<HTMLUListElement | null>
  onFocus: (index: number) => void
  onPlay: (item: CrateItem) => void
}): React.JSX.Element {
  // Stock-in runs on a DELIVERY, not on every render that happens to have
  // records in it. Arriving at a crate that already exists — switching to the
  // tab, reloading the renderer — is not a delivery, and replaying the cascade
  // there would turn a moment into a tic.
  //
  // So the first crate_no this component sees is seeded silently, and only a
  // change from it counts. That also gets the first-ever crate right: mount
  // seeds null, the build completes, null -> 1 is a change, and it plays.
  const seenRef = useRef<number | null>(null)
  const seededRef = useRef(false)
  const [stocking, setStocking] = useState(false)

  useEffect(() => {
    if (!seededRef.current) {
      seededRef.current = true
      seenRef.current = crateNo
      return
    }
    if (crateNo === null || crateNo === seenRef.current) return
    seenRef.current = crateNo
    setStocking(true)
    const timer = window.setTimeout(() => setStocking(false), STOCK_IN_MS)
    return () => window.clearTimeout(timer)
  }, [crateNo])

  return (
    <div className={`crate-bin${stocking ? ' crate-bin--stocking' : ''}`}>
      {/* The divider card, ABOVE the records — it stands at the back of the bin,
          so from this angle you read it over the tops of the sleeves rather than
          under them. Written in marker, and the one place a
          handwritten-adjacent face is allowed: small, and once. */}
      {spineName && <p className="crate-bin-spine">{spineName}</p>}
      {/* A plain list. It was role="listbox" until KAMP-672 took the arrows for
          the deck's transport — see the note on the sleeve. */}
      <ul className="crate-bin-records" aria-label="Crate" ref={railRef} tabIndex={-1}>
        {items.map((item, index) => (
          <BinSleeve
            key={item.id}
            item={item}
            index={index}
            total={items.length}
            focusIndex={focusIndex}
            focused={index === focusIndex}
            flipped={index < focusIndex}
            away={item.id === awayItemId}
            onFocus={onFocus}
            onPlay={onPlay}
          />
        ))}
      </ul>
    </div>
  )
}
