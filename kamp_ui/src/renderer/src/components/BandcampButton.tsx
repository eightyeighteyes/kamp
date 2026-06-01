import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { useMenuBounds } from '../hooks/useMenuBounds'

export function BandcampButton(): React.JSX.Element | null {
  const configValues = useStore((s) => s.configValues)
  const openPrefs = useStore((s) => s.openPrefs)
  const loadConfig = useStore((s) => s.loadConfig)
  const [syncState, setSyncState] = useState<'idle' | 'syncing'>('idle')
  const [menuOpen, setMenuOpen] = useState(false)
  const anchorRef = useRef<HTMLDivElement>(null)
  const tooltip = useTooltip()
  const menuRef = useRef<HTMLDivElement>(null)
  useMenuBounds(menuRef, menuOpen)

  useEffect(() => {
    return window.api.bandcamp.onSyncStatus(setSyncState)
  }, [])

  // Close context menu on outside click.
  useEffect(() => {
    if (!menuOpen) return
    const handler = (e: MouseEvent): void => {
      if (anchorRef.current && !anchorRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuOpen])

  const connected = configValues?.['bandcamp.connected'] ?? false
  const everConnected = configValues?.['bandcamp.ever_connected'] ?? false

  if (!connected && !everConnected) return null

  if (!connected) {
    return (
      <div className="bandcamp-btn-anchor">
        <button
          className="bandcamp-btn bandcamp-btn--disconnected"
          onClick={() =>
            window.api.bandcamp
              .beginLogin()
              .then(({ ok }) => {
                if (ok) loadConfig()
              })
              .catch(console.error)
          }
          {...tooltip(TOOLTIPS.BANDCAMP_RECONNECT)}
        >
          <svg viewBox="0 0 24 24" width="20" height="20">
            <path d="M0 18.75l7.437-13.5H24L16.563 18.75z" />
          </svg>
        </button>
      </div>
    )
  }

  const handleClick = (): void => {
    window.api.bandcamp.triggerSync().catch(console.error)
  }

  const handleContextMenu = (e: React.MouseEvent): void => {
    e.preventDefault()
    setMenuOpen((v) => !v)
  }

  return (
    <div className="bandcamp-btn-anchor" ref={anchorRef}>
      <button
        className={`bandcamp-btn${syncState === 'syncing' ? ' bandcamp-btn--syncing' : ''}`}
        onClick={handleClick}
        onContextMenu={handleContextMenu}
        {...tooltip(syncState === 'syncing' ? TOOLTIPS.BANDCAMP_SYNCING : TOOLTIPS.BANDCAMP_SYNC)}
      >
        {/* Bandcamp logo: parallelogram shape */}
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
          <path d="M0 18.75l7.437-13.5H24L16.563 18.75z" />
        </svg>
      </button>
      {menuOpen && (
        <div ref={menuRef} className="bandcamp-context-menu">
          <button
            onClick={() => {
              setMenuOpen(false)
              openPrefs('services')
            }}
          >
            Bandcamp options…
          </button>
        </div>
      )}
    </div>
  )
}
