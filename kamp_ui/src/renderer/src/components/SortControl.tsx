import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

type SortOrder = 'album_artist' | 'album' | 'date_added' | 'last_played' | 'most_played'

const SORT_OPTIONS: SortOrder[] = [
  'album_artist',
  'album',
  'date_added',
  'last_played',
  'most_played'
]

const SORT_LABELS: Record<SortOrder, string> = {
  album_artist: 'Artist',
  album: 'Album',
  date_added: 'Date Added',
  last_played: 'Last Played',
  most_played: 'Most Played'
}

export function SortControl(): React.JSX.Element {
  const sortOrder = useStore((s) => s.sortOrder)
  const setSortOrder = useStore((s) => s.setSortOrder)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: PointerEvent): void => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', handler)
    return (): void => document.removeEventListener('pointerdown', handler)
  }, [open])

  return (
    <div className="sort-anchor" ref={ref}>
      <button
        className="toolbar-dropdown-trigger"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        {SORT_LABELS[sortOrder as SortOrder] ?? SORT_LABELS.album_artist}
        <span className="dropdown-chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div className="toolbar-dropdown-popover" role="listbox" aria-label="Sort by">
          {SORT_OPTIONS.map((key) => (
            <button
              key={key}
              role="option"
              aria-selected={sortOrder === key}
              className={`toolbar-dropdown-item${sortOrder === key ? ' active' : ''}`}
              onClick={() => {
                setSortOrder(key)
                setOpen(false)
              }}
            >
              <span className="dropdown-check" aria-hidden="true">
                {sortOrder === key ? '✓' : ''}
              </span>
              {SORT_LABELS[key]}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
