import React, { useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Album, Track } from '../api/client'

function SearchAlbumCard({ album }: { album: Album }): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setSearchQuery = useStore((s) => s.setSearchQuery)
  const setActiveView = useStore((s) => s.setActiveView)
  const [artLoaded, setArtLoaded] = useState(false)

  const handleClick = (): void => {
    void setActiveView('library')
    void selectAlbum(album)
    void setSearchQuery('')
  }

  return (
    <div
      className="search-album-card"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={(e) => e.key === 'Enter' && handleClick()}
    >
      <div className={`search-album-art${artLoaded ? ' has-art' : ''}`}>
        {album.has_art && (
          <img
            src={artUrl(album.album_artist, album.album)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => setArtLoaded(false)}
          />
        )}
      </div>
      <div className="search-album-info">
        <div className="search-album-title">{album.album}</div>
        <div className="search-album-artist">{album.album_artist}</div>
        <div className="search-album-year">{album.year}</div>
      </div>
    </div>
  )
}

function SearchTrackRow({ track, index }: { track: Track; index: number }): React.JSX.Element {
  const playTrack = useStore((s) => s.playTrack)
  const setSearchQuery = useStore((s) => s.setSearchQuery)

  const handleClick = (): void => {
    void playTrack(track.album_artist, track.album, index)
    void setSearchQuery('')
  }

  return (
    <div
      className="search-track-row"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={(e) => e.key === 'Enter' && handleClick()}
    >
      <span className="search-track-title">{track.title}</span>
      <span className="search-track-meta">
        {track.artist} — {track.album}
      </span>
    </div>
  )
}

export function SearchView(): React.JSX.Element {
  const results = useStore((s) => s.searchResults)
  const query = useStore((s) => s.searchQuery)

  if (!results) {
    return <div className="search-empty">Searching…</div>
  }

  const hasAlbums = results.albums.length > 0
  const hasTracks = results.tracks.length > 0

  if (!hasAlbums && !hasTracks) {
    return <div className="search-empty">No results for &ldquo;{query}&rdquo;</div>
  }

  return (
    <div className="search-view">
      {hasAlbums && (
        <section className="search-section">
          <h2 className="search-section-title">Albums</h2>
          <div className="search-album-grid">
            {results.albums.map((album) => (
              <SearchAlbumCard key={`${album.album_artist}\0${album.album}`} album={album} />
            ))}
          </div>
        </section>
      )}
      {hasTracks && (
        <section className="search-section">
          <h2 className="search-section-title">Tracks</h2>
          <div className="search-track-list">
            {results.tracks.map((track, i) => (
              <SearchTrackRow key={`${track.file_path}`} track={track} index={i} />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
