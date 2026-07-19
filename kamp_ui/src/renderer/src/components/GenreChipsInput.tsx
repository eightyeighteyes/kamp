import React, { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTooltip } from '../hooks/useTooltip'

// Multi-value genre editor (KAMP-586): removable chips + type-ahead autocomplete
// sourced from genres already in the library. Genres may contain spaces
// ("Free Jazz"). Case-insensitive dedup. Commits the full list on blur (leaving
// the component), matching the album panel's save-on-blur pattern.
type Props = {
  chips: string[]
  suggestions: string[]
  editMode: boolean
  onCommit: (genres: string[]) => void
  // KAMP-611: when provided, read-only chips become clickable and navigate to
  // that genre's filter. Ignored in edit mode (chips stay inert there).
  onGenreClick?: (genre: string) => void
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
  onCommit,
  onGenreClick
}: Props): React.JSX.Element {
  const tooltip = useTooltip()
  const [chips, setChips] = useState(initial)
  const [input, setInput] = useState('')
  const [open, setOpen] = useState(false)
  // -1 = nothing highlighted (Enter adds the typed text instead of a suggestion).
  const [active, setActive] = useState(-1)
  const [initialChips, setInitialChips] = useState(initial)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Re-sync when the external value changes (e.g. after a save round-trips a new
  // track list). Render-phase update avoids the cascading-render effect pattern.
  if (!sameSet(initialChips, initial)) {
    setInitialChips(initial)
    setChips(initial)
  }

  const addChip = (raw: string): void => {
    const name = raw.trim()
    setInput('')
    setActive(-1)
    if (!name || chips.some((c) => c.toLowerCase() === name.toLowerCase())) return
    setChips([...chips, name])
  }

  const removeChip = (name: string): void => {
    const next = chips.filter((c) => c !== name)
    setChips(next)
    // Persist removals immediately instead of waiting for the container's blur.
    // Clicking a chip's × can leave focus outside the input (on macOS a button
    // click doesn't focus it), so the focusout that normally commits never
    // fires when the user then navigates away — the removal was silently
    // dropped and the genre "healed" on reopen (KAMP-550). Adds keep focus in
    // the input, so their blur-commit already works.
    if (!sameSet(next, initialChips)) onCommit(next)
  }

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

  const showMenu = open && filtered.length > 0

  // The dropdown is portaled to document.body so it can't be clipped or painted
  // over by the surrounding track rows (they establish their own stacking
  // context). Fixed-position it under the input, tracking scroll/resize while
  // open. (Same escape-the-stacking-context approach as ContextMenu.)
  const [menuPos, setMenuPos] = useState<{ top: number; left: number; width: number } | null>(null)
  useLayoutEffect(() => {
    if (!showMenu) return
    const place = (): void => {
      const r = inputRef.current?.getBoundingClientRect()
      if (r) setMenuPos({ top: r.bottom + 2, left: r.left, width: r.width })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [showMenu, filtered.length])

  if (!editMode) {
    if (chips.length === 0) return <span className="meta-field--empty">—</span>
    return (
      <span className="genre-chips genre-chips--readonly">
        {chips.map((c) =>
          onGenreClick ? (
            <button
              key={c}
              type="button"
              className="genre-chip genre-chip--clickable"
              {...tooltip(`Show ${c}`)}
              onClick={() => onGenreClick(c)}
            >
              {c}
            </button>
          ) : (
            <span key={c} className="genre-chip">
              {c}
            </span>
          )
        )}
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
          ref={inputRef}
          className="genre-chips-input"
          value={input}
          placeholder={chips.length ? '' : 'Add genre…'}
          aria-label="Add genre"
          role="combobox"
          aria-expanded={showMenu}
          aria-controls="genre-autocomplete-list"
          aria-activedescendant={active >= 0 ? `genre-opt-${active}` : undefined}
          onChange={(e) => {
            setInput(e.target.value)
            setOpen(true)
            setActive(-1)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              setOpen(true)
              setActive((i) => (filtered.length ? Math.min(i + 1, filtered.length - 1) : -1))
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              setActive((i) => Math.max(i - 1, -1))
            } else if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault()
              addChip(active >= 0 && active < filtered.length ? filtered[active] : input)
            } else if (e.key === 'Backspace' && !input && chips.length) {
              removeChip(chips[chips.length - 1])
            } else if (e.key === 'Escape') {
              setOpen(false)
              setActive(-1)
            }
          }}
        />
        {showMenu &&
          menuPos &&
          createPortal(
            <ul
              id="genre-autocomplete-list"
              className="genre-autocomplete"
              role="listbox"
              style={{ top: menuPos.top, left: menuPos.left, minWidth: menuPos.width }}
            >
              {filtered.map((s, i) => (
                <li key={s} id={`genre-opt-${i}`} role="option" aria-selected={i === active}>
                  <button
                    type="button"
                    className={`genre-autocomplete-item${i === active ? ' active' : ''}`}
                    // onMouseDown fires before the input's blur, so the chip is
                    // added without the blur committing/closing first.
                    onMouseDown={(e) => {
                      e.preventDefault()
                      addChip(s)
                    }}
                    onMouseEnter={() => setActive(i)}
                  >
                    {s}
                  </button>
                </li>
              ))}
            </ul>,
            document.body
          )}
      </div>
    </div>
  )
}
