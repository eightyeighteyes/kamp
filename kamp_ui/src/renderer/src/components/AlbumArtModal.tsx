import React, { useEffect, useRef, useState } from 'react'
import type { Album, ItunesArtCandidate } from '../api/client'
import { useTooltip } from '../hooks/useTooltip'
import { applyAlbumArt, applyAlbumArtLocal, searchAlbumArt } from '../api/client'
import '../assets/album-art-modal.css'

type ItunesState =
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

type LocalState =
  | { kind: 'local_confirming'; file: File }
  | { kind: 'local_applying'; file: File }
  | { kind: 'local_apply_error'; file: File; message: string }

type ModalState = ItunesState | LocalState

function isLocalState(s: ModalState): s is LocalState {
  return (
    s.kind === 'local_confirming' || s.kind === 'local_applying' || s.kind === 'local_apply_error'
  )
}

type Props = {
  albumArtist: string
  album: string
  hasExistingArt: boolean
  onClose: () => void
  onApplied: (updatedAlbum: Album) => void
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
  const tooltip = useTooltip()

  return (
    <button
      className={`art-thumb${selected ? ' art-thumb--selected' : ''}`}
      type="button"
      onClick={onClick}
      {...tooltip(`${candidate.artist} — ${candidate.title}`)}
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

export function AlbumArtModal({
  albumArtist,
  album,
  hasExistingArt,
  onClose,
  onApplied
}: Props): React.JSX.Element {
  const [state, setState] = useState<ModalState>({ kind: 'searching' })
  const abortRef = useRef<AbortController | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const previewImgRef = useRef<HTMLImageElement>(null)
  // Track the previous iTunes state so "Back" from local_confirming restores it.
  const prevItunesStateRef = useRef<ItunesState>({ kind: 'searching' })

  useEffect(() => {
    const controller = new AbortController()
    abortRef.current = controller

    searchAlbumArt(albumArtist, album, controller.signal)
      .then((candidates) => {
        if (controller.signal.aborted) return
        let next: ItunesState
        if (candidates.length === 0) {
          next = { kind: 'no_results' }
        } else if (candidates.length === 1) {
          next = { kind: 'confirming', candidates, selectedIndex: 0 }
        } else {
          next = { kind: 'results', candidates, selectedIndex: null }
        }
        prevItunesStateRef.current = next
        setState(next)
      })
      .catch((err: Error) => {
        if (controller.signal.aborted) return
        const next: ItunesState = { kind: 'error', message: err.message }
        prevItunesStateRef.current = next
        setState(next)
      })

    return () => controller.abort()
  }, [albumArtist, album])

  // Create a blob URL for local file preview, set it imperatively on the img element,
  // and revoke it on cleanup. Using setAttribute (not a JSX src prop) keeps the
  // user-controlled File object out of the JSX attribute data flow.
  const localFile = isLocalState(state) ? state.file : null
  useEffect(() => {
    const img = previewImgRef.current
    if (!localFile) return
    const url = URL.createObjectURL(localFile)
    img?.setAttribute('src', url)
    return () => {
      img?.removeAttribute('src')
      URL.revokeObjectURL(url)
    }
  }, [localFile])

  const handleApply = async (
    candidates: ItunesArtCandidate[],
    selectedIndex: number
  ): Promise<void> => {
    setState({ kind: 'applying', candidates, selectedIndex })
    const candidate = candidates[selectedIndex]
    try {
      const updatedAlbum = await applyAlbumArt(albumArtist, album, candidate.artwork_url_template)
      onApplied(updatedAlbum)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Apply failed'
      setState({ kind: 'apply_error', candidates, selectedIndex, message })
    }
  }

  const handleApplyLocal = async (file: File): Promise<void> => {
    setState({ kind: 'local_applying', file })
    try {
      const updatedAlbum = await applyAlbumArtLocal(albumArtist, album, file)
      onApplied(updatedAlbum)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Apply failed'
      setState({ kind: 'local_apply_error', file, message })
    }
  }

  const handleFileChosen = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const file = e.target.files?.[0]
    if (!file) return
    // Save current iTunes state so Back can restore it.
    if (!isLocalState(state)) {
      prevItunesStateRef.current = state
    }
    setState({ kind: 'local_confirming', file })
    // Reset input value so the same file can be re-selected after cancelling.
    e.target.value = ''
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

  const isApplying = state.kind === 'applying' || state.kind === 'local_applying'

  const covUrl = `https://covers.musichoarders.xyz/?sources=applemusic,qobuz,spotify&artist=${encodeURIComponent(albumArtist)}&album=${encodeURIComponent(album)}`

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
              <img
                className="art-modal__confirm-img"
                src={state.candidates[state.selectedIndex].artwork_url_template.replace(
                  '{size}',
                  '600x600bb'
                )}
                alt={state.candidates[state.selectedIndex].title}
              />
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

          {(state.kind === 'local_confirming' ||
            state.kind === 'local_applying' ||
            state.kind === 'local_apply_error') && (
            <div className="art-modal__confirm">
              <img
                ref={previewImgRef}
                className="art-modal__confirm-img art-modal__confirm-img--local"
                alt={state.file.name}
              />
              <div className="art-modal__confirm-meta">
                <p className="art-modal__confirm-title">{state.file.name}</p>
              </div>
              {state.kind === 'local_apply_error' && (
                <p className="art-modal__apply-error">{state.message}</p>
              )}
            </div>
          )}

          {hasExistingArt &&
            (state.kind === 'confirming' ||
              state.kind === 'results' ||
              state.kind === 'local_confirming') && (
              <span className="art-modal__replace-note">Replaces existing art</span>
            )}
        </div>

        {/* Footer */}
        <div className="mb-modal__footer">
          {/* Hidden native file input — triggered by the Choose File button */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="art-modal__file-input"
            onChange={handleFileChosen}
          />
          {!isApplying && (
            <button
              className="mb-modal__btn mb-modal__btn--ghost art-modal__choose-file-btn"
              type="button"
              onClick={() => fileInputRef.current?.click()}
            >
              Choose file…
            </button>
          )}

          {!isApplying && (
            <a className="art-modal__cov-link" href={covUrl} target="_blank" rel="noreferrer">
              Search for album art at covers.musichoarders.xyz
            </a>
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
                {(state.kind === 'local_confirming' || state.kind === 'local_apply_error') && (
                  <button
                    className="mb-modal__btn mb-modal__btn--ghost"
                    type="button"
                    onClick={() => setState(prevItunesStateRef.current)}
                  >
                    Back
                  </button>
                )}
                {state.kind !== 'confirming' &&
                  state.kind !== 'applying' &&
                  state.kind !== 'apply_error' &&
                  !isLocalState(state) && (
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
                    Select
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
                {state.kind === 'local_confirming' && (
                  <button
                    className="mb-modal__btn mb-modal__btn--accent"
                    type="button"
                    onClick={() => void handleApplyLocal(state.file)}
                  >
                    Apply
                  </button>
                )}
                {state.kind === 'local_applying' && (
                  <button className="mb-modal__btn mb-modal__btn--accent" type="button" disabled>
                    Applying…
                  </button>
                )}
                {state.kind === 'local_apply_error' && (
                  <>
                    <button
                      className="mb-modal__btn mb-modal__btn--accent"
                      type="button"
                      onClick={() => void handleApplyLocal(state.file)}
                    >
                      Retry
                    </button>
                  </>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
