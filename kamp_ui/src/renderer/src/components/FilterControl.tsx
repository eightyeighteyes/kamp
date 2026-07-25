import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

type FilterKey = 'favorite_album' | 'has_favorite_track' | 'unplayed' | 'top_albums'

const FILTER_OPTIONS: { key: FilterKey; label: string }[] = [
  { key: 'favorite_album', label: 'Favorite albums' },
  { key: 'has_favorite_track', label: 'Albums with favorited tracks' },
  { key: 'unplayed', label: 'Unplayed' },
  { key: 'top_albums', label: 'Top Albums' }
]

export function FilterControl(): React.JSX.Element {
  const libraryFilter = useStore((s) => s.libraryFilter)
  const setLibraryFilter = useStore((s) => s.setLibraryFilter)
  const [open, setOpen] = useState(false)
  // Single ref wrapping both trigger and popover so contains() check works on option clicks.
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent): void => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return (): void => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const toggle = (key: FilterKey): void => {
    if (libraryFilter.includes(key)) {
      setLibraryFilter(libraryFilter.filter((f) => f !== key))
    } else {
      setLibraryFilter([...libraryFilter, key])
    }
  }

  const SOURCE_KEYS = ['remote_only', 'local_only']
  const activeFilters = libraryFilter.filter((f) => !SOURCE_KEYS.includes(f))
  const hasActive = activeFilters.length > 0

  return (
    <div className="filter-anchor" ref={ref}>
      <button
        className={`toolbar-dropdown-trigger${hasActive ? ' has-active' : ''}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        Filter
        {hasActive && (
          <span className="toolbar-dropdown-badge" aria-label={`${activeFilters.length} active`}>
            · {activeFilters.length}
          </span>
        )}
        <span className="dropdown-chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div
          className="toolbar-dropdown-popover"
          role="listbox"
          aria-multiselectable="true"
          aria-label="Filter"
        >
          {hasActive && (
            <>
              <button
                className="toolbar-dropdown-item toolbar-dropdown-clear"
                onClick={() =>
                  setLibraryFilter(libraryFilter.filter((f) => SOURCE_KEYS.includes(f)))
                }
              >
                No filter
              </button>
              <div className="toolbar-dropdown-divider" />
            </>
          )}
          {FILTER_OPTIONS.map(({ key, label }) => {
            const active = libraryFilter.includes(key)
            return (
              <button
                key={key}
                role="option"
                aria-selected={active}
                className={`toolbar-dropdown-item${active ? ' active' : ''}`}
                onClick={() => toggle(key)}
              >
                <span className="dropdown-check" aria-hidden="true">
                  {active ? '▪' : ''}
                </span>
                {label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
