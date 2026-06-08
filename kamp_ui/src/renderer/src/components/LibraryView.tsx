/**
 * Library view: shows the album grid, album track list, playlist grid, or playlist view.
 *
 * Extracted from App.tsx so it can register as a built-in slot panel
 * (kamp.library) while keeping the AlbumGrid/TrackList switching logic
 * in one place.
 */

import React from 'react'
import { useStore } from '../store'
import { AlbumGrid } from './AlbumGrid'
import { TrackList } from './TrackList'
import { PlaylistGrid } from './PlaylistGrid'
import { PlaylistView } from './PlaylistView'

export function LibraryView(): React.JSX.Element {
  const selectedAlbum = useStore((s) => s.library.selectedAlbum)
  const collectionType = useStore((s) => s.library.collectionType)
  const selectedPlaylist = useStore((s) => s.library.selectedPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)
  const loadPlaylists = useStore((s) => s.loadPlaylists)

  const handleSelectAlbums = (): void => {
    setCollectionType('albums')
  }

  const handleSelectPlaylists = (): void => {
    setCollectionType('playlists')
    void loadPlaylists()
  }

  const renderContent = (): React.JSX.Element => {
    if (collectionType === 'playlists') {
      return selectedPlaylist ? <PlaylistView /> : <PlaylistGrid />
    }
    return selectedAlbum ? <TrackList /> : <AlbumGrid />
  }

  return (
    <div className="library-view">
      <div className="library-collection-selector">
        <button
          className={`library-collection-tab${collectionType === 'albums' ? ' library-collection-tab--active' : ''}`}
          onClick={handleSelectAlbums}
        >
          Albums
        </button>
        <button
          className={`library-collection-tab${collectionType === 'playlists' ? ' library-collection-tab--active' : ''}`}
          onClick={handleSelectPlaylists}
        >
          Playlists
        </button>
      </div>
      {renderContent()}
    </div>
  )
}
