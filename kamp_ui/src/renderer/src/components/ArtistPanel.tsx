import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

const ARTIST_WIDTH_KEY = 'kamp:artist-panel-width'
const ARTIST_WIDTH_DEFAULT = 200

export function ArtistPanel(): React.JSX.Element {
  const artists = useStore((s) => s.library.artists)
  const genres = useStore((s) => s.library.genres)
  const selectedArtist = useStore((s) => s.library.selectedArtist)
  const selectedGenre = useStore((s) => s.library.selectedGenre)
  const selectArtist = useStore((s) => s.selectArtist)
  const selectGenre = useStore((s) => s.selectGenre)
  // Which list is shown. Seeded from the active filter so re-opening while a
  // genre is selected lands on the Genres tab (KAMP-550). Switching tabs is a
  // pure view change — it never alters the active filter.
  const [tab, setTab] = useState<'artists' | 'genres'>(
    selectedGenre !== null ? 'genres' : 'artists'
  )
  const [panelWidth, setPanelWidth] = useState<number>(() => {
    const saved = parseFloat(localStorage.getItem(ARTIST_WIDTH_KEY) ?? '')
    const max = window.innerWidth * 0.33
    return isNaN(saved)
      ? ARTIST_WIDTH_DEFAULT
      : Math.min(max, Math.max(ARTIST_WIDTH_DEFAULT, saved))
  })
  const [isResizing, setIsResizing] = useState(false)
  const dragStartXRef = useRef(0)
  const widthAtDragStartRef = useRef(ARTIST_WIDTH_DEFAULT)
  const didDragRef = useRef(false)

  // Clamp width to 33% when the window shrinks.
  useEffect(() => {
    const onWindowResize = (): void => {
      const max = window.innerWidth * 0.33
      setPanelWidth((w) => {
        if (w > max) {
          localStorage.setItem(ARTIST_WIDTH_KEY, String(Math.round(max)))
          return max
        }
        return w
      })
    }
    window.addEventListener('resize', onWindowResize)
    return () => window.removeEventListener('resize', onWindowResize)
  }, [])

  function handleResizeMouseDown(e: React.MouseEvent): void {
    e.preventDefault()
    didDragRef.current = false
    dragStartXRef.current = e.clientX
    widthAtDragStartRef.current = panelWidth
    setIsResizing(true)

    const onMove = (ev: MouseEvent): void => {
      // Dragging right (larger clientX) widens the panel.
      const delta = ev.clientX - dragStartXRef.current
      if (Math.abs(delta) > 4) didDragRef.current = true
      if (!didDragRef.current) return
      const max = window.innerWidth * 0.33
      setPanelWidth(
        Math.min(max, Math.max(ARTIST_WIDTH_DEFAULT, widthAtDragStartRef.current + delta))
      )
    }

    const onUp = (): void => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      setIsResizing(false)
      if (didDragRef.current) {
        setPanelWidth((w) => {
          localStorage.setItem(ARTIST_WIDTH_KEY, String(Math.round(w)))
          return w
        })
      }
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  function handleResizeDoubleClick(): void {
    setPanelWidth(ARTIST_WIDTH_DEFAULT)
    localStorage.setItem(ARTIST_WIDTH_KEY, String(ARTIST_WIDTH_DEFAULT))
  }

  return (
    <aside
      className={`artist-panel${isResizing ? ' artist-panel--resizing' : ''}`}
      style={{ width: panelWidth }}
    >
      <div className="library-tabs" role="tablist" aria-label="Library filter">
        <button
          id="lib-tab-artists"
          role="tab"
          aria-selected={tab === 'artists'}
          className={`library-tab${tab === 'artists' ? ' active' : ''}`}
          onClick={() => setTab('artists')}
        >
          Artists
        </button>
        <button
          id="lib-tab-genres"
          role="tab"
          aria-selected={tab === 'genres'}
          className={`library-tab${tab === 'genres' ? ' active' : ''}`}
          onClick={() => setTab('genres')}
        >
          Genres
        </button>
      </div>
      {tab === 'artists' ? (
        <ul className="artist-list" role="tabpanel" aria-labelledby="lib-tab-artists">
          <li
            className={selectedArtist === null ? 'active' : ''}
            tabIndex={0}
            onClick={() => selectArtist(null)}
            onKeyDown={(e) => e.key === 'Enter' && selectArtist(null)}
          >
            All Artists
          </li>
          {artists.map((artist) => (
            <li
              key={artist}
              className={selectedArtist === artist ? 'active' : ''}
              tabIndex={0}
              onClick={() => selectArtist(artist)}
              onKeyDown={(e) => e.key === 'Enter' && selectArtist(artist)}
            >
              {artist}
            </li>
          ))}
        </ul>
      ) : (
        <ul className="artist-list" role="tabpanel" aria-labelledby="lib-tab-genres">
          {genres.length === 0 ? (
            <li className="library-list-empty" aria-disabled="true">
              No genres tagged yet
            </li>
          ) : (
            <>
              <li
                className={selectedGenre === null ? 'active' : ''}
                tabIndex={0}
                onClick={() => selectGenre(null)}
                onKeyDown={(e) => e.key === 'Enter' && selectGenre(null)}
              >
                All Genres
              </li>
              {genres.map((genre) => (
                <li
                  key={genre}
                  className={selectedGenre === genre ? 'active' : ''}
                  tabIndex={0}
                  onClick={() => selectGenre(genre)}
                  onKeyDown={(e) => e.key === 'Enter' && selectGenre(genre)}
                >
                  {genre}
                </li>
              ))}
            </>
          )}
        </ul>
      )}
      <div
        className="artist-resize-handle"
        onMouseDown={handleResizeMouseDown}
        onDoubleClick={handleResizeDoubleClick}
      />
    </aside>
  )
}
