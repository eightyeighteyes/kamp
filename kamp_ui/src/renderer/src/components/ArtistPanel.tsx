import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

const ARTIST_WIDTH_KEY = 'kamp:artist-panel-width'
const ARTIST_WIDTH_DEFAULT = 200

export function ArtistPanel(): React.JSX.Element {
  const artists = useStore((s) => s.library.artists)
  const selected = useStore((s) => s.library.selectedArtist)
  const selectArtist = useStore((s) => s.selectArtist)
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
      <div className="panel-header">Artists</div>
      <ul className="artist-list">
        <li
          className={selected === null ? 'active' : ''}
          tabIndex={0}
          onClick={() => selectArtist(null)}
          onKeyDown={(e) => e.key === 'Enter' && selectArtist(null)}
        >
          All Artists
        </li>
        {artists.map((artist) => (
          <li
            key={artist}
            className={selected === artist ? 'active' : ''}
            tabIndex={0}
            onClick={() => selectArtist(artist)}
            onKeyDown={(e) => e.key === 'Enter' && selectArtist(artist)}
          >
            {artist}
          </li>
        ))}
      </ul>
      <div
        className="artist-resize-handle"
        onMouseDown={handleResizeMouseDown}
        onDoubleClick={handleResizeDoubleClick}
      />
    </aside>
  )
}
