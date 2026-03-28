import React, { useEffect } from 'react'
import { useStore } from './store'
import { connectStateStream } from './api/client'
import { ArtistPanel } from './components/ArtistPanel'
import { AlbumGrid } from './components/AlbumGrid'
import { SetupScreen } from './components/SetupScreen'
import { TrackList } from './components/TrackList'
import { TransportBar } from './components/TransportBar'

export default function App(): React.JSX.Element {
  const loadLibrary = useStore((s) => s.loadLibrary)
  const applyServerState = useStore((s) => s.applyServerState)
  const setServerStatus = useStore((s) => s.setServerStatus)
  const serverStatus = useStore((s) => s.serverStatus)
  const hasAlbums = useStore((s) => s.library.albums.length > 0)
  const selectedAlbum = useStore((s) => s.library.selectedAlbum)

  useEffect(() => {
    loadLibrary()

    // Connect WebSocket state stream. Re-establishes automatically when the
    // server restarts. Each reconnect also reloads the library in case albums
    // were added while the server was down.
    const connect = (): (() => void) => {
      return connectStateStream(
        applyServerState,
        () => {
          setServerStatus('disconnected')
          setTimeout(connect, 2000)
        },
        () => {
          setServerStatus('connected')
          loadLibrary()
        }
      )
    }

    const disconnect = connect()
    return disconnect
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (serverStatus === 'disconnected' && !hasAlbums) {
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
      {serverStatus === 'disconnected' && (
        <div className="reconnecting-banner">Reconnecting to server…</div>
      )}
      <div className="app-body">
        {!showSetup && <ArtistPanel />}
        <main className="main-content">
          {showSetup ? <SetupScreen /> : selectedAlbum ? <TrackList /> : <AlbumGrid />}
        </main>
      </div>
      <TransportBar />
    </div>
  )
}
