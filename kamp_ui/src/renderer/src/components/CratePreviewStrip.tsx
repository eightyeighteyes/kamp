// The deck's mini-player (KAMP-651, re-skinned in KAMP-671).
//
// Deliberately its own strip rather than reusing the TransportBar: the whole
// promise of preview is that the main transport keeps showing the user's own
// queue, untouched. Two players on screen is the point, not a redundancy.
//
// Position is interpolated locally. The daemon publishes on transitions only —
// a sample plus the moment it was taken — so the bar can move smoothly without
// the daemon broadcasting several times a second.
import React, { useEffect, useState } from 'react'
import type { CrateItem, PreviewState } from '../api/client'
import { formatClock } from '../utils/formatClock'
import { FavoriteIcon, NextIcon, PauseIcon, PlayIcon, PrevIcon } from './TransportIcons'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'

export function CratePreviewStrip({
  preview,
  item,
  wishlistSaving,
  onToggle,
  onStep,
  onSeek,
  onToggleWishlist
}: {
  preview: PreviewState
  // The record on the deck. The strip names the RECORD, not the track — see the
  // meta block below.
  item: CrateItem | null
  wishlistSaving: boolean
  onToggle: () => void
  onStep: (delta: number) => void
  onSeek: (position: number) => void
  onToggleWishlist: (item: CrateItem) => void
}): React.JSX.Element {
  const [now, setNow] = useState(() => Date.now() / 1000)
  const tooltip = useTooltip()

  // Tick only while actually playing — a paused or buffering preview has a
  // fixed position, and a timer running against nothing is just wakeups.
  useEffect(() => {
    if (preview.state !== 'playing' || preview.buffering) return
    const id = window.setInterval(() => setNow(Date.now() / 1000), 250)
    return () => window.clearInterval(id)
  }, [preview.state, preview.buffering])

  // Extrapolate from the last sample. Clamped to the track length so a stalled
  // stream cannot march the bar off the end — the daemon's next transition
  // corrects it.
  const elapsed =
    preview.state === 'playing' && !preview.buffering
      ? Math.max(0, now - preview.position_updated_at)
      : 0
  const position = Math.min(preview.position + elapsed, preview.duration || Infinity)
  const pct = preview.duration > 0 ? Math.min(100, (position / preview.duration) * 100) : 0

  const playing = preview.state === 'playing'
  const preparing = preview.state === 'preparing' || preview.buffering

  // Nothing on the deck (KAMP-678). The strip still renders — its transport is
  // the affordance that says the deck is there — but the controls that need a
  // record are disabled rather than merely inert. Play stays live: it means
  // "put the record you're looking at on", which is the whole point of showing
  // the strip early.
  const empty = item === null

  const wishlisted = item?.state === 'wishlisted'
  const purchased = item?.state === 'purchased'

  return (
    <div
      className={`crate-preview${empty ? ' crate-preview--empty' : ''}`}
      role="group"
      aria-label="Preview"
    >
      <div className="crate-preview-controls">
        <button
          className="crate-preview-btn"
          onClick={() => onStep(-1)}
          disabled={empty}
          aria-label="Previous track"
        >
          <PrevIcon size={16} />
        </button>
        <button
          className="crate-preview-btn crate-preview-btn--play"
          onClick={onToggle}
          aria-label={playing ? 'Pause preview' : 'Play preview'}
        >
          {playing ? <PauseIcon size={20} /> : <PlayIcon size={20} />}
        </button>
        <button
          className="crate-preview-btn"
          onClick={() => onStep(1)}
          disabled={empty}
          aria-label="Next track"
        >
          <NextIcon size={16} />
        </button>

        {/* The RECORD, not the track (KAMP-671). The track is named in the list
            directly below with the current one highlighted, so putting it here
            too spent the player's most prominent line on the one fact already on
            screen — while the record it belongs to went unnamed. "Cueing it up"
            still takes the slot while there is nothing to name yet. */}
        <div className="crate-preview-meta">
          {preparing ? (
            <span className="crate-preview-title">Cueing it up…</span>
          ) : empty ? (
            <span className="crate-preview-title crate-preview-title--empty">
              Nothing on the deck
            </span>
          ) : (
            <>
              <span className="crate-preview-artist">{item?.artist ?? ''}</span>
              <span className="crate-preview-title">{item?.title ?? ''}</span>
            </>
          )}
        </div>

        {item && (
          <button
            className={`crate-preview-btn${
              wishlisted || purchased ? ' crate-preview-btn--done' : ''
            }`}
            onClick={() => onToggleWishlist(item)}
            disabled={wishlistSaving || purchased}
            aria-label={
              purchased
                ? 'In your collection'
                : wishlisted
                  ? 'Remove from your wishlist'
                  : 'Add to your wishlist'
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
        )}
      </div>

      {/* One bar, and it is still a real control. The spec asks for the thin bar
          without a handle; there used to be a range input AND a decorative div
          below it. Deleting the input would have taken keyboard seeking with it,
          so the input IS the bar — thumb hidden in CSS, progress painted by the
          --pct gradient stop. Pointer and arrow-key seek both survive. */}
      <input
        className="crate-preview-seek"
        type="range"
        min={0}
        max={Math.max(1, Math.floor(preview.duration))}
        value={Math.floor(position)}
        onChange={(e) => onSeek(Number(e.target.value))}
        disabled={empty}
        style={{ '--pct': `${pct}%` } as React.CSSProperties}
        aria-label="Seek preview"
      />

      <div className="crate-preview-clock">
        <span>{formatClock(position)}</span>
        <span>{formatClock(preview.duration)}</span>
      </div>
    </div>
  )
}
