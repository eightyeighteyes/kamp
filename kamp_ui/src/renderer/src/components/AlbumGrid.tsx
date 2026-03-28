import React, { useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Album } from '../api/client'

function AlbumCard({ album }: { album: Album }): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const [artLoaded, setArtLoaded] = useState(false)

  const isActive =
    currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  return (
    <div
      className={`album-card${isActive ? ' playing' : ''}`}
      tabIndex={0}
      onClick={() => selectAlbum(album)}
      onKeyDown={(e) => e.key === 'Enter' && selectAlbum(album)}
    >
      <div className={`album-art${artLoaded ? ' has-art' : ''}`}>
        {album.has_art && (
          <img
            className="album-art-img"
            src={artUrl(album.album_artist, album.album)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => setArtLoaded(false)}
          />
        )}
        {playing && isActive && <div className="now-playing-badge">▶</div>}
      </div>
      <div className="album-info">
        <div className="album-title">{album.album}</div>
        <div className="album-artist">{album.album_artist}</div>
        <div className="album-year">{album.year}</div>
      </div>
    </div>
  )
}

export function AlbumGrid(): React.JSX.Element {
  const albums = useStore((s) => s.library.albums)
  const selectedArtist = useStore((s) => s.library.selectedArtist)

  const visible = selectedArtist ? albums.filter((a) => a.album_artist === selectedArtist) : albums

  if (visible.length === 0) {
    return (
      <div className="album-grid-empty">
        {albums.length === 0 ? 'No albums in library.' : 'No albums for this artist.'}
      </div>
    )
  }

  return (
    <div className="album-grid">
      {visible.map((album) => (
        <AlbumCard key={`${album.album_artist}\0${album.album}`} album={album} />
      ))}
    </div>
  )
}
