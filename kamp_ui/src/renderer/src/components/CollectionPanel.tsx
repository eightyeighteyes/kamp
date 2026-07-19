import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { PanelToggleTab } from './PanelToggleTab'
import { GenreContextMenu } from './GenreContextMenu'
import { RemoveGenreModal } from './RemoveGenreModal'

const COLLECTION_WIDTH_KEY = 'kamp:collection-panel-width'
const COLLECTION_WIDTH_DEFAULT = 200
const COLLECTION_TAB_KEY = 'kamp:collection-tab'

export function CollectionPanel(): React.JSX.Element {
  const artists = useStore((s) => s.library.artists)
  const genres = useStore((s) => s.library.genres)
  const selectedArtist = useStore((s) => s.library.selectedArtist)
  const selectedGenre = useStore((s) => s.library.selectedGenre)
  const selectArtist = useStore((s) => s.selectArtist)
  const selectGenre = useStore((s) => s.selectGenre)
  const removeGenre = useStore((s) => s.removeGenre)
  const toggleCollectionPanel = useStore((s) => s.toggleCollectionPanel)
  // Genre right-click menu + destructive-removal confirmation (KAMP-606).
  const [genreMenu, setGenreMenu] = useState<{ x: number; y: number; genre: string } | null>(null)
  const [pendingRemove, setPendingRemove] = useState<string | null>(null)
  // Which list is shown. An active genre filter forces the Genres tab so
  // re-opening while a genre is selected lands there (KAMP-550); otherwise the
  // last explicitly-chosen tab is restored across restarts (KAMP-612).
  // Switching tabs is a pure view change — it never alters the active filter.
  const [tab, setTabState] = useState<'artists' | 'genres'>(() => {
    if (selectedGenre !== null) return 'genres'
    return localStorage.getItem(COLLECTION_TAB_KEY) === 'genres' ? 'genres' : 'artists'
  })

  function setTab(next: 'artists' | 'genres'): void {
    localStorage.setItem(COLLECTION_TAB_KEY, next)
    setTabState(next)
  }

  // KAMP-611: when a genre becomes the active filter (e.g. clicking a genre pill
  // on an album), surface the Genres tab. Render-phase sync on the selectedGenre
  // transition (same pattern as GenreChipsInput/AlbumMetaPanel) — state-only, never
  // persisted, so a user's explicit Artists preference (KAMP-612) survives once the
  // filter clears. Only fires on an actual change, so manual tab switches stick.
  const [prevGenre, setPrevGenre] = useState(selectedGenre)
  if (selectedGenre !== prevGenre) {
    setPrevGenre(selectedGenre)
    if (selectedGenre !== null) setTabState('genres')
  }
  const [panelWidth, setPanelWidth] = useState<number>(() => {
    const saved = parseFloat(localStorage.getItem(COLLECTION_WIDTH_KEY) ?? '')
    const max = window.innerWidth * 0.33
    return isNaN(saved)
      ? COLLECTION_WIDTH_DEFAULT
      : Math.min(max, Math.max(COLLECTION_WIDTH_DEFAULT, saved))
  })
  const [isResizing, setIsResizing] = useState(false)
  const dragStartXRef = useRef(0)
  const widthAtDragStartRef = useRef(COLLECTION_WIDTH_DEFAULT)
  const didDragRef = useRef(false)

  // Clamp width to 33% when the window shrinks.
  useEffect(() => {
    const onWindowResize = (): void => {
      const max = window.innerWidth * 0.33
      setPanelWidth((w) => {
        if (w > max) {
          localStorage.setItem(COLLECTION_WIDTH_KEY, String(Math.round(max)))
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
        Math.min(max, Math.max(COLLECTION_WIDTH_DEFAULT, widthAtDragStartRef.current + delta))
      )
    }

    const onUp = (): void => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      setIsResizing(false)
      if (didDragRef.current) {
        setPanelWidth((w) => {
          localStorage.setItem(COLLECTION_WIDTH_KEY, String(Math.round(w)))
          return w
        })
      }
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  function handleResizeDoubleClick(): void {
    setPanelWidth(COLLECTION_WIDTH_DEFAULT)
    localStorage.setItem(COLLECTION_WIDTH_KEY, String(COLLECTION_WIDTH_DEFAULT))
  }

  return (
    <aside
      className={`collection-panel${isResizing ? ' collection-panel--resizing' : ''}`}
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
        <ul className="collection-list" role="tabpanel" aria-labelledby="lib-tab-artists">
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
        <ul className="collection-list" role="tabpanel" aria-labelledby="lib-tab-genres">
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
                  onContextMenu={(e) => {
                    e.preventDefault()
                    setGenreMenu({ x: e.clientX, y: e.clientY, genre })
                  }}
                >
                  {genre}
                </li>
              ))}
            </>
          )}
        </ul>
      )}
      <div
        className="collection-resize-handle"
        onMouseDown={handleResizeMouseDown}
        onDoubleClick={handleResizeDoubleClick}
      />
      {/* Inner-edge toggle: a child of the panel so it tracks the resize edge. */}
      <PanelToggleTab panel="collection" placement="inner" active onClick={toggleCollectionPanel} />
      {genreMenu && (
        <GenreContextMenu
          x={genreMenu.x}
          y={genreMenu.y}
          genre={genreMenu.genre}
          onRemove={() => setPendingRemove(genreMenu.genre)}
          onClose={() => setGenreMenu(null)}
        />
      )}
      {pendingRemove && (
        <RemoveGenreModal
          genre={pendingRemove}
          onConfirm={() => {
            void removeGenre(pendingRemove)
            setPendingRemove(null)
          }}
          onCancel={() => setPendingRemove(null)}
        />
      )}
    </aside>
  )
}
