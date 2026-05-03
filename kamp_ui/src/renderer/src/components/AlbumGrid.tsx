import React, { useLayoutEffect, useRef } from 'react'

// Persists scroll position across AlbumGrid mount/unmount cycles (e.g. open
// album → back). Module-level so it survives React unmounting the component.
let savedScrollTop = 0
import { useStore } from '../store'
import { AlbumCard } from './AlbumCard'
import { SortControl } from './SortControl'
import { BandcampButton } from './BandcampButton'
import { PipelineIndicator } from './PipelineIndicator'

export function AlbumGrid(): React.JSX.Element {
  const albums = useStore((s) => s.library.albums)
  const selectedArtist = useStore((s) => s.library.selectedArtist)

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

  const visible = selectedArtist ? albums.filter((a) => a.album_artist === selectedArtist) : albums

  return (
    <div className="album-grid-container" ref={containerRef}>
      <div className="album-grid-toolbar">
        <SortControl />
        <div className="album-grid-toolbar-actions">
          <PipelineIndicator />
          <BandcampButton />
        </div>
      </div>
      {visible.length === 0 ? (
        <div className="album-grid-empty">
          {albums.length === 0 ? 'No albums in library.' : 'No albums for this artist.'}
        </div>
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
