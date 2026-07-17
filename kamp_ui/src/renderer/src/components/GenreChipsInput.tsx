import React, { useMemo, useRef, useState } from 'react'

// Multi-value genre editor (KAMP-586): removable chips + type-ahead autocomplete
// sourced from genres already in the library. Genres may contain spaces
// ("Free Jazz"). Case-insensitive dedup. Commits the full list on blur (leaving
// the component), matching the album panel's save-on-blur pattern.
type Props = {
  chips: string[]
  suggestions: string[]
  editMode: boolean
  onCommit: (genres: string[]) => void
}

function sameSet(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  const sa = new Set(a.map((x) => x.toLowerCase()))
  return b.every((x) => sa.has(x.toLowerCase()))
}

export function GenreChipsInput({
  chips: initial,
  suggestions,
  editMode,
  onCommit
}: Props): React.JSX.Element {
  const [chips, setChips] = useState(initial)
  const [input, setInput] = useState('')
  const [open, setOpen] = useState(false)
  const [initialChips, setInitialChips] = useState(initial)
  const containerRef = useRef<HTMLDivElement>(null)

  // Re-sync when the external value changes (e.g. after a save round-trips a new
  // track list). Render-phase update avoids the cascading-render effect pattern.
  if (!sameSet(initialChips, initial)) {
    setInitialChips(initial)
    setChips(initial)
  }

  const addChip = (raw: string): void => {
    const name = raw.trim()
    setInput('')
    if (!name || chips.some((c) => c.toLowerCase() === name.toLowerCase())) return
    setChips([...chips, name])
  }

  const removeChip = (name: string): void => setChips(chips.filter((c) => c !== name))

  const commit = (): void => {
    if (!sameSet(chips, initialChips)) onCommit(chips)
  }

  const filtered = useMemo(() => {
    const q = input.trim().toLowerCase()
    const have = new Set(chips.map((c) => c.toLowerCase()))
    return suggestions
      .filter((s) => !have.has(s.toLowerCase()) && (!q || s.toLowerCase().includes(q)))
      .slice(0, 8)
  }, [input, suggestions, chips])

  if (!editMode) {
    if (chips.length === 0) return <span className="meta-field--empty">—</span>
    return (
      <span className="genre-chips genre-chips--readonly">
        {chips.map((c) => (
          <span key={c} className="genre-chip">
            {c}
          </span>
        ))}
      </span>
    )
  }

  return (
    <div
      ref={containerRef}
      className="genre-chips genre-chips--editable"
      onBlur={(e) => {
        // Only when focus truly leaves the component (not moving between chip
        // buttons / the input / a suggestion).
        if (!containerRef.current?.contains(e.relatedTarget as Node | null)) {
          setOpen(false)
          commit()
        }
      }}
    >
      {chips.map((c) => (
        <span key={c} className="genre-chip">
          {c}
          <button
            type="button"
            className="genre-chip-remove"
            aria-label={`Remove ${c}`}
            onClick={() => removeChip(c)}
          >
            ×
          </button>
        </span>
      ))}
      <div className="genre-chips-input-wrap">
        <input
          className="genre-chips-input"
          value={input}
          placeholder={chips.length ? '' : 'Add genre…'}
          aria-label="Add genre"
          onChange={(e) => {
            setInput(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault()
              addChip(input)
            } else if (e.key === 'Backspace' && !input && chips.length) {
              removeChip(chips[chips.length - 1])
            } else if (e.key === 'Escape') {
              setOpen(false)
            }
          }}
        />
        {open && filtered.length > 0 && (
          <ul className="genre-autocomplete" role="listbox">
            {filtered.map((s) => (
              <li key={s} role="option" aria-selected={false}>
                <button
                  type="button"
                  className="genre-autocomplete-item"
                  // onMouseDown fires before the input's blur, so the chip is
                  // added without the blur committing/closing first.
                  onMouseDown={(e) => {
                    e.preventDefault()
                    addChip(s)
                  }}
                >
                  {s}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
