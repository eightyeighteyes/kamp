import React, { useEffect } from 'react'
import { useStore } from './store'
import { connectStateStream } from './api/client'
import { ArtistPanel } from './components/ArtistPanel'
import { AlbumGrid } from './components/AlbumGrid'
import { TrackList } from './components/TrackList'
import { TransportBar } from './components/TransportBar'

export default function App(): React.JSX.Element {
  const loadLibrary = useStore((s) => s.loadLibrary)
  const applyServerState = useStore((s) => s.applyServerState)
  const selectedAlbum = useStore((s) => s.library.selectedAlbum)

  useEffect(() => {
    loadLibrary()

    // Connect WebSocket state stream. The cleanup function closes the
    // socket and stops the polling interval when the component unmounts.
    const disconnect = connectStateStream(applyServerState, () => {
      // Reconnect after a short delay if the server closes the socket.
      setTimeout(() => {
        connectStateStream(applyServerState)
      }, 2000)
    })

    return disconnect
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="app">
      <div className="app-body">
        <ArtistPanel />
        <main className="main-content">{selectedAlbum ? <TrackList /> : <AlbumGrid />}</main>
      </div>
      <TransportBar />
    </div>
  )
}
