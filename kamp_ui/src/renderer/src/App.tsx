import React, { useEffect, useLayoutEffect, useRef } from 'react'
import { useStore } from './store'
import { connectStateStream } from './api/client'
import { ArtistPanel } from './components/ArtistPanel'
import { AlbumGrid } from './components/AlbumGrid'
import { NowPlayingView } from './components/NowPlayingView'
import { SearchBar } from './components/SearchBar'
import { SearchView } from './components/SearchView'
import { SetupScreen } from './components/SetupScreen'
import { TrackList } from './components/TrackList'
import { TransportBar } from './components/TransportBar'
import { QueuePanel } from './components/QueuePanel'

export default function App(): React.JSX.Element {
  const loadLibrary = useStore((s) => s.loadLibrary)
  const refreshOpenAlbum = useStore((s) => s.refreshOpenAlbum)
  const loadUiState = useStore((s) => s.loadUiState)
  const applyServerState = useStore((s) => s.applyServerState)
  const setServerStatus = useStore((s) => s.setServerStatus)
  const serverStatus = useStore((s) => s.serverStatus)
  const hasAlbums = useStore((s) => s.library.albums.length > 0)
  const selectedAlbum = useStore((s) => s.library.selectedAlbum)
  const activeView = useStore((s) => s.activeView)
  const setActiveView = useStore((s) => s.setActiveView)
  const togglePlayPause = useStore((s) => s.togglePlayPause)
  const next = useStore((s) => s.next)
  const prev = useStore((s) => s.prev)
  const searchQuery = useStore((s) => s.searchQuery)
  const setSearchQuery = useStore((s) => s.setSearchQuery)
  const queueVisible = useStore((s) => s.queueVisible)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)
  const loadQueue = useStore((s) => s.loadQueue)
  const searchBarRef = useRef<HTMLInputElement>(null)
  const mainContentRef = useRef<HTMLElement>(null)
  // Per-view scroll positions — kept current by a scroll listener so we never
  // read a browser-clamped value when the outgoing view's content was taller.
  const viewScrollRef = useRef<Partial<Record<string, number>>>({})

  useEffect(() => {
    // Load UI state (sort order, active view) before loading the library so the
    // library is fetched with the correct persisted sort order, not the default.
    loadUiState().then(() => loadLibrary())

    // Connect WebSocket state stream. On close, retry with exponential backoff
    // (1 s, 2 s, 4 s … capped at 30 s). After 8 failed attempts we give up and
    // show the offline screen. Attempts reset to 0 on every successful open so
    // that a sleep/wake cycle always gets a fresh run of retries.
    let attempts = 0
    const MAX_ATTEMPTS = 8

    const connect = (): (() => void) => {
      return connectStateStream(
        applyServerState,
        () => {
          attempts++
          if (attempts >= MAX_ATTEMPTS) {
            setServerStatus('disconnected')
          } else {
            setServerStatus('reconnecting')
            const delay = Math.min(1000 * 2 ** (attempts - 1), 30000)
            setTimeout(connect, delay)
          }
        },
        () => {
          attempts = 0
          setServerStatus('connected')
          void loadUiState().then(() => loadLibrary())
          void loadQueue()
        },
        () => {
          // Background scan completed — refresh album list then open track list.
          // Sequential: loadLibrary and refreshOpenAlbum both spread library state,
          // so running them concurrently risks one overwriting the other's update.
          void loadLibrary().then(() => refreshOpenAlbum())
        }
      )
    }

    const disconnect = connect()
    return disconnect
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Global keyboard shortcuts — skip when focus is inside a text input,
  // except for Cmd/Ctrl+K which focuses the search bar from anywhere.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent): void {
      // Cmd+K (mac) / Ctrl+K (win/linux) focuses the search bar.
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        searchBarRef.current?.focus()
        searchBarRef.current?.select()
        return
      }

      // Escape clears search when the search bar is focused.
      if (e.key === 'Escape' && searchQuery) {
        void setSearchQuery('')
        searchBarRef.current?.blur()
        return
      }

      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return

      switch (e.key) {
        case ' ':
          e.preventDefault()
          void togglePlayPause()
          break
        case 'ArrowRight':
          e.preventDefault()
          void next()
          break
        case 'ArrowLeft':
          e.preventDefault()
          void prev()
          break
        case 'l':
        case 'L':
          void setActiveView(activeView === 'library' ? 'now-playing' : 'library')
          break
        case 'q':
        case 'Q':
          // Don't intercept Cmd+Q (macOS quit) or Ctrl+Q.
          if (e.metaKey || e.ctrlKey) break
          toggleQueuePanel()
          break
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [
    togglePlayPause,
    next,
    prev,
    setActiveView,
    activeView,
    searchQuery,
    setSearchQuery,
    toggleQueuePanel
  ])

  // Keep viewScrollRef continuously current so we always have the right value
  // when switching views — reading scrollTop after a DOM update can give a
  // browser-clamped value if the new content is shorter than the old.
  useEffect(() => {
    const el = mainContentRef.current
    if (!el) return
    const onScroll = (): void => {
      viewScrollRef.current[activeView] = el.scrollTop
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [activeView])

  // Restore the incoming view's scroll position synchronously before paint.
  useLayoutEffect(() => {
    const el = mainContentRef.current
    if (!el) return
    el.scrollTop = viewScrollRef.current[activeView] ?? 0
  }, [activeView])

  if (serverStatus === 'disconnected') {
    return (
      <div className="server-offline">
        <div className="server-offline-icon">⏻</div>
        <div className="server-offline-title">kamp server is not running</div>
        <div className="server-offline-hint">
          Start it with <code>kamp server</code>
        </div>
      </div>
    )
  }

  const showSetup = serverStatus === 'connected' && !hasAlbums

  return (
    <div className="app">
      {serverStatus === 'reconnecting' && (
        <div className="reconnecting-banner">Reconnecting to server…</div>
      )}
      {!showSetup && (
        <nav className="view-tabs">
          <button
            className={activeView === 'library' ? 'active' : ''}
            onClick={() => setActiveView('library')}
          >
            Library
          </button>
          <button
            className={activeView === 'now-playing' ? 'active' : ''}
            onClick={() => setActiveView('now-playing')}
          >
            Now Playing
          </button>
          <SearchBar ref={searchBarRef} />
        </nav>
      )}
      <div className="app-body">
        {!showSetup && activeView === 'library' && !searchQuery && <ArtistPanel />}
        <main className="main-content" ref={mainContentRef}>
          {showSetup ? (
            <SetupScreen />
          ) : searchQuery ? (
            <SearchView />
          ) : activeView === 'now-playing' ? (
            <NowPlayingView />
          ) : selectedAlbum ? (
            <TrackList />
          ) : (
            <AlbumGrid />
          )}
        </main>
        {queueVisible && <QueuePanel />}
      </div>
      <TransportBar />
    </div>
  )
}
