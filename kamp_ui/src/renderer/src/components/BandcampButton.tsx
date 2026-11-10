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
  // KAMP-750: the reconnect path used to discard the login error entirely, so
  // a refused credential write looked exactly like nothing happening.
  const [loginError, setLoginError] = useState<string | null>(null)
  const popoverOpen = menuOpen || loginError !== null
  const anchorRef = useRef<HTMLDivElement>(null)
  const tooltip = useTooltip()
  const menuRef = useRef<HTMLDivElement>(null)
  useMenuBounds(menuRef, popoverOpen)

  useEffect(() => {
    return window.api.bandcamp.onSyncStatus(setSyncState)
  }, [])

  // Close the context menu / login error on outside click or Escape (KAMP-625:
  // this custom menu rolled its own dismissal and — unlike the shared
  // ContextMenu — was missing the Escape key handler).
  useEffect(() => {
    if (!popoverOpen) return
    const close = (): void => {
      setMenuOpen(false)
      setLoginError(null)
    }
    const onMouseDown = (e: MouseEvent): void => {
      if (anchorRef.current && !anchorRef.current.contains(e.target as Node)) close()
    }
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') close()
    }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [popoverOpen])

  const connected = configValues?.['bandcamp.connected'] ?? false
  const everConnected = configValues?.['bandcamp.ever_connected'] ?? false

  if (!connected && !everConnected) return null

  const handleReconnect = (): void => {
    setLoginError(null)
    window.api.bandcamp
      .beginLogin()
      .then((result) => {
        // Refresh on failure too: a keychain write that later completes (an
        // Allow clicked after the timeout) can leave the account connected.
        loadConfig()
        // A window the user closed themselves is not worth a popover.
        if (!result.ok && !result.cancelled) setLoginError(result.error ?? 'Login failed.')
      })
      .catch((err: unknown) => {
        console.error(err)
        setLoginError('Login failed.')
      })
  }

  if (!connected) {
    return (
      <div className="bandcamp-btn-anchor" ref={anchorRef}>
        <button
          className="bandcamp-btn bandcamp-btn--disconnected"
          onClick={handleReconnect}
          {...tooltip(TOOLTIPS.BANDCAMP_RECONNECT)}
        >
          <svg viewBox="0 0 24 24" width="20" height="20">
            <path d="M0 18.75l7.437-13.5H24L16.563 18.75z" />
          </svg>
        </button>
        {loginError !== null && (
          <div ref={menuRef} className="bandcamp-context-menu bandcamp-login-error" role="alert">
            {loginError}
          </div>
        )}
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
