// The crate at a glance (KAMP-671) — every record in the crate, down the left.
//
// This is what retired the hold shelf (KAMP-669). The shelf showed the records
// you had set aside; this shows all ten and lets you jump to any of them, which
// is strictly the larger answer to the same question.
//
// Deliberately NOT a second role="listbox". The bin is the listbox — it owns the
// roving tabindex, aria-setsize/aria-posinset and the KAMP-598 focus recovery —
// and a second one over the same selection would make a screen reader announce
// every record twice and give the arrow keys two competing meanings. So this is
// a plain list of ordinary buttons, with aria-current marking the record the bin
// is currently showing.
import React from 'react'
import type { CrateItem } from '../api/client'
import { FavoriteIcon } from './TransportIcons'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'

export function CrateTitles({
  items,
  focusIndex,
  wishlistPending,
  onFocus,
  onToggleWishlist
}: {
  items: CrateItem[]
  focusIndex: number
  wishlistPending: number[]
  onFocus: (index: number) => void
  onToggleWishlist: (item: CrateItem) => void
}): React.JSX.Element {
  const tooltip = useTooltip()

  return (
    <div className="crate-titles">
      <ul className="crate-titles-list">
        {items.map((item, index) => {
          // Read from item.state, never from a local flag: a confirmed write
          // re-broadcasts the whole crate, so "never show a heart Bandcamp has
          // not confirmed" stays structural rather than something each click
          // handler has to remember (KAMP-653).
          const wishlisted = item.state === 'wishlisted'
          // A bought record is not offered the toggle at all. `state` is a
          // single-slot rank cache and 'purchased' MASKS 'wishlisted', so an
          // owned record reports wishlisted === false and an ungated heart would
          // POST an *add* for something already in the collection (KAMP-654).
          const purchased = item.state === 'purchased'
          const saving = wishlistPending.includes(item.id)

          return (
            <li
              key={item.id}
              className={`crate-title-row${
                index === focusIndex ? ' crate-title-row--current' : ''
              }`}
            >
              <button
                className="crate-title-btn"
                onClick={() => onFocus(index)}
                // Marks the row the bin is showing without claiming this is a
                // selection widget in its own right.
                aria-current={index === focusIndex ? 'true' : undefined}
              >
                <span className="crate-title-artist">{item.artist}</span>
                <span className="crate-title-album">{item.title}</span>
              </button>
              <button
                className={`crate-title-heart${
                  wishlisted || purchased ? ' crate-title-heart--done' : ''
                }`}
                onClick={() => onToggleWishlist(item)}
                disabled={saving || purchased}
                // The icon carries no text, so the label has to name the record
                // as well as the action — "Wishlist" ten times over is useless
                // to a screen reader.
                aria-label={
                  purchased
                    ? `${item.title} by ${item.artist} is in your collection`
                    : `${wishlisted ? 'Remove' : 'Add'} ${item.title} by ${item.artist} ${
                        wishlisted ? 'from' : 'to'
                      } your wishlist`
                }
                {...tooltip(
                  purchased
                    ? TOOLTIPS.CRATE_PURCHASED
                    : wishlisted
                      ? TOOLTIPS.CRATE_UNWISHLIST
                      : TOOLTIPS.CRATE_WISHLIST
                )}
              >
                <FavoriteIcon active={wishlisted || purchased} size={15} />
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
