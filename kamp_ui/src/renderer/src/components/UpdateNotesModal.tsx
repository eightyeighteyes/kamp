import React from 'react'
import ReactMarkdown from 'react-markdown'

type Props = {
  version: string
  notes: string
  onClose: () => void
  onDismiss: () => void
}

export function UpdateNotesModal({ version, notes, onClose, onDismiss }: Props): React.JSX.Element {
  const handleGotIt = (): void => {
    onDismiss()
    onClose()
  }

  const openSite = (e: React.MouseEvent): void => {
    e.preventDefault()
    window.open('https://kamp.fm', '_blank')
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal update-notes-modal" onClick={(e) => e.stopPropagation()}>
        <div className="update-notes-modal__header">
          <span className="update-notes-modal__title">What&rsquo;s new in Kamp {version}</span>
        </div>
        <div className="update-notes-modal__body">
          <ReactMarkdown
            components={{
              a: ({ href, children }) => (
                <a
                  href={href}
                  onClick={(e) => {
                    e.preventDefault()
                    if (href) window.open(href, '_blank')
                  }}
                >
                  {children}
                </a>
              )
            }}
          >
            {notes}
          </ReactMarkdown>
        </div>
        <div className="update-notes-modal__footer">
          <a href="https://kamp.fm" className="update-notes-modal__site-link" onClick={openSite}>
            kamp.fm
          </a>
          <button className="update-notes-modal__btn" onClick={handleGotIt}>
            Got it
          </button>
        </div>
      </div>
    </div>
  )
}
