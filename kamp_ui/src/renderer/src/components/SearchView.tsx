import React, { useState } from 'react'
import { useStore } from '../store'
import type { Album, Track } from '../api/client'
import { SortControl } from './SortControl'
import { SourceControl } from './SourceControl'
import { FilterControl } from './FilterControl'
import { AlbumCard } from './AlbumCard'
import { TrackContextMenu } from './TrackContextMenu'
import { FavoriteIcon } from './TransportIcons'

type TrackMenu = { x: number; y: number; track: Track }

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

const SEARCH_SORT_OPTIONS = [
  { key: 'album_artist', label: 'Artist' },
  { key: 'album', label: 'Album' },
  { key: 'date_added', label: 'Date Added' },
  { key: 'last_played', label: 'Last Played' },
  { key: 'most_played', label: 'Most Played' },
]

export function SearchView(): React.JSX.Element {
  const results = useStore((s) => s.searchResults)
  const query = useStore((s) => s.searchQuery)
  const setSearchQuery = useStore((s) => s.setSearchQuery)
  const libraryFilter = useStore((s) => s.libraryFilter)
  const allAlbums = useStore((s) => s.library.albums)
  const sortOrder = useStore((s) => s.sortOrder)
  const sortDir = useStore((s) => s.sortDir)
  const setSortOrder = useStore((s) => s.setSortOrder)
  const setSortDir = useStore((s) => s.setSortDir)

  const [trackMenu, setTrackMenu] = useState<TrackMenu | null>(null)

  const QUALITATIVE_FILTERS = ['favorite_album', 'has_favorite_track', 'unplayed', 'top_albums']
  const hasQualitativeFilter = QUALITATIVE_FILTERS.some((f) => libraryFilter.includes(f))

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
  let visibleAlbums = rawAlbums
  if (hasQualitativeFilter) {
    visibleAlbums = visibleAlbums.filter(
      (a) =>
        (libraryFilter.includes('favorite_album') && a.favorite) ||
        (libraryFilter.includes('has_favorite_track') && a.has_favorite_track) ||
        (libraryFilter.includes('unplayed') && a.last_played_at === null) ||
        (libraryFilter.includes('top_albums') && top100Keys!.has(`${a.album_artist}\0${a.album}`))
    )
  }
  // Source filters are AND-type: they narrow the result set independently.
  if (libraryFilter.includes('remote_only'))
    visibleAlbums = visibleAlbums.filter((a) => a.source !== 'local')
  if (libraryFilter.includes('local_only'))
    visibleAlbums = visibleAlbums.filter((a) => a.source === 'local')

  const albumMap = new Map<string, Album>()
  allAlbums.forEach((a) => albumMap.set(`${a.album_artist}\0${a.album}`, a))

  const rawTracks = results?.tracks ?? []
  let visibleTracks = rawTracks
  if (hasQualitativeFilter) {
    visibleTracks = visibleTracks.filter((t) => {
      const key = `${t.album_artist}\0${t.album}`
      const album = t.album ? albumMap.get(key) : undefined
      return (
        (libraryFilter.includes('favorite_album') && album?.favorite === true) ||
        (libraryFilter.includes('has_favorite_track') && t.favorite) ||
        (libraryFilter.includes('unplayed') && t.play_count === 0) ||
        (libraryFilter.includes('top_albums') && album !== undefined && top100Keys!.has(key))
      )
    })
  }
  // Source filters are AND-type: they narrow the result set independently.
  if (libraryFilter.includes('remote_only'))
    visibleTracks = visibleTracks.filter((t) => t.source !== 'local')
  if (libraryFilter.includes('local_only'))
    visibleTracks = visibleTracks.filter((t) => t.source === 'local')

  return (
    <div className="search-view">
      <div className="search-view-toolbar">
        <SortControl
          value={sortOrder}
          options={SEARCH_SORT_OPTIONS}
          dir={sortDir}
          onChange={(key) => void setSortOrder(key as typeof sortOrder)}
          onDirChange={(d) => void setSortDir(d)}
        />
        <SourceControl />
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
                    <AlbumCard
                      key={`${album.album_artist}\0${album.album}`}
                      album={album}
                      onAfterSelect={() => void setSearchQuery('')}
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
