import React, { useRef, useState } from 'react'

type Props = {
  trackId: number
  title: string
  editMode: boolean
  locked: boolean
  onSave: (trackId: number, title: string) => Promise<void>
}

export function EditableTrackTitle({
  trackId,
  title,
  editMode,
  locked,
  onSave
}: Props): React.JSX.Element {
  const [value, setValue] = useState(title)
  const [prevTitle, setPrevTitle] = useState(title)
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // Sync external title changes (e.g. after refreshOpenAlbum) back into local state.
  // Render-time update avoids the cascading-render issue of useEffect setState.
  if (title !== prevTitle) {
    setPrevTitle(title)
    setValue(title)
  }

  if (!editMode) {
    return <span className="track-row-title">{title}</span>
  }

  if (locked) {
    return (
      <input
        className="track-row-title track-row-title--input"
        value={title}
        disabled
        readOnly
        title="Pause to edit this track"
        aria-label={title}
      />
    )
  }

  const commit = async (): Promise<void> => {
    const trimmed = value.trim()
    if (!trimmed || trimmed === title || saving) return
    setSaving(true)
    try {
      await onSave(trackId, trimmed)
    } finally {
      setSaving(false)
    }
  }

  return (
    <input
      ref={inputRef}
      className={`track-row-title track-row-title--input${saving ? ' saving' : ''}`}
      value={value}
      disabled={saving}
      aria-label={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => void commit()}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          e.stopPropagation()
          inputRef.current?.blur()
        } else if (e.key === 'Escape') {
          e.stopPropagation()
          setValue(title)
          inputRef.current?.blur()
        } else if (e.key === 'Tab') {
          const inputs = Array.from(
            document.querySelectorAll<HTMLInputElement>('.track-row-title--input:not(:disabled)')
          )
          const idx = inputRef.current ? inputs.indexOf(inputRef.current) : -1
          const next = e.shiftKey ? inputs[idx - 1] : inputs[idx + 1]
          if (next) {
            // Prevent the browser landing on the <li tabIndex={0}> between inputs.
            e.preventDefault()
            e.stopPropagation()
            inputRef.current?.blur() // commits current edit
            next.focus()
          }
          // No next/prev input: let Tab fall through and blur naturally commits.
        }
      }}
      // Prevent row double-click from triggering play when clicking the input.
      onDoubleClick={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    />
  )
}
