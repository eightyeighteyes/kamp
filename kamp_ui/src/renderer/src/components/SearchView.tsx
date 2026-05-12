import React, { useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Album, Track } from '../api/client'
import { SortControl } from './SortControl'
import { AlbumContextMenu } from './AlbumContextMenu'
import { TrackContextMenu } from './TrackContextMenu'
import { FavoriteIcon } from './TransportIcons'

type AlbumMenu = { x: number; y: number; album: Album }
type TrackMenu = { x: number; y: number; filePath: string; favorite: boolean }

function SearchAlbumCard({
  album,
  onContextMenu
}: {
  album: Album
  onContextMenu: (e: React.MouseEvent, album: Album) => void
}): React.JSX.Element {
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
      draggable
      onClick={handleClick}
      onKeyDown={(e) => e.key === 'Enter' && handleClick()}
      onContextMenu={(e) => onContextMenu(e, album)}
      onDragStart={(e) => {
        e.dataTransfer.setData(
          'text/kamp-album',
          JSON.stringify({
            album_artist: album.album_artist,
            album: album.album,
            file_path: album.file_path
          })
        )
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className={`search-album-art${artLoaded ? ' has-art' : ''}`}>
        {album.has_art && (
          <img
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
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

function SearchTrackRow({
  track,
  onContextMenu
}: {
  track: Track
  onContextMenu: (e: React.MouseEvent, track: Track) => void
}): React.JSX.Element {
  const playTrack = useStore((s) => s.playTrack)
  const setSearchQuery = useStore((s) => s.setSearchQuery)

  const handleClick = (): void => {
    // Pass file_path for tracks with no album so the server can look them up
    // by path rather than by the empty album key.
    void playTrack(
      track.album_artist,
      track.album,
      track.track_number - 1,
      track.album ? '' : track.file_path
    )
    void setSearchQuery('')
  }

  return (
    <div
      className="search-track-row"
      tabIndex={0}
      draggable
      onDoubleClick={handleClick}
      onKeyDown={(e) => e.key === 'Enter' && handleClick()}
      onContextMenu={(e) => onContextMenu(e, track)}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-track-path', track.file_path)
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <span className="search-track-fav">
        {track.favorite && <FavoriteIcon active size={10} />}
      </span>
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

  const [albumMenu, setAlbumMenu] = useState<AlbumMenu | null>(null)
  const [trackMenu, setTrackMenu] = useState<TrackMenu | null>(null)

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
      {hasAlbums && <SortControl />}
      {hasAlbums && (
        <section className="search-section">
          <h2 className="search-section-title">Albums</h2>
          <div className="search-album-grid">
            {results.albums.map((album) => (
              <SearchAlbumCard
                key={`${album.album_artist}\0${album.album}`}
                album={album}
                onContextMenu={(e, a) => {
                  e.preventDefault()
                  setTrackMenu(null)
                  setAlbumMenu({ x: e.clientX, y: e.clientY, album: a })
                }}
              />
            ))}
          </div>
        </section>
      )}
      {hasTracks && (
        <section className="search-section">
          <h2 className="search-section-title">Tracks</h2>
          <div className="search-track-list">
            {results.tracks.map((track) => (
              <SearchTrackRow
                key={track.file_path}
                track={track}
                onContextMenu={(e, t) => {
                  e.preventDefault()
                  setAlbumMenu(null)
                  setTrackMenu({
                    x: e.clientX,
                    y: e.clientY,
                    filePath: t.file_path,
                    favorite: t.favorite
                  })
                }}
              />
            ))}
          </div>
        </section>
      )}

      {albumMenu && (
        <AlbumContextMenu
          x={albumMenu.x}
          y={albumMenu.y}
          album={albumMenu.album}
          onClose={() => setAlbumMenu(null)}
        />
      )}

      {trackMenu && (
        <TrackContextMenu
          x={trackMenu.x}
          y={trackMenu.y}
          filePath={trackMenu.filePath}
          favorite={trackMenu.favorite}
          onClose={() => setTrackMenu(null)}
        />
      )}
    </div>
  )
}
