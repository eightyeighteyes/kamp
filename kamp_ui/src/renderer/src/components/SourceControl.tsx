import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

type SourceKey = 'remote_only' | 'local_only'

const SOURCE_OPTIONS: { key: SourceKey; label: string }[] = [
  { key: 'remote_only', label: 'Streaming' },
  { key: 'local_only', label: 'Local only' }
]

export function SourceControl(): React.JSX.Element {
  const libraryFilter = useStore((s) => s.libraryFilter)
  const setLibraryFilter = useStore((s) => s.setLibraryFilter)
  const [open, setOpen] = useState(false)
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

  const activeKey = SOURCE_OPTIONS.find(({ key }) => libraryFilter.includes(key))?.key ?? null

  const toggle = (key: SourceKey): void => {
    if (activeKey === key) {
      // deselect
      setLibraryFilter(libraryFilter.filter((f) => f !== key))
    } else {
      // select this one, removing the other if present
      const other = SOURCE_OPTIONS.find((o) => o.key !== key)!.key
      setLibraryFilter([...libraryFilter.filter((f) => f !== other && f !== key), key])
    }
    setOpen(false)
  }

  return (
    <div className="filter-anchor" ref={ref}>
      <button
        className={`toolbar-dropdown-trigger${activeKey ? ' has-active' : ''}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        {activeKey ? `Source: ${SOURCE_OPTIONS.find((o) => o.key === activeKey)!.label}` : 'Source'}
        <span className="dropdown-chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && (
        <div className="toolbar-dropdown-popover" role="listbox" aria-label="Source">
          <button
            role="option"
            aria-selected={activeKey === null}
            className={`toolbar-dropdown-item${activeKey === null ? ' active' : ''}`}
            onClick={() => {
              if (activeKey) setLibraryFilter(libraryFilter.filter((f) => f !== activeKey))
              setOpen(false)
            }}
          >
            <span className="dropdown-check" aria-hidden="true">
              {activeKey === null ? '▪' : ''}
            </span>
            All
          </button>
          {SOURCE_OPTIONS.map(({ key, label }) => {
            const active = activeKey === key
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
