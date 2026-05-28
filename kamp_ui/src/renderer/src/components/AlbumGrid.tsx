import React, { useLayoutEffect, useRef } from 'react'

// Persists scroll position across AlbumGrid mount/unmount cycles (e.g. open
// album → back). Module-level so it survives React unmounting the component.
let savedScrollTop = 0
import { useStore } from '../store'
import { AlbumCard } from './AlbumCard'
import { SortControl } from './SortControl'
import { FilterControl } from './FilterControl'

export function AlbumGrid(): React.JSX.Element {
  const albums = useStore((s) => s.library.albums)
  const selectedArtist = useStore((s) => s.library.selectedArtist)
  const libraryFilter = useStore((s) => s.libraryFilter)

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

  const emptyMessage = (): string => {
    if (albums.length === 0) return 'No albums in library.'
    if (libraryFilter.length > 0) return 'No albums match the active filter.'
    return 'No albums for this artist.'
  }

  return (
    <div className="album-grid-container" ref={containerRef}>
      <div className="album-grid-toolbar">
        <SortControl />
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
