import React, { useRef, useState } from 'react'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'

// Generalized inline editor for per-track text fields (title, artist —
// KAMP-582). One shared input class drives both the styling and the Tab
// navigation ring, so Tab walks title → artist → next row in DOM order.
type Props = {
  trackId: number
  value: string
  editMode: boolean
  deferred?: boolean
  /** Outer span class, e.g. 'track-row-title' or 'track-row-artist'. */
  className: string
  onSave: (trackId: number, value: string) => Promise<void>
}

export function EditableTrackField({
  trackId,
  value: fieldValue,
  editMode,
  deferred,
  className,
  onSave
}: Props): React.JSX.Element {
  const [value, setValue] = useState(fieldValue)
  const [prevValue, setPrevValue] = useState(fieldValue)
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const cancelRef = useRef(false)
  const tooltip = useTooltip()

  // Sync external value changes (e.g. after refreshOpenAlbum) back into local state.
  // Render-time update avoids the cascading-render issue of useEffect setState.
  if (fieldValue !== prevValue) {
    setPrevValue(fieldValue)
    setValue(fieldValue)
  }

  const pip = deferred ? (
    <span
      className="deferred-op-pip"
      {...tooltip(TOOLTIPS.META_WILL_REORGANIZE)}
      aria-label="Pending rename"
    />
  ) : null

  if (!editMode) {
    return (
      <span className={className}>
        {fieldValue}
        {pip}
      </span>
    )
  }

  const commit = async (): Promise<void> => {
    if (cancelRef.current) {
      cancelRef.current = false
      return
    }
    const trimmed = value.trim()
    if (!trimmed || trimmed === fieldValue || saving) return
    setSaving(true)
    try {
      await onSave(trackId, trimmed)
    } finally {
      setSaving(false)
    }
  }

  return (
    <span className={`${className} track-row-title--editable`}>
      <input
        ref={inputRef}
        className={`track-row-title--input${saving ? ' saving' : ''}`}
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
            cancelRef.current = true
            setValue(fieldValue)
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
      {pip}
    </span>
  )
}
