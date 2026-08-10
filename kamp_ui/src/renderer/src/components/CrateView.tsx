// Crate (KAMP-650) — a crate of ten records to dig through.
//
// User-facing name is "Crate"; `discovery` stays the code/API namespace.
// The whole crate arrives in one `discovery.crate` snapshot and is seeded from
// GET /api/v1/discovery/crate on every WS (re)connect, since _broadcast no-ops
// with no client attached.
//
// Copy here follows the brand guardrails: the clerk's voice, concrete and dry,
// and never the vocabulary of a recommender (algorithm, curated, personalized,
// "made for you", match %, feed, radio, smart). The `why` line is the server's
// and is rendered verbatim — it is the promise that every pick explains itself.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store'
import { crateArtUrl } from '../api/client'
import type { CrateItem, DiggingStats } from '../api/client'
import { CrateSleeve, CrateSlot } from './CrateSleeve'
import { CrateBin } from './CrateBin'
import { CrateTitles } from './CrateTitles'
import { crateSpineName } from './crateSpine'
import { CratePreviewStrip } from './CratePreviewStrip'
import { RecordFlight } from './RecordFlight'
import type { FlightRect } from './RecordFlight'
import { formatClock } from '../utils/formatClock'
import { BandcampIcon, FavoriteIcon, PauseIcon, PlayIcon, ShareIcon } from './TransportIcons'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'

const CRATE_SIZE = 10

function formatPause(seconds: number): string {
  const total = Math.ceil(seconds)
  const mins = Math.floor(total / 60)
  const secs = total % 60
  return mins > 0 ? `${mins}:${String(secs).padStart(2, '0')}` : `${secs}s`
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

// A record in transit between its slot in the crate and the deck (KAMP-668).
// `dir` is where it is heading, which decides both which end hides its copy and
// which end the rects came from.
type Flight = { id: number; dir: 'deck' | 'home'; from: FlightRect; to: FlightRect }

// The lifetime line. Everything the user has dug, in the clerk's register:
// concrete counts, no percentages, nothing that reads as a score to beat.
function describeHistory(s: DiggingStats): string {
  const parts = [plural(s.crates, 'crate') + ' dug', plural(s.records, 'record')]
  if (s.previewed > 0) parts.push(`${s.previewed} previewed`)
  if (s.wishlisted > 0) parts.push(`${s.wishlisted} set aside`)
  if (s.purchased > 0) parts.push(`${s.purchased} brought home`)
  return parts.join(' · ')
}

// One crate's tally. Omits the zeroes: "0 brought home" at the end of a crate
// reads as a reprimand, which is the opposite of what this is for.
function describeTally(s: DiggingStats): string {
  const parts: string[] = []
  if (s.previewed > 0) parts.push(`${s.previewed} previewed`)
  if (s.wishlisted > 0) parts.push(`${s.wishlisted} set aside`)
  if (s.purchased > 0) parts.push(`${s.purchased} brought home`)
  return parts.length > 0
    ? `${plural(s.records, 'record')} — ${parts.join(', ')}.`
    : `${plural(s.records, 'record')}.`
}

export function CrateView({ active = false }: { active?: boolean }): React.JSX.Element {
  const crate = useStore((s) => s.crate)
  const newCrate = useStore((s) => s.newCrate)
  const copyCrateItemUrl = useStore((s) => s.copyCrateItemUrl)
  const toggleCrateWishlist = useStore((s) => s.toggleCrateWishlist)
  const wishlistPending = useStore((s) => s.crateWishlistPending)
  const wishlistError = useStore((s) => s.crateWishlistError)
  const clearCrateWishlistError = useStore((s) => s.clearCrateWishlistError)
  const setActiveView = useStore((s) => s.setActiveView)
  const preview = useStore((s) => s.preview)
  const previewPlay = useStore((s) => s.previewPlay)
  const previewAction = useStore((s) => s.previewAction)
  const previewSeek = useStore((s) => s.previewSeek)
  const tooltip = useTooltip()

  const [focused, setFocused] = useState(0)
  const railRef = useRef<HTMLUListElement | null>(null)
  const [pauseRemaining, setPauseRemaining] = useState(0)

  const items = useMemo(() => crate?.items ?? [], [crate])
  const state = crate?.state ?? 'idle'
  const building = state === 'building'
  // Never derived from `filled` outside a live build: the daemon's status is
  // in-memory, so a restored crate reports the builder's last count. The
  // snapshot derives both from the stored rows when idle (KAMP-650).
  //
  // The crate is exactly what the daemon sent. There used to be a `visible`
  // subset filtering out locally-passed records; pass is gone (KAMP-674), so
  // every record in the snapshot is on screen for the life of the crate.
  const hasCrate = items.length > 0

  // Rate-limit countdown, ticked locally so the daemon publishes the deadline
  // once rather than broadcasting every second (the KAMP-639 downloads pattern).
  const pausedUntil = crate?.paused_until ?? 0
  useEffect(() => {
    const tick = (): void =>
      setPauseRemaining(pausedUntil ? Math.max(0, pausedUntil - Date.now() / 1000) : 0)
    tick()
    if (!pausedUntil) return
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [pausedUntil])

  // Clamped during render rather than corrected in an effect: the crate grows
  // while a build streams, so the stored index can briefly point past the end.
  // Deriving avoids a re-render round trip and the intermediate frame where the
  // focus card would be blank.
  const focusIndex = items.length === 0 ? 0 : Math.min(focused, items.length - 1)
  const current = items[focusIndex] ?? null

  // The done-state is read from item.state, never from a local flag. A confirmed
  // write re-broadcasts the whole crate, so this arrives from the daemon — which
  // makes "never show a done-state Bandcamp has not confirmed" structural rather
  // than something the click handler has to remember. It also survives a remount,
  // a view switch, and KAMP-652 marking items out of band.
  const wishlisted = current?.state === 'wishlisted'
  const wishlistSaving = current ? wishlistPending.includes(current.id) : false
  // A bought record is not offered a wishlist toggle at all (KAMP-654).
  //
  // Not cosmetic: `state` is a single-slot rank cache and 'purchased' (rank 4)
  // MASKS 'wishlisted' (rank 3), so after attribution `wishlisted` above goes
  // false for a record that really is on the user's Bandcamp wishlist — and the
  // next W press would POST an *add* for it. Retiring the toggle once the record
  // is owned sidesteps the masking entirely, and is the better answer anyway:
  // you do not wishlist something you already have.
  const purchased = current?.state === 'purchased'

  // KAMP-655. Both ride the crate snapshot, so they are live without a fetch and
  // the tally cannot drift from the lifetime line.
  const history = crate?.stats ?? null
  const crateTally = crate?.crate_stats ?? null
  const atLastRecord = items.length > 1 && focusIndex === items.length - 1

  // The name on the crate's divider card (KAMP-656). Derived from the snapshot
  // the view already has, because this story is skin only — no API changes. It
  // is memoised on the item identities rather than recomputed per render, and it
  // reads nothing from the clock, so a crate keeps its name.
  const spineName = useMemo(() => crateSpineName(items, crate?.hints ?? []), [items, crate?.hints])

  // What is actually ON the deck, which is deliberately NOT `current`: the
  // engine plays one item at a time and you can keep flipping while it plays, so
  // the deck must follow the preview rather than the focus. Looked up in the
  // crate because the preview state carries an id, not the row (KAMP-668).
  const deckItem = useMemo(() => {
    const id = preview?.item_id
    if (id == null || preview?.state === 'idle') return null
    return items.find((item) => item.id === id) ?? null
  }, [items, preview?.item_id, preview?.state])

  // Where a record flies TO. Measured at the moment a flight starts rather than
  // held as state — the view scrolls, so a rect captured earlier is stale.
  const deckArtRef = useRef<HTMLDivElement | null>(null)
  const [flight, setFlight] = useState<Flight | null>(null)
  const lastDeckId = useRef<number | null>(null)
  const seededDeck = useRef(false)

  // A record leaves the crate when it goes on the deck and comes back when it
  // comes off. The flight is an enhancement on that STATE CHANGE, never the
  // thing that puts it there: preview is daemon-owned and survives a renderer
  // reload (KAMP-651), so arriving with something already playing must show an
  // occupied deck and no flight at all. Hence seeding the first value silently.
  useEffect(() => {
    const nextId = deckItem?.id ?? null
    const prevId = lastDeckId.current
    lastDeckId.current = nextId
    if (!seededDeck.current) {
      seededDeck.current = true
      return
    }
    if (nextId === prevId) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const platter = deckArtRef.current?.getBoundingClientRect()
    if (!platter) return
    const sleeveOf = (id: number): DOMRect | undefined =>
      railRef.current
        ?.querySelector<HTMLElement>(`[data-bin-item="${id}"]`)
        ?.getBoundingClientRect()

    if (nextId !== null) {
      // Out of the crate and onto the deck.
      const sleeve = sleeveOf(nextId)
      if (sleeve) setFlight({ id: nextId, dir: 'deck', from: sleeve, to: platter })
    } else if (prevId !== null) {
      // Off the deck and home. Covers stopping, and covers a preview that
      // failed to play — otherwise a record that never started would leave the
      // crate a gap forever.
      const sleeve = sleeveOf(prevId)
      if (sleeve) setFlight({ id: prevId, dir: 'home', from: platter, to: sleeve })
    }
  }, [deckItem?.id, railRef])

  // Which sleeve is invisible in the bin: the one on the deck, or the one still
  // flying home. Without the second case the sleeve reappears the instant the
  // preview stops and you see the record in two places at once.
  const awayItemId = deckItem?.id ?? (flight?.dir === 'home' ? flight.id : null)
  // Same idea at the other end: the platter stays empty until the outbound
  // flight lands on it, or the art is in two places for the length of the trip.
  const platterItem = flight?.dir === 'deck' ? null : deckItem

  // Preview state for the record on screen. The engine plays one item at a
  // time, so a preview belonging to another card must not light this one up.
  const previewingThis = Boolean(
    current && preview && preview.item_id === current.id && preview.state !== 'idle'
  )
  const previewPlaying = previewingThis && preview?.state === 'playing'
  const previewPreparing = previewingThis && preview?.state === 'preparing'

  // Not memoized: `current` is derived from the filtered crate on every render,
  // so a useCallback here cannot keep a stable identity anyway.
  const togglePreview = (): void => {
    if (!current) return
    if (previewingThis) void previewAction('toggle')
    else void previewPlay(current.id)
  }

  // Leaving the view stops the preview: audio with no visible controls is the
  // one outcome worse than no preview at all.
  useEffect(() => {
    if (active) return
    if (useStore.getState().preview?.state !== 'idle') void previewAction('stop')
  }, [active, previewAction])

  // The album's own page, for buying it or reading the notes — preview now
  // handles listening (KAMP-651).
  const openOnBandcamp = useCallback((item: CrateItem): void => {
    if (item.item_url) window.api.openExternal(item.item_url)
  }, [])

  // A failure explains the record it happened on. Carrying it to the next one
  // would have the clerk apologising about something else entirely.
  const currentId = current?.id ?? null
  useEffect(() => {
    clearCrateWishlistError()
  }, [currentId, clearCrateWishlistError])

  const focusSleeve = useCallback((index: number): void => {
    setFocused(index)
    // Move real DOM focus with the selection so the roving tabindex stays
    // coherent and the container keeps receiving keys. This is also what a click
    // in the titles list calls, which is why focus lands in the bin rather than
    // staying on the row you clicked — the bin is the widget that owns selection.
    //
    // preventScroll, for the same reason the mount effect uses it: the sleeves
    // are absolutely positioned and lean out of their box, and the view is now a
    // clipped fixed-height box (KAMP-671). A scroll-into-view on a leaning
    // element can shift the whole composition inside that clip.
    const rail = railRef.current
    const option = rail?.querySelectorAll<HTMLElement>('[role="option"]')[index]
    option?.focus({ preventScroll: true })
  }, [])

  // Escape leaves the view. Deliberately a window listener, matching
  // DownloadsView: modals listen on document, which runs first, so Escape closes
  // an open dialog rather than dropping the user out of the Crate underneath it.
  useEffect(() => {
    if (!active) return
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape') return
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      // Two-stage, by state rather than by counting presses: a running preview
      // is the innermost thing Escape can dismiss, so it goes first and the
      // view stays put. This handler is on window, and modals listen on
      // document, so a dialog opened over the Crate still wins outright.
      if (useStore.getState().preview?.state !== 'idle') {
        void previewAction('stop')
        return
      }
      const prev = useStore.getState().previousView
      void setActiveView(prev && prev !== 'crate' ? prev : 'library')
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [active, setActiveView, previewAction])

  // The crate's own keys live on the view container, NOT on document. App's
  // global handler is a window listener and the React root sits below document
  // in the bubble path, so stopPropagation here wins. A document listener would
  // also win — and would pre-empt the Escape handling of every modal opened
  // afterwards, since those are document listeners too and registration order
  // decides.
  //
  // Digging is , and . rather than the arrows. The arrows are transport
  // prev/next track globally, and taking them for the length of a whole view
  // would strand a listener mid-album — the same objection that keeps Space
  // unclaimed here (the ticket reserves it for preview, which is KAMP-651;
  // swallowing it now would remove play/pause from a view of a music player for
  // nothing).
  //
  // The arrows still work *inside the rail*, because that is the listbox
  // contract a screen-reader user expects of a role="option" list and the scope
  // is the widget rather than the view.
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    const target = e.target as HTMLElement
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return
    if (e.metaKey || e.ctrlKey || e.altKey) return

    const inRail = railRef.current?.contains(target) ?? false
    const isNext = e.key === '.' || (inRail && e.key === 'ArrowRight')
    const isPrev = e.key === ',' || (inRail && e.key === 'ArrowLeft')

    if (isNext) {
      e.preventDefault()
      e.stopPropagation()
      if (items.length > 0) focusSleeve(Math.min(focusIndex + 1, items.length - 1))
    } else if (isPrev) {
      e.preventDefault()
      e.stopPropagation()
      if (items.length > 0) focusSleeve(Math.max(focusIndex - 1, 0))
    } else if (e.key === 'c' || e.key === 'C') {
      e.preventDefault()
      e.stopPropagation()
      if (current) void copyCrateItemUrl(current)
    } else if (e.key === 'w' || e.key === 'W') {
      e.preventDefault()
      e.stopPropagation()
      // `current` is captured here and never read again in the resolution
      // handler: it is derived during render from a clamped index, and any
      // snapshot push can change its identity while the request is out.
      //
      // Guarded on `purchased` for the same reason the button is disabled — the
      // key would otherwise walk straight past it and re-add an owned record to
      // the wishlist (see the `purchased` note above).
      if (current && !purchased) void toggleCrateWishlist(current)
    } else if (e.key === ' ') {
      // Now that preview exists, Space is the Crate's (KAMP-651). KAMP-650
      // deliberately left it global, because claiming it for a no-op would have
      // removed play/pause from a whole view of a music player.
      e.preventDefault()
      e.stopPropagation()
      togglePreview()
    }
  }

  // App blurs the focused element on any key it handles when focus came from
  // the mouse (KAMP-598), which lands focus on document.body — outside this
  // container, so none of the handlers above would fire again. Click a sleeve,
  // press a global key, and digging went silently dead. Taking focus back the
  // moment it falls to nothing fixes that; focus moving into a dialog names
  // that dialog as relatedTarget, so modals are left alone.
  const onBlurCapture = (e: React.FocusEvent<HTMLDivElement>): void => {
    if (e.relatedTarget !== null || !active) return
    const rail = railRef.current
    const option = rail?.querySelectorAll<HTMLElement>('[role="option"]')[focusIndex]
    option?.focus()
  }

  // Claim focus when the view opens, so , and . work on arrival.
  //
  // The recovery above only fires when focus is LOST; nothing ever claimed it in
  // the first place, so the keys did nothing until you clicked a record. The
  // handlers live on this container by design — listening on document would
  // pre-empt every modal's Escape — so the container has to actually hold focus.
  //
  // preventScroll matters: the view scrolls, the bin sits below the focus card,
  // and a plain focus() scrolls the record into view, yanking the card off the
  // top of the screen the moment you arrive.
  useEffect(() => {
    if (!active || items.length === 0) return
    const rail = railRef.current
    if (!rail) return
    // Never steal focus from something already in use inside the view — a
    // pressed action button, or a record the user just clicked.
    if (rail.contains(document.activeElement)) return
    const option = rail.querySelectorAll<HTMLElement>('[role="option"]')[focusIndex]
    option?.focus({ preventScroll: true })
  }, [active, items.length, focusIndex])

  // ---------------------------------------------------------------------------
  // Compositions
  // ---------------------------------------------------------------------------

  const banner = ((): React.JSX.Element | null => {
    if (pauseRemaining > 0)
      return (
        <div className="crate-banner" role="status">
          The distributor&rsquo;s on the phone — back in {formatPause(pauseRemaining)}.
        </div>
      )
    if (state === 'ready' && crate?.short)
      return (
        <div className="crate-banner" role="status">
          Short crate today — Bandcamp asked us to slow down.
        </div>
      )
    if (crate?.thin && hasCrate)
      return (
        <div className="crate-banner" role="status">
          Nothing on your shelves to go on yet, so this one is what&rsquo;s selling.
        </div>
      )
    return null
  })()

  const digButton = (
    <button
      className="crate-dig-btn"
      onClick={() => void newCrate()}
      disabled={building || pauseRemaining > 0}
    >
      {hasCrate ? 'Dig up another crate' : 'Dig up a crate'}
    </button>
  )

  if (!hasCrate && !building) {
    return (
      <div className="crate-view crate-view--empty">
        {banner}
        <div className="crate-empty">
          <div className="crate-empty-glyph" aria-hidden="true">
            ⬓
          </div>
          {state === 'error' ? (
            <>
              <p className="crate-empty-title">Couldn&rsquo;t get a crate together.</p>
              <p className="crate-empty-hint">
                Bandcamp might be having a moment. Worth another go.
              </p>
            </>
          ) : (
            <>
              <p className="crate-empty-title">Nothing in the crate yet.</p>
              <p className="crate-empty-hint">
                Ten records pulled from the racks, based on what you already play.
              </p>
            </>
          )}
          {digButton}
          {/* Also here: the empty state is its own early return, and a first-run
              user with nothing dug yet should not see a row of zeroes. */}
          {history && history.records > 0 && (
            <p className="crate-history">{describeHistory(history)}</p>
          )}
        </div>
      </div>
    )
  }

  const slots = building ? Math.max(CRATE_SIZE - items.length, 0) : 0

  return (
    <div className="crate-view" onKeyDown={onKeyDown} onBlurCapture={onBlurCapture}>
      {banner}

      <div className={`crate-stage${building ? ' crate-stage--building' : ''}`}>
        {/* The header belongs to the MIDDLE column, not the page: in the spec
            its text starts at the same x as the centre album art, so it reads as
            that column's heading. It carries no artwork of its own any more —
            the large art is the bin's front record, one screen-width away from
            being the same picture twice (KAMP-671). */}
        {current ? (
          <header className="crate-header">
            <div className="crate-header-row">
              <div className="crate-focus-meta">
                <p className="crate-focus-artist">{current.artist}</p>
                <h2 className="crate-focus-title">{current.title}</h2>
                {current.why && (
                  <p className="crate-clerk-card" {...tooltip(TOOLTIPS.CRATE_WHY)}>
                    {current.why}
                  </p>
                )}
                <p className="crate-focus-position">
                  Record {focusIndex + 1} of {items.length}
                  {current.release_date ? ` · ${current.release_date.slice(0, 4)}` : ''}
                </p>
              </div>

              {/* Icons, each with a tooltip through the useTooltip() hook — never
                  a native `title`, which renders an OS tooltip this app does not
                  use. Every glyph here already existed in TransportIcons; none
                  was drawn for this. aria-label carries the same words as the
                  tooltip, because the tooltip is not an accessible name. */}
              <div className="crate-actions">
                <button
                  className="crate-action crate-action--primary"
                  onClick={togglePreview}
                  disabled={previewPreparing}
                  aria-label={previewPlaying ? 'Hold on' : 'Give it a spin'}
                  {...tooltip(TOOLTIPS.CRATE_PREVIEW)}
                >
                  {previewPlaying ? <PauseIcon size={26} /> : <PlayIcon size={26} />}
                </button>
                <button
                  className={`crate-action${wishlisted || purchased ? ' crate-action--done' : ''}`}
                  onClick={() => void toggleCrateWishlist(current)}
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
                  <FavoriteIcon active={wishlisted || purchased} size={18} />
                </button>
                <button
                  className="crate-action"
                  onClick={() => void copyCrateItemUrl(current)}
                  aria-label="Copy link"
                  {...tooltip(TOOLTIPS.CRATE_COPY)}
                >
                  <ShareIcon size={18} />
                </button>
                <button
                  className="crate-action"
                  onClick={() => openOnBandcamp(current)}
                  disabled={!current.item_url}
                  aria-label="Open on Bandcamp"
                  {...tooltip(TOOLTIPS.CRATE_BANDCAMP)}
                >
                  <BandcampIcon size={18} />
                </button>
              </div>
            </div>

            {/* The clerk explains a failed wishlist write here rather than in a
                toast: the retry is that button, so the message belongs beside
                it. Copy link is right there as the fallback every reason ends
                at. Appears rarely and pushes nothing the user is mid-interaction
                with. */}
            {wishlistError && (
              <p className="crate-action-error" role="status">
                {wishlistError}
              </p>
            )}
          </header>
        ) : (
          <header className="crate-header">
            <div className="crate-focus crate-focus--digging">
              <p className="crate-empty-hint">Digging through the racks…</p>
            </div>
          </header>
        )}

        {/* The whole crate at a glance. Only once the bin is up: during a build
            the middle column is the streaming rail across the full width, and a
            list that grows a row at a time beside it is noise. */}
        {!building && (
          <div className="crate-titles-col">
            <CrateTitles
              items={items}
              focusIndex={focusIndex}
              wishlistPending={wishlistPending}
              onFocus={focusSleeve}
              onToggleWishlist={(item) => void toggleCrateWishlist(item)}
            />
          </div>
        )}

        {/* KAMP-656 prototype: the bin renders the same state the flat rail did.
            CrateSleeve and .crate-sleeve are left intact so the Counter fallback
            is a switch back to them here, not a rewrite. While a build streams,
            the flat rail still runs — the empty slots are the fill indicator and
            a half-full bin has no pile to show yet. */}
        {building ? (
          <ul className="crate-rail" role="listbox" aria-label="Crate" ref={railRef} tabIndex={-1}>
            {items.map((item, index) => (
              <CrateSleeve
                key={item.id}
                item={item}
                index={index}
                total={items.length}
                focused={index === focusIndex}
                onFocus={focusSleeve}
              />
            ))}
            {Array.from({ length: slots }, (_unused, i) => (
              <CrateSlot key={`slot-${i}`} index={items.length + i} />
            ))}
          </ul>
        ) : (
          <div className="crate-bin-col">
            <CrateBin
              items={items}
              crateNo={crate?.crate_no ?? null}
              focusIndex={focusIndex}
              awayItemId={awayItemId}
              spineName={spineName}
              railRef={railRef}
              onFocus={focusSleeve}
            />
          </div>
        )}

        {/* The deck (KAMP-668, the right column since KAMP-671). Always here,
            whether or not a record is on it — it is the thing a record gets put
            ON, so it has to exist before one does.

            The platter shows whatever is PLAYING, which is not necessarily the
            focused record — you can keep flipping while something plays, and the
            deck should not change under you when you do. It is also the flight's
            destination rect, which is why it keeps a fixed size rather than one
            that depends on what is on it. */}
        {!building && (
          <div className="crate-deck-col">
            <div className="crate-deck" role="group" aria-label="Preview deck">
              <div
                className={`crate-deck-platter${platterItem ? ' is-loaded' : ''}`}
                ref={deckArtRef}
                aria-hidden="true"
              >
                {platterItem?.art_url && (
                  <img className="crate-deck-art" src={crateArtUrl(platterItem.id)} alt="" />
                )}
              </div>

              <div className="crate-deck-body">
                {deckItem && preview ? (
                  <>
                    <CratePreviewStrip
                      preview={preview}
                      item={deckItem}
                      wishlistSaving={wishlistPending.includes(deckItem.id)}
                      onToggle={togglePreview}
                      onStep={(delta) => void previewAction(delta > 0 ? 'next' : 'prev')}
                      onSeek={(position) => void previewSeek(position)}
                      onToggleWishlist={(item) => void toggleCrateWishlist(item)}
                    />

                    {preview.tracks.length > 0 && (
                      <ol className="crate-tracklist">
                        {preview.tracks.map((track) => (
                          <li key={track.track_num}>
                            <button
                              className={`crate-track${
                                track.track_num === preview.track_num ? ' crate-track--current' : ''
                              }`}
                              onClick={() => void previewPlay(deckItem.id, track.track_num)}
                            >
                              <span className="crate-track-num">{track.track_num}</span>
                              <span className="crate-track-title">
                                {track.title || `Track ${track.track_num}`}
                              </span>
                              <span className="crate-track-time">
                                {formatClock(track.duration)}
                              </span>
                            </button>
                          </li>
                        ))}
                      </ol>
                    )}

                    {preview.error && (
                      <p className="crate-preview-error" role="status">
                        {preview.error === 'rate_limited'
                          ? 'Bandcamp asked us to slow down — try again shortly.'
                          : 'No preview for this one.'}
                      </p>
                    )}
                  </>
                ) : (
                  <p className="crate-deck-empty">
                    Nothing on the deck. Space puts the record you&rsquo;re looking at on — your
                    queue stays where it is.
                  </p>
                )}
              </div>
            </div>
          </div>
        )}

        <div className="crate-footer">
          {digButton}
          {/* The closing beat, at the moment the user has actually just done the
              digging rather than as a running score. Only worth showing for a
              crate with more than one record in it. */}
          {atLastRecord && crateTally && (
            <p className="crate-tally" role="status">
              That&rsquo;s the crate. {describeTally(crateTally)}
            </p>
          )}
          {history && <p className="crate-history">{describeHistory(history)}</p>}
        </div>

        {/* The clerk card lives on the focus card, so arrowing the rail would
            otherwise never announce it: the option's own aria-label and
            aria-posinset already carry identity and position. Announcing only
            the reason keeps this from double-reading every record. */}
        <div className="sr-only" role="status" aria-live="polite">
          {current ? `Record ${focusIndex + 1} of ${items.length}. ${current.why}` : ''}
        </div>
      </div>

      {/* Keyed on the record AND the direction, so previewing a second record
          before the first has landed mounts a fresh traveller for it rather than
          the old one snapping to a new destination mid-flight. */}
      {flight && (
        <RecordFlight
          key={`${flight.id}-${flight.dir}`}
          from={flight.from}
          to={flight.to}
          artUrl={crateArtUrl(flight.id)}
          onDone={() => setFlight((f) => (f === flight ? null : f))}
        />
      )}
    </div>
  )
}
