import React from 'react'
import { useStore } from '../store'

export function ArtistPanel(): React.JSX.Element {
  const artists = useStore((s) => s.library.artists)
  const selected = useStore((s) => s.library.selectedArtist)
  const selectArtist = useStore((s) => s.selectArtist)

  return (
    <aside className="artist-panel">
      <div className="panel-header">Artists</div>
      <ul className="artist-list">
        <li
          className={selected === null ? 'active' : ''}
          tabIndex={0}
          onClick={() => selectArtist(null)}
          onKeyDown={(e) => e.key === 'Enter' && selectArtist(null)}
        >
          All Artists
        </li>
        {artists.map((artist) => (
          <li
            key={artist}
            className={selected === artist ? 'active' : ''}
            tabIndex={0}
            onClick={() => selectArtist(artist)}
            onKeyDown={(e) => e.key === 'Enter' && selectArtist(artist)}
          >
            {artist}
          </li>
        ))}
      </ul>
    </aside>
  )
}
