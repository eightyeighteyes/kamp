import React, { useEffect } from 'react'
import { truncateTitle } from '../utils/truncateTitle'

type Props = {
  playlistName: string
  hasMixed: boolean
  onAddAll: () => void
  onAddUnique: () => void
  onCancel: () => void
}

export function DuplicatePlaylistTrackModal({
  playlistName,
  hasMixed,
  onAddAll,
  onAddUnique,
  onCancel
}: Props): React.JSX.Element {
  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onCancel])

  const displayName = truncateTitle(playlistName, 40)

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal collision-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="duplicate-playlist-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="duplicate-playlist-modal-title" className="modal-title">
          Already in playlist
        </h2>
        <p className="modal-body">
          hey! some of these songs are already in the playlist <strong>{displayName}</strong>. are
          you sure you want to add them again?
        </p>
        <div className="modal-actions">
          {hasMixed && (
            <button className="modal-btn" onClick={onAddUnique}>
              just the unique songs
            </button>
          )}
          <button className="modal-btn modal-btn--primary" onClick={onAddAll}>
            yeah, i&apos;m sure
          </button>
          <button className="modal-btn" onClick={onCancel}>
            whoops, no
          </button>
        </div>
      </div>
    </div>
  )
}
