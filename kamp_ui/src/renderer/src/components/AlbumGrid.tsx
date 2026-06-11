import React, { useLayoutEffect, useRef } from 'react'

// Persists scroll position across AlbumGrid mount/unmount cycles (e.g. open
// album → back). Module-level so it survives React unmounting the component.
let savedScrollTop = 0
import { useStore } from '../store'
import { AlbumCard } from './AlbumCard'
import { SortControl } from './SortControl'
import { FilterControl } from './FilterControl'
import { SourceControl } from './SourceControl'

const ALBUM_SORT_OPTIONS = [
  { key: 'album_artist', label: 'Artist' },
  { key: 'album', label: 'Album' },
  { key: 'date_added', label: 'Date Added' },
  { key: 'last_played', label: 'Last Played' },
  { key: 'most_played', label: 'Most Played' }
]

export function AlbumGrid(): React.JSX.Element {
  const albums = useStore((s) => s.library.albums)
  const selectedArtist = useStore((s) => s.library.selectedArtist)
  const selectArtist = useStore((s) => s.selectArtist)
  const libraryFilter = useStore((s) => s.libraryFilter)
  const sortOrder = useStore((s) => s.sortOrder)
  const sortDir = useStore((s) => s.sortDir)
  const setSortOrder = useStore((s) => s.setSortOrder)
  const setSortDir = useStore((s) => s.setSortDir)

  const containerRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const scroller = containerRef.current?.closest<HTMLElement>('.main-content')
    // Restore scroll position synchronously before paint to avoid a visible
    // jump to the top when navigating back from a track list.
    if (scroller) scroller.scrollTop = savedScrollTop
    return () => {
      // Save scroll position when navigating into an album.
      if (scroller) savedScrollTop = scroller.scrollTop
    }
  }, [])

  let visible = selectedArtist ? albums.filter((a) => a.album_artist === selectedArtist) : albums

  if (libraryFilter.length > 0) {
    const QUALITATIVE_FILTERS = ['favorite_album', 'has_favorite_track', 'unplayed', 'top_albums']
    const hasQualitativeFilter = QUALITATIVE_FILTERS.some((f) => libraryFilter.includes(f))

    if (hasQualitativeFilter) {
      // "Top Albums": top 100 by avg plays per track — same algorithm as Base Kamp Top Albums module.
      const top100Keys =
        libraryFilter.includes('top_albums') && albums.length > 0
          ? new Set(
              [...albums]
                .sort((a, b) => b.play_count_avg - a.play_count_avg)
                .slice(0, 100)
                .map((a) => `${a.album_artist}\0${a.album}`)
            )
          : null

      visible = visible.filter(
        (a) =>
          (libraryFilter.includes('favorite_album') && a.favorite) ||
          (libraryFilter.includes('has_favorite_track') && a.has_favorite_track) ||
          (libraryFilter.includes('unplayed') && a.last_played_at === null) ||
          (libraryFilter.includes('top_albums') && top100Keys!.has(`${a.album_artist}\0${a.album}`))
      )
    }

    // Source filters are AND-type: they narrow the result set independently.
    if (libraryFilter.includes('remote_only')) visible = visible.filter((a) => a.source !== 'local')
    if (libraryFilter.includes('local_only')) visible = visible.filter((a) => a.source === 'local')
  }

  const emptyMessage = (): string => {
    if (albums.length === 0) return 'No albums in library.'
    if (libraryFilter.length > 0) return 'No albums match the active filter.'
    return 'No albums for this artist.'
  }

  return (
    <div className="album-grid-container" ref={containerRef}>
      <div className="album-grid-toolbar">
        {selectedArtist && (
          <nav className="breadcrumb album-grid-breadcrumb" aria-label="Navigation">
            <button onClick={() => selectArtist(null)}>Library</button>
            <span className="breadcrumb-sep" aria-hidden="true">
              ›
            </span>
            <span>{selectedArtist}</span>
          </nav>
        )}
        <SortControl
          value={sortOrder}
          options={ALBUM_SORT_OPTIONS}
          dir={sortDir}
          onChange={(key) => void setSortOrder(key as typeof sortOrder)}
          onDirChange={(d) => void setSortDir(d)}
        />
        <SourceControl />
        <FilterControl />
      </div>
      {visible.length === 0 ? (
        <div className="album-grid-empty">{emptyMessage()}</div>
      ) : (
        <div className="album-grid">
          {visible.map((album) => (
            <AlbumCard
              key={album.missing_album ? album.file_path : `${album.album_artist}\0${album.album}`}
              album={album}
            />
          ))}
        </div>
      )}
    </div>
  )
}
