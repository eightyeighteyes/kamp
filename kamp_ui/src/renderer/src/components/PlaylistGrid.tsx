import React from 'react'
import { useStore } from '../store'
import { PlaylistCard } from './PlaylistCard'

export function PlaylistGrid(): React.JSX.Element {
  const playlists = useStore((s) => s.library.playlists)

  if (playlists.length === 0) {
    return (
      <div className="album-grid-container">
        <div className="album-grid-empty">
          No playlists yet. Right-click any track or album and choose Add to Playlist &rsaquo; New
          Playlist.
        </div>
      </div>
    )
  }

  return (
    <div className="album-grid-container">
      <div className="album-grid">
        {playlists.map((pl) => (
          <PlaylistCard key={pl.id} playlist={pl} />
        ))}
      </div>
    </div>
  )
}
