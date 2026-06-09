import React, { useEffect, useRef, useState } from 'react'

export type SortOption = { key: string; label: string }

interface Props {
  value: string
  options: SortOption[]
  dir: 'asc' | 'desc'
  onChange: (key: string) => void
  onDirChange: (dir: 'asc' | 'desc') => void
}

export function SortControl({
  value,
  options,
  dir,
  onChange,
  onDirChange
}: Props): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const currentLabel = options.find((o) => o.key === value)?.label ?? options[0]?.label ?? 'Sort'

  useEffect(() => {
    if (!open) return
    const handler = (e: PointerEvent): void => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', handler)
    return (): void => document.removeEventListener('pointerdown', handler)
  }, [open])

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <div className="sort-anchor" ref={ref}>
        <button
          className="toolbar-dropdown-trigger"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="listbox"
        >
          {`Sort: ${currentLabel}`}
          <span className="dropdown-chevron" aria-hidden="true">
            ▾
          </span>
        </button>
        {open && (
          <div className="toolbar-dropdown-popover" role="listbox" aria-label="Sort by">
            {options.map((opt) => (
              <button
                key={opt.key}
                role="option"
                aria-selected={value === opt.key}
                className={`toolbar-dropdown-item${value === opt.key ? ' active' : ''}`}
                onClick={() => {
                  onChange(opt.key)
                  setOpen(false)
                }}
              >
                <span className="dropdown-check" aria-hidden="true">
                  {value === opt.key ? '✓' : ''}
                </span>
                {opt.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <button
        className="toolbar-dropdown-trigger sort-dir-btn"
        title={dir === 'asc' ? 'Ascending — click to reverse' : 'Descending — click to reverse'}
        aria-label={dir === 'asc' ? 'Sort ascending' : 'Sort descending'}
        onClick={() => onDirChange(dir === 'asc' ? 'desc' : 'asc')}
      >
        {dir === 'asc' ? '↑' : '↓'}
      </button>
    </div>
  )
}
