import React, { useEffect, useRef, useState } from 'react'
import type { Album, ItunesArtCandidate } from '../api/client'
import { applyAlbumArt, searchAlbumArt } from '../api/client'
import '../assets/itunes-art-modal.css'

type ModalState =
  | { kind: 'searching' }
  | { kind: 'no_results' }
  | { kind: 'error'; message: string }
  | { kind: 'results'; candidates: ItunesArtCandidate[]; selectedIndex: number | null }
  | { kind: 'confirming'; candidates: ItunesArtCandidate[]; selectedIndex: number }
  | { kind: 'applying'; candidates: ItunesArtCandidate[]; selectedIndex: number }
  | {
      kind: 'apply_error'
      candidates: ItunesArtCandidate[]
      selectedIndex: number
      message: string
    }

type Props = {
  albumArtist: string
  album: string
  hasExistingArt: boolean
  onClose: () => void
  onApplied: (updatedAlbum: Album) => void
}

function resolveArtUrl(template: string, size: string): string {
  return template.replace('{size}', size)
}

function ArtThumbnail({
  candidate,
  selected,
  onClick
}: {
  candidate: ItunesArtCandidate
  selected: boolean
  onClick: () => void
}): React.JSX.Element {
  const [imgState, setImgState] = useState<'loading' | 'loaded' | 'error'>('loading')

  return (
    <button
      className={`art-thumb${selected ? ' art-thumb--selected' : ''}`}
      type="button"
      onClick={onClick}
      title={`${candidate.artist} — ${candidate.title}`}
    >
      <div className="art-thumb__img-wrap">
        {imgState === 'loading' && <div className="art-thumb__skeleton" />}
        {imgState === 'error' && (
          <div className="art-thumb__broken" aria-label="Image unavailable">
            ?
          </div>
        )}
        <img
          className={`art-thumb__img${imgState === 'loaded' ? ' art-thumb__img--loaded' : ''}`}
          src={candidate.preview_url}
          alt={`${candidate.artist} — ${candidate.title}`}
          onLoad={() => setImgState('loaded')}
          onError={() => setImgState('error')}
        />
      </div>
      <p className="art-thumb__artist">{candidate.artist}</p>
      <p className="art-thumb__title">{candidate.title}</p>
    </button>
  )
}

export function ItunesArtModal({
  albumArtist,
  album,
  hasExistingArt,
  onClose,
  onApplied
}: Props): React.JSX.Element {
  const [state, setState] = useState<ModalState>({ kind: 'searching' })
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    abortRef.current = controller

    searchAlbumArt(albumArtist, album, controller.signal)
      .then((candidates) => {
        if (controller.signal.aborted) return
        if (candidates.length === 0) {
          setState({ kind: 'no_results' })
        } else if (candidates.length === 1) {
          setState({ kind: 'confirming', candidates, selectedIndex: 0 })
        } else {
          setState({ kind: 'results', candidates, selectedIndex: null })
        }
      })
      .catch((err: Error) => {
        if (controller.signal.aborted) return
        setState({ kind: 'error', message: err.message })
      })

    return () => controller.abort()
  }, [albumArtist, album])

  const handleApply = async (
    candidates: ItunesArtCandidate[],
    selectedIndex: number
  ): Promise<void> => {
    setState({ kind: 'applying', candidates, selectedIndex })
    const candidate = candidates[selectedIndex]
    const artworkUrl = resolveArtUrl(candidate.artwork_url_template, '600x600bb')
    try {
      const updatedAlbum = await applyAlbumArt(albumArtist, album, artworkUrl)
      onApplied(updatedAlbum)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Apply failed'
      setState({ kind: 'apply_error', candidates, selectedIndex, message })
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent): void => {
    if (e.key === 'Escape') onClose()
  }

  const resultCount =
    state.kind === 'results' ||
    state.kind === 'confirming' ||
    state.kind === 'applying' ||
    state.kind === 'apply_error'
      ? state.candidates.length
      : null

  const countLabel =
    resultCount != null ? `${resultCount} result${resultCount === 1 ? '' : 's'}` : ''

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose} onKeyDown={handleKeyDown}>
      <div
        className="modal art-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="art-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="mb-modal__header">
          <h2 id="art-modal-title" className="mb-modal__title">
            Album Art — {albumArtist} · {album}
          </h2>
          <div className="mb-modal__header-right">
            {countLabel && <span className="art-modal__count">{countLabel}</span>}
            <button
              className="mb-modal__close-btn"
              type="button"
              aria-label="Close"
              onClick={onClose}
            >
              ✕
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="mb-modal__body art-modal__body">
          {state.kind === 'searching' && (
            <div className="art-modal__empty">
              <p className="art-modal__empty-msg">Searching…</p>
            </div>
          )}

          {state.kind === 'no_results' && (
            <div className="art-modal__empty">
              <div className="art-modal__empty-icon" aria-hidden="true">
                ◎
              </div>
              <p className="art-modal__empty-msg">No album art found</p>
            </div>
          )}

          {state.kind === 'error' && (
            <div className="art-modal__empty">
              <div className="art-modal__empty-icon" aria-hidden="true">
                ◎
              </div>
              <p className="art-modal__empty-msg">Could not reach album art provider</p>
            </div>
          )}

          {state.kind === 'results' && (
            <div className="art-modal__grid">
              {state.candidates.map((c, i) => (
                <ArtThumbnail
                  key={i}
                  candidate={c}
                  selected={state.selectedIndex === i}
                  onClick={() =>
                    setState({ kind: 'results', candidates: state.candidates, selectedIndex: i })
                  }
                />
              ))}
            </div>
          )}

          {(state.kind === 'confirming' ||
            state.kind === 'applying' ||
            state.kind === 'apply_error') && (
            <div className="art-modal__confirm">
              {state.candidates.length === 1 ? (
                <img
                  className="art-modal__confirm-img"
                  src={state.candidates[state.selectedIndex].preview_url}
                  alt={state.candidates[state.selectedIndex].title}
                />
              ) : (
                <img
                  className="art-modal__confirm-img"
                  src={state.candidates[state.selectedIndex].preview_url}
                  alt={state.candidates[state.selectedIndex].title}
                />
              )}
              <div className="art-modal__confirm-meta">
                <p className="art-modal__confirm-artist">
                  {state.candidates[state.selectedIndex].artist}
                </p>
                <p className="art-modal__confirm-title">
                  {state.candidates[state.selectedIndex].title}
                </p>
              </div>
              {state.kind === 'apply_error' && (
                <p className="art-modal__apply-error">{state.message}</p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="mb-modal__footer">
          {hasExistingArt && (state.kind === 'confirming' || state.kind === 'results') && (
            <span className="art-modal__replace-note">Replaces existing art</span>
          )}
          <div className="art-modal__footer-actions">
            {state.kind === 'no_results' || state.kind === 'error' ? (
              <button
                className="mb-modal__btn mb-modal__btn--ghost"
                type="button"
                onClick={onClose}
              >
                Close
              </button>
            ) : (
              <>
                {state.kind === 'confirming' && state.candidates.length > 1 && (
                  <button
                    className="mb-modal__btn mb-modal__btn--ghost"
                    type="button"
                    onClick={() =>
                      setState({
                        kind: 'results',
                        candidates: state.candidates,
                        selectedIndex: state.selectedIndex
                      })
                    }
                  >
                    Back
                  </button>
                )}
                {state.kind !== 'confirming' &&
                  state.kind !== 'applying' &&
                  state.kind !== 'apply_error' && (
                    <button
                      className="mb-modal__btn mb-modal__btn--ghost"
                      type="button"
                      onClick={onClose}
                    >
                      Cancel
                    </button>
                  )}
                {state.kind === 'apply_error' && (
                  <>
                    <button
                      className="mb-modal__btn mb-modal__btn--ghost"
                      type="button"
                      onClick={() =>
                        setState({
                          kind: state.candidates.length > 1 ? 'results' : 'confirming',
                          candidates: state.candidates,
                          selectedIndex: state.selectedIndex
                        })
                      }
                    >
                      Back
                    </button>
                    <button
                      className="mb-modal__btn mb-modal__btn--accent"
                      type="button"
                      onClick={() => void handleApply(state.candidates, state.selectedIndex)}
                    >
                      Retry
                    </button>
                  </>
                )}
                {state.kind === 'results' && (
                  <button
                    className="mb-modal__btn mb-modal__btn--accent"
                    type="button"
                    disabled={state.selectedIndex === null}
                    onClick={() => {
                      if (state.selectedIndex !== null)
                        setState({
                          kind: 'confirming',
                          candidates: state.candidates,
                          selectedIndex: state.selectedIndex
                        })
                    }}
                  >
                    Apply selected
                  </button>
                )}
                {state.kind === 'confirming' && (
                  <button
                    className="mb-modal__btn mb-modal__btn--accent"
                    type="button"
                    onClick={() => void handleApply(state.candidates, state.selectedIndex)}
                  >
                    Apply
                  </button>
                )}
                {state.kind === 'applying' && (
                  <button className="mb-modal__btn mb-modal__btn--accent" type="button" disabled>
                    Applying…
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
