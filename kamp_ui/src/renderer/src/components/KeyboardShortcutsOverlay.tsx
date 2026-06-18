import React, { useEffect } from 'react'

const isMac = navigator.platform.startsWith('Mac')
const mod = isMac ? 'Cmd' : 'Ctrl'

type Shortcut = {
  keys: string[]
  description: string
  note?: string
}

type Group = {
  label: string
  shortcuts: Shortcut[]
}

const GROUPS: Group[] = [
  {
    label: 'Playback',
    shortcuts: [
      { keys: ['Space'], description: 'Play / Pause' },
      { keys: ['→'], description: 'Next track' },
      { keys: ['←'], description: 'Previous track' }
    ]
  },
  {
    label: 'Navigation',
    shortcuts: [
      { keys: [mod, 'K'], description: 'Focus search' },
      { keys: ['L'], description: 'Library / Now Playing' },
      { keys: ['Q'], description: 'Queue panel' },
      { keys: ['A'], description: 'Artist panel', note: 'Library only' },
      { keys: [mod, ','], description: 'Preferences' }
    ]
  },
  {
    label: 'Queue',
    shortcuts: [
      { keys: ['Alt'], description: 'Toggle album grouping mode' },
      { keys: ['Esc'], description: 'Exit album grouping mode' },
      { keys: ['Shift', 'click'], description: 'Range select in Next Up' }
    ]
  },
  {
    label: 'Help',
    shortcuts: [
      { keys: ['?'], description: 'Show / hide keyboard shortcuts' },
      { keys: ['Esc'], description: 'Close this overlay' }
    ]
  }
]

interface Props {
  onClose: () => void
}

export function KeyboardShortcutsOverlay({ onClose }: Props): React.JSX.Element {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === 'Escape') onClose()
      // Prevent playback shortcuts from firing while overlay is open.
      e.stopPropagation()
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [onClose])

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal shortcuts-overlay"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="shortcuts-title" className="shortcuts-overlay-title">
          Keyboard Shortcuts
        </h2>
        {GROUPS.map((group) => (
          <div key={group.label} className="shortcuts-group">
            <div className="shortcuts-group-label">{group.label}</div>
            {group.shortcuts.map((shortcut) => (
              <div key={shortcut.description} className="shortcuts-row">
                <span className="shortcuts-keys">
                  {shortcut.keys.map((key, i) => (
                    <React.Fragment key={key}>
                      {i > 0 && <span className="shortcuts-plus">+</span>}
                      <kbd>{key}</kbd>
                    </React.Fragment>
                  ))}
                </span>
                <span className="shortcuts-description">
                  {shortcut.description}
                  {shortcut.note && <span className="shortcuts-note">({shortcut.note})</span>}
                </span>
              </div>
            ))}
          </div>
        ))}
        <button className="shortcuts-close-btn" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  )
}
