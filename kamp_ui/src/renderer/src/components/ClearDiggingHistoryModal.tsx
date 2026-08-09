import React, { useEffect, useState } from 'react'

type Props = {
  onConfirm: (forgetSeen: boolean) => void
  onCancel: () => void
}

// Confirmation before erasing the digging history (KAMP-655).
//
// The checkbox is not a "stronger version" of the same wipe — it is a different
// act, and the copy has to say which. Clearing the history zeroes the numbers.
// Forgetting what you have been shown drops the seen ledger, so records already
// offered start coming round again in future crates. A generic "this cannot be
// undone" would hide the only consequence the user cannot predict.
export function ClearDiggingHistoryModal({ onConfirm, onCancel }: Props): React.JSX.Element {
  const [forgetSeen, setForgetSeen] = useState(false)

  // Esc = implicit Cancel, matching every other modal.
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
        aria-labelledby="clear-digging-history-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="clear-digging-history-title" className="modal-title">
          Clear digging history
        </h2>
        <p className="modal-body">
          Erase the record of what you&rsquo;ve dug through, previewed, set aside and brought home?
          The counts go back to zero. This can&rsquo;t be undone.
        </p>
        <label className="modal-body" style={{ display: 'flex', gap: 8, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={forgetSeen}
            onChange={(e) => setForgetSeen(e.target.checked)}
          />
          <span>
            Also forget which records you&rsquo;ve been shown — ones you&rsquo;ve already passed on
            will come round again.
          </span>
        </label>
        <div className="modal-actions">
          <button
            className="modal-btn modal-btn--destructive"
            onClick={() => onConfirm(forgetSeen)}
          >
            Clear
          </button>
          <button className="modal-btn modal-btn--primary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
