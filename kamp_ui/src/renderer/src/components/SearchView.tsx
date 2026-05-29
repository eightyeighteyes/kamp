import React, { useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Album, Track } from '../api/client'
import { SortControl } from './SortControl'
import { FilterControl } from './FilterControl'
import { AlbumContextMenu } from './AlbumContextMenu'
import { TrackContextMenu } from './TrackContextMenu'
import { FavoriteIcon } from './TransportIcons'

type AlbumMenu = { x: number; y: number; album: Album }
type TrackMenu = { x: number; y: number; track: Track }

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
        {album.favorite && (
          <div className="album-fav-badge">
            <FavoriteIcon active size={14} />
          </div>
        )}
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
  const libraryFilter = useStore((s) => s.libraryFilter)
  const allAlbums = useStore((s) => s.library.albums)

  const [albumMenu, setAlbumMenu] = useState<AlbumMenu | null>(null)
  const [trackMenu, setTrackMenu] = useState<TrackMenu | null>(null)

  const top100Keys =
    libraryFilter.includes('top_albums') && allAlbums.length > 0
      ? new Set(
          [...allAlbums]
            .sort((a, b) => b.play_count_avg - a.play_count_avg)
            .slice(0, 100)
            .map((a) => `${a.album_artist}\0${a.album}`)
        )
      : null

  const rawAlbums = results?.albums ?? []
  const visibleAlbums =
    libraryFilter.length > 0
      ? rawAlbums.filter(
          (a) =>
            (libraryFilter.includes('favorite_album') && a.favorite) ||
            (libraryFilter.includes('has_favorite_track') && a.has_favorite_track) ||
            (libraryFilter.includes('unplayed') && a.last_played_at === null) ||
            (libraryFilter.includes('top_albums') &&
              top100Keys!.has(`${a.album_artist}\0${a.album}`))
        )
      : rawAlbums

  const albumMap = new Map<string, Album>()
  allAlbums.forEach((a) => albumMap.set(`${a.album_artist}\0${a.album}`, a))

  const rawTracks = results?.tracks ?? []
  const visibleTracks =
    libraryFilter.length > 0
      ? rawTracks.filter((t) => {
          const key = `${t.album_artist}\0${t.album}`
          const album = t.album ? albumMap.get(key) : undefined
          return (
            (libraryFilter.includes('favorite_album') && album?.favorite === true) ||
            (libraryFilter.includes('has_favorite_track') && t.favorite) ||
            (libraryFilter.includes('unplayed') && t.play_count === 0) ||
            (libraryFilter.includes('top_albums') && album !== undefined && top100Keys!.has(key))
          )
        })
      : rawTracks

  return (
    <div className="search-view">
      <div className="search-view-toolbar">
        <SortControl />
        <FilterControl />
      </div>
      <div className="search-view-content">
        {!results ? (
          <div className="search-empty">Searching…</div>
        ) : !visibleAlbums.length && !visibleTracks.length ? (
          <div className="search-empty">No results for &ldquo;{query}&rdquo;</div>
        ) : (
          <>
            {visibleAlbums.length > 0 && (
              <section className="search-section">
                <h2 className="search-section-title">Albums</h2>
                <div className="search-album-grid">
                  {visibleAlbums.map((album) => (
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
            {visibleTracks.length > 0 && (
              <section className="search-section">
                <h2 className="search-section-title">Tracks</h2>
                <div className="search-track-list">
                  {visibleTracks.map((track) => (
                    <SearchTrackRow
                      key={track.id}
                      track={track}
                      onContextMenu={(e, t) => {
                        e.preventDefault()
                        setAlbumMenu(null)
                        setTrackMenu({ x: e.clientX, y: e.clientY, track: t })
                      }}
                    />
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>

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
          track={trackMenu.track}
          onClose={() => setTrackMenu(null)}
        />
      )}
    </div>
  )
}
