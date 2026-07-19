import React, { useEffect } from 'react'

type Props = {
  genre: string
  onConfirm: () => void
  onCancel: () => void
}

// Confirmation before a destructive, library-wide genre removal (KAMP-606):
// it strips the genre from every tagged track's DB row and audio-file tag.
export function RemoveGenreModal({ genre, onConfirm, onCancel }: Props): React.JSX.Element {
  // Esc = implicit Cancel.
  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onCancel])

  return (
    // Click-away backdrop = implicit Cancel.
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal collision-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="remove-genre-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="remove-genre-modal-title" className="modal-title">
          Remove genre
        </h2>
        <p className="modal-body">
          Remove <strong>{genre}</strong> from your collection? This strips it from every tagged
          track and edits those files&rsquo; tags. This can&rsquo;t be undone.
        </p>
        <div className="modal-actions">
          <button className="modal-btn modal-btn--destructive" onClick={onConfirm}>
            Remove
          </button>
          <button className="modal-btn modal-btn--primary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
