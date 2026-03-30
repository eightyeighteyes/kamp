import React, { useEffect } from 'react'
import { useStore } from './store'
import { connectStateStream } from './api/client'
import { ArtistPanel } from './components/ArtistPanel'
import { AlbumGrid } from './components/AlbumGrid'
import { NowPlayingView } from './components/NowPlayingView'
import { SetupScreen } from './components/SetupScreen'
import { TrackList } from './components/TrackList'
import { TransportBar } from './components/TransportBar'

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

  useEffect(() => {
    loadLibrary()
    loadUiState()

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
          loadLibrary()
          loadUiState()
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

  // Global keyboard shortcuts — skip when focus is inside a text input.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent): void {
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
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [togglePlayPause, next, prev, setActiveView, activeView])

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
        </nav>
      )}
      <div className="app-body">
        {!showSetup && activeView === 'library' && <ArtistPanel />}
        <main className="main-content">
          {showSetup ? (
            <SetupScreen />
          ) : activeView === 'now-playing' ? (
            <NowPlayingView />
          ) : selectedAlbum ? (
            <TrackList />
          ) : (
            <AlbumGrid />
          )}
        </main>
      </div>
      <TransportBar />
    </div>
  )
}
