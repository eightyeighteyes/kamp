// The Crate (KAMP-650) — a crate of ten records to dig through.
//
// User-facing name is "The Crate"; `discovery` stays the code/API namespace.
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
import type { CrateItem } from '../api/client'
import { CrateSleeve, CrateSlot } from './CrateSleeve'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'

const CRATE_SIZE = 10
// How long a passed record can be brought back. The dismiss POST is held for
// this long rather than sent and reversed: 'dismissed' is terminal server-side.
const UNDO_MS = 5000

function formatPause(seconds: number): string {
  const total = Math.ceil(seconds)
  const mins = Math.floor(total / 60)
  const secs = total % 60
  return mins > 0 ? `${mins}:${String(secs).padStart(2, '0')}` : `${secs}s`
}

export function CrateView({ active = false }: { active?: boolean }): React.JSX.Element {
  const crate = useStore((s) => s.crate)
  const pendingDismissals = useStore((s) => s.crateDismissPending)
  const newCrate = useStore((s) => s.newCrate)
  const deferCrateDismiss = useStore((s) => s.deferCrateDismiss)
  const undoCrateDismiss = useStore((s) => s.undoCrateDismiss)
  const commitCrateDismiss = useStore((s) => s.commitCrateDismiss)
  const flushCrateDismissals = useStore((s) => s.flushCrateDismissals)
  const copyCrateItemUrl = useStore((s) => s.copyCrateItemUrl)
  const setActiveView = useStore((s) => s.setActiveView)
  const tooltip = useTooltip()

  const [focused, setFocused] = useState(0)
  const [undoItem, setUndoItem] = useState<CrateItem | null>(null)
  const undoTimerRef = useRef<number | null>(null)
  const railRef = useRef<HTMLUListElement | null>(null)
  const [pauseRemaining, setPauseRemaining] = useState(0)

  const items = useMemo(() => crate?.items ?? [], [crate])
  const state = crate?.state ?? 'idle'
  const building = state === 'building'
  // Never derived from `filled` outside a live build: the daemon's status is
  // in-memory, so a restored crate reports the builder's last count. The
  // snapshot derives both from the stored rows when idle (KAMP-650).
  const visible = items.filter((item) => !pendingDismissals.includes(item.id))
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
  // while a build streams and shrinks as records are passed, so the stored index
  // can briefly point past the end. Deriving avoids a re-render round trip and
  // the intermediate frame where the focus card would be blank.
  const focusIndex = visible.length === 0 ? 0 : Math.min(focused, visible.length - 1)
  const current = visible[focusIndex] ?? null

  // Any held dismiss must be sent before this view stops being able to send it.
  // Without this, passing a record and immediately switching views loses it.
  useEffect(() => {
    if (active) return
    void flushCrateDismissals()
  }, [active, flushCrateDismissals])
  useEffect(() => {
    return () => {
      void useStore.getState().flushCrateDismissals()
    }
  }, [])

  const clearUndoTimer = useCallback((): void => {
    if (undoTimerRef.current !== null) {
      window.clearTimeout(undoTimerRef.current)
      undoTimerRef.current = null
    }
  }, [])

  const pass = useCallback(
    (item: CrateItem): void => {
      // Commit whatever was already waiting — only one Undo is offered at a time,
      // and the previous one's window is over the moment a new pass happens.
      if (undoItem && undoItem.id !== item.id) void commitCrateDismiss(undoItem.id)
      clearUndoTimer()
      deferCrateDismiss(item.id)
      setUndoItem(item)
      undoTimerRef.current = window.setTimeout(() => {
        undoTimerRef.current = null
        setUndoItem(null)
        void commitCrateDismiss(item.id)
      }, UNDO_MS)
    },
    [clearUndoTimer, commitCrateDismiss, deferCrateDismiss, undoItem]
  )

  const undo = useCallback((): void => {
    if (!undoItem) return
    clearUndoTimer()
    undoCrateDismiss(undoItem.id)
    setUndoItem(null)
  }, [clearUndoTimer, undoCrateDismiss, undoItem])

  // Preview ("Give it a spin") is KAMP-651 — there is no preview route yet — so
  // the primary action is the honest one available now: the album's own page.
  const openOnBandcamp = useCallback((item: CrateItem): void => {
    if (item.item_url) window.api.openExternal(item.item_url)
  }, [])

  const focusSleeve = useCallback((index: number): void => {
    setFocused(index)
    // Move real DOM focus with the selection so the roving tabindex stays
    // coherent and the container keeps receiving keys.
    const rail = railRef.current
    const option = rail?.querySelectorAll<HTMLElement>('[role="option"]')[index]
    option?.focus()
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
      const prev = useStore.getState().previousView
      void setActiveView(prev && prev !== 'crate' ? prev : 'library')
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [active, setActiveView])

  // The crate's own keys live on the view container, NOT on document. App's
  // global handler is a window listener and the React root sits below document
  // in the bubble path, so stopPropagation here wins for Left/Right/W/X/C. A
  // document listener would also win — and would pre-empt the Escape handling of
  // every modal opened afterwards, since those are document listeners too and
  // registration order decides.
  //
  // Space is deliberately NOT claimed. The ticket reserves it for preview, but
  // preview lands in KAMP-651; swallowing it now would remove play/pause from a
  // whole view of a music player for no gain.
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    const tag = (e.target as HTMLElement).tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA') return
    if (e.metaKey || e.ctrlKey || e.altKey) return

    if (e.key === 'ArrowRight') {
      e.preventDefault()
      e.stopPropagation()
      if (visible.length > 0) focusSleeve(Math.min(focusIndex + 1, visible.length - 1))
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      e.stopPropagation()
      if (visible.length > 0) focusSleeve(Math.max(focusIndex - 1, 0))
    } else if (e.key === 'x' || e.key === 'X') {
      e.preventDefault()
      e.stopPropagation()
      if (current) pass(current)
    } else if (e.key === 'c' || e.key === 'C') {
      e.preventDefault()
      e.stopPropagation()
      if (current) void copyCrateItemUrl(current)
    } else if (e.key === 'w' || e.key === 'W') {
      // Claimed so it does not fall through to a global shortcut, but wishlist
      // itself is unavailable until KAMP-653 extends the Electron relay.
      e.preventDefault()
      e.stopPropagation()
    }
  }

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
        </div>
      </div>
    )
  }

  const slots = building ? Math.max(CRATE_SIZE - visible.length, 0) : 0

  return (
    <div className="crate-view" onKeyDown={onKeyDown}>
      {banner}

      <div className="crate-stage">
        {current ? (
          <article className="crate-focus" aria-live="polite">
            <div className={`crate-focus-art${current.art_url ? ' has-art' : ''}`}>
              {current.art_url && (
                <img
                  className="crate-focus-art-img"
                  src={crateArtUrl(current.id)}
                  alt=""
                  key={current.id}
                />
              )}
            </div>
            <div className="crate-focus-meta">
              <p className="crate-focus-artist">{current.artist}</p>
              <h2 className="crate-focus-title">{current.title}</h2>
              {current.why && (
                <p className="crate-clerk-card" {...tooltip(TOOLTIPS.CRATE_WHY)}>
                  {current.why}
                </p>
              )}
              <p className="crate-focus-position">
                Record {focusIndex + 1} of {visible.length}
                {current.release_date ? ` · ${current.release_date.slice(0, 4)}` : ''}
              </p>
              <div className="crate-actions">
                <button
                  className="crate-action crate-action--primary"
                  onClick={() => openOnBandcamp(current)}
                  disabled={!current.item_url}
                >
                  Open on Bandcamp
                </button>
                <button
                  className="crate-action"
                  disabled
                  {...tooltip(TOOLTIPS.CRATE_WISHLIST_SOON)}
                >
                  Wishlist it
                </button>
                <button
                  className="crate-action"
                  onClick={() => void copyCrateItemUrl(current)}
                  {...tooltip(TOOLTIPS.CRATE_COPY)}
                >
                  Copy link
                </button>
                <button
                  className="crate-action"
                  onClick={() => pass(current)}
                  {...tooltip(TOOLTIPS.CRATE_PASS)}
                >
                  Pass
                </button>
              </div>
            </div>
          </article>
        ) : (
          <div className="crate-focus crate-focus--digging">
            <p className="crate-empty-hint">Digging through the racks…</p>
          </div>
        )}

        <ul
          className="crate-rail"
          role="listbox"
          aria-label="The Crate"
          ref={railRef}
          tabIndex={-1}
        >
          {visible.map((item, index) => (
            <CrateSleeve
              key={item.id}
              item={item}
              index={index}
              total={visible.length}
              focused={index === focusIndex}
              passed={pendingDismissals.includes(item.id)}
              onFocus={focusSleeve}
            />
          ))}
          {Array.from({ length: slots }, (_unused, i) => (
            <CrateSlot key={`slot-${i}`} index={visible.length + i} />
          ))}
        </ul>

        <div className="crate-footer">{digButton}</div>
      </div>

      {undoItem && (
        <div className="crate-undo-toast" role="status">
          <span className="crate-undo-text">Passed on {undoItem.title}.</span>
          <button className="crate-undo-btn" onClick={undo}>
            Undo
          </button>
          <div className="crate-undo-bar" style={{ animationDuration: `${UNDO_MS}ms` }} />
        </div>
      )}
    </div>
  )
}
