import React, { useEffect } from 'react'

type Props = {
  targetPath: string
  onOverwrite: () => void
  onSkip: () => void
  onCancel: () => void
}

export function CollisionModal({
  targetPath,
  onOverwrite,
  onSkip,
  onCancel
}: Props): React.JSX.Element {
  // Esc = implicit Cancel.
  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onCancel])

  const filename = targetPath.split(/[/\\]/).pop() ?? targetPath

  return (
    // Click-away backdrop = implicit Cancel.
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal collision-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="collision-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="collision-modal-title" className="modal-title">
          File already exists
        </h2>
        <p className="modal-body">
          <strong>{filename}</strong> already exists at the target location. What would you like to
          do?
        </p>
        <div className="modal-actions">
          <button className="modal-btn modal-btn--destructive" onClick={onOverwrite}>
            Overwrite
          </button>
          <button className="modal-btn" onClick={onSkip}>
            Skip
          </button>
          <button className="modal-btn modal-btn--primary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
