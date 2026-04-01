import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

type ContextMenu = { x: number; y: number; trackIdx: number | null }

export function QueuePanel(): React.JSX.Element {
  const queue = useStore((s) => s.queue)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)
  const moveQueueTrack = useStore((s) => s.moveQueueTrack)
  const skipToQueueTrack = useStore((s) => s.skipToQueueTrack)
  const clearQueue = useStore((s) => s.clearQueue)
  const clearRemainingQueue = useStore((s) => s.clearRemainingQueue)
  const addToQueue = useStore((s) => s.addToQueue)
  const insertIntoQueue = useStore((s) => s.insertIntoQueue)
  const insertAlbumAt = useStore((s) => s.insertAlbumAt)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const activeRef = useRef<HTMLLIElement>(null)
  const [menu, setMenu] = useState<ContextMenu | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const tracks = queue?.tracks ?? []
  const position = queue?.position ?? -1

  // Scroll the active track into view whenever it changes.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' })
  }, [position])

  // Dismiss context menu on click outside.
  useEffect(() => {
    if (!menu) return
    const handler = (e: MouseEvent): void => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenu(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menu])

  function handleDrop(e: React.DragEvent, dropIdx: number): void {
    e.currentTarget.classList.remove('drag-over')
    const queueIdx = e.dataTransfer.getData('text/kamp-queue-idx')
    const trackPath = e.dataTransfer.getData('text/kamp-track-path')
    const albumJson = e.dataTransfer.getData('text/kamp-album')
    if (queueIdx !== '') {
      const from = Number(queueIdx)
      if (from !== dropIdx) void moveQueueTrack(from, dropIdx)
    } else if (trackPath) {
      void insertIntoQueue(trackPath, dropIdx)
    } else if (albumJson) {
      try {
        const { album_artist, album } = JSON.parse(albumJson) as {
          album_artist: string
          album: string
        }
        void insertAlbumAt(album_artist, album, dropIdx)
      } catch {
        // malformed drag data — ignore
      }
    }
  }

  return (
    <aside className="queue-panel">
      <div className="queue-panel-header">
        <span className="queue-panel-label">QUEUE</span>
        <button className="queue-close-btn" onClick={toggleQueuePanel} title="Close queue">
          ✕
        </button>
      </div>
      {tracks.length === 0 ? (
        <div
          className="queue-empty"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            const trackPath = e.dataTransfer.getData('text/kamp-track-path')
            const albumJson = e.dataTransfer.getData('text/kamp-album')
            if (trackPath) {
              void addToQueue(trackPath)
            } else if (albumJson) {
              try {
                const { album_artist, album } = JSON.parse(albumJson) as {
                  album_artist: string
                  album: string
                }
                void addAlbumToQueue(album_artist, album)
              } catch {
                // malformed drag data — ignore
              }
            }
          }}
        >
          No tracks in queue.
        </div>
      ) : (
        <ol
          className="queue-track-list"
          onContextMenu={(e) => {
            e.preventDefault()
            setMenu({ x: e.clientX, y: e.clientY, trackIdx: null })
          }}
        >
          {tracks.map((track, idx) => {
            const isCurrent = idx === position
            const isPlayed = position >= 0 && idx < position
            const isUnplayed = position >= 0 && idx > position
            return (
              <li
                key={track.file_path}
                ref={isCurrent ? activeRef : null}
                className={`queue-track-row${isCurrent ? ' current' : ''}${isPlayed ? ' played' : ''}`}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('text/kamp-queue-idx', String(idx))
                  e.dataTransfer.effectAllowed = 'move'
                }}
                onDragOver={(e) => {
                  e.preventDefault()
                  e.currentTarget.classList.add('drag-over')
                }}
                onDragLeave={(e) => {
                  e.currentTarget.classList.remove('drag-over')
                }}
                onDrop={(e) => handleDrop(e, idx)}
                onDoubleClick={() => void skipToQueueTrack(idx)}
                onContextMenu={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  setMenu({ x: e.clientX, y: e.clientY, trackIdx: isUnplayed ? idx : null })
                }}
              >
                <span className="queue-track-num">{idx + 1}</span>
                <span className="queue-track-title">{track.title}</span>
                <span className="queue-track-artist">{track.artist}</span>
              </li>
            )
          })}
        </ol>
      )}
      {menu && (
        <div ref={menuRef} className="track-context-menu" style={{ top: menu.y, left: menu.x }}>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void clearQueue()
              setMenu(null)
            }}
          >
            Clear Queue
          </button>
          {menu.trackIdx !== null && (
            <button
              className="track-context-menu-item"
              onClick={() => {
                void clearRemainingQueue(menu.trackIdx as number)
                setMenu(null)
              }}
            >
              Clear Remaining
            </button>
          )}
        </div>
      )}
    </aside>
  )
}
