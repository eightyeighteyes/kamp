import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { QueueContextMenu } from './QueueContextMenu'

const QUEUE_DROP_TYPES = new Set(['text/kamp-track-path', 'text/kamp-album', 'text/kamp-queue-idx'])
function isQueueDrop(types: DOMStringList | readonly string[]): boolean {
  return Array.from(types).some((t) => QUEUE_DROP_TYPES.has(t))
}

type ContextMenu = {
  x: number
  y: number
  trackIdx: number | null
  albumArtist?: string
  album?: string
  filePath?: string
  favorite?: boolean
}

export function QueuePanel(): React.JSX.Element {
  const queue = useStore((s) => s.queue)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)
  const moveQueueTrack = useStore((s) => s.moveQueueTrack)
  const skipToQueueTrack = useStore((s) => s.skipToQueueTrack)
  const addToQueue = useStore((s) => s.addToQueue)
  const insertIntoQueue = useStore((s) => s.insertIntoQueue)
  const insertAlbumAt = useStore((s) => s.insertAlbumAt)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const activeRef = useRef<HTMLLIElement>(null)
  const listRef = useRef<HTMLOListElement>(null)
  const hasMounted = useRef(false)
  const [menu, setMenu] = useState<ContextMenu | null>(null)

  const tracks = queue?.tracks ?? []
  const position = queue?.position ?? -1

  // Scroll so that up to 5 history rows are visible above the current track.
  // On initial mount use instant scroll to avoid a visible jump from the top;
  // only animate when the position advances during an active session.
  useEffect(() => {
    const behavior: ScrollBehavior = hasMounted.current ? 'smooth' : 'instant'
    hasMounted.current = true
    const list = listRef.current
    const active = activeRef.current
    if (!list || !active || position < 5) {
      // For the first few tracks just ensure the active row is visible.
      activeRef.current?.scrollIntoView({ block: 'nearest', behavior })
      return
    }
    const rowHeight = active.offsetHeight
    list.scrollTo({ top: (position - 5) * rowHeight, behavior })
  }, [position])

  function handleDrop(e: React.DragEvent, dropIdx: number): void {
    e.stopPropagation()
    e.currentTarget.classList.remove('drag-over')
    listRef.current?.classList.remove('queue-tail-drop')
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
        const {
          album_artist,
          album,
          file_path = ''
        } = JSON.parse(albumJson) as {
          album_artist: string
          album: string
          file_path?: string
        }
        void insertAlbumAt(album_artist, album, dropIdx, file_path)
      } catch {
        // malformed drag data — ignore
      }
    }
  }

  function handleListDrop(e: React.DragEvent): void {
    // Fires only when dropping on the empty space below all track rows (li handlers
    // stop propagation, so this never fires when the target is a track row).
    e.currentTarget.classList.remove('queue-tail-drop')
    const queueIdx = e.dataTransfer.getData('text/kamp-queue-idx')
    const trackPath = e.dataTransfer.getData('text/kamp-track-path')
    const albumJson = e.dataTransfer.getData('text/kamp-album')
    if (queueIdx !== '') {
      const from = Number(queueIdx)
      const last = tracks.length - 1
      if (from !== last) void moveQueueTrack(from, last)
    } else if (trackPath) {
      void addToQueue(trackPath)
    } else if (albumJson) {
      try {
        const {
          album_artist,
          album,
          file_path = ''
        } = JSON.parse(albumJson) as {
          album_artist: string
          album: string
          file_path?: string
        }
        void addAlbumToQueue(album_artist, album, file_path)
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
          onDragOver={(e) => {
            if (!isQueueDrop(e.dataTransfer.types)) return
            e.preventDefault()
          }}
          onDrop={(e) => {
            const trackPath = e.dataTransfer.getData('text/kamp-track-path')
            const albumJson = e.dataTransfer.getData('text/kamp-album')
            if (trackPath) {
              void addToQueue(trackPath)
            } else if (albumJson) {
              try {
                const {
                  album_artist,
                  album,
                  file_path = ''
                } = JSON.parse(albumJson) as {
                  album_artist: string
                  album: string
                  file_path?: string
                }
                void addAlbumToQueue(album_artist, album, file_path)
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
          ref={listRef}
          className="queue-track-list"
          onContextMenu={(e) => {
            e.preventDefault()
            setMenu({ x: e.clientX, y: e.clientY, trackIdx: null })
          }}
          onDragOver={(e) => {
            if (!isQueueDrop(e.dataTransfer.types)) return
            e.preventDefault()
            e.currentTarget.classList.add('queue-tail-drop')
          }}
          onDragLeave={(e) => {
            // Only remove the indicator when the pointer leaves the <ol> entirely,
            // not when entering a child <li> (which stops its own drag events).
            if (!e.currentTarget.contains(e.relatedTarget as Node)) {
              e.currentTarget.classList.remove('queue-tail-drop')
            }
          }}
          onDrop={handleListDrop}
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
                draggable={!isCurrent}
                onDragStart={(e) => {
                  if (isCurrent) return
                  e.dataTransfer.setData('text/kamp-queue-idx', String(idx))
                  e.dataTransfer.effectAllowed = 'move'
                }}
                onDragOver={(e) => {
                  if (!isQueueDrop(e.dataTransfer.types)) return
                  e.preventDefault()
                  e.stopPropagation()
                  e.currentTarget.classList.add('drag-over')
                  // Clear tail-drop outline when pointer enters a row.
                  listRef.current?.classList.remove('queue-tail-drop')
                }}
                onDragLeave={(e) => {
                  e.stopPropagation()
                  e.currentTarget.classList.remove('drag-over')
                }}
                onDrop={(e) => handleDrop(e, idx)}
                onDoubleClick={() => void skipToQueueTrack(idx)}
                onContextMenu={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  setMenu({
                    x: e.clientX,
                    y: e.clientY,
                    trackIdx: isUnplayed ? idx : null,
                    albumArtist: track.album_artist,
                    album: track.album,
                    filePath: track.file_path,
                    favorite: track.favorite
                  })
                }}
              >
                <span className="queue-track-fav" aria-hidden="true">
                  {track.favorite ? '♥' : ''}
                </span>
                <span className="queue-track-num">{idx + 1}</span>
                <span className="queue-track-title">{track.title}</span>
                <span className="queue-track-artist">{track.artist}</span>
              </li>
            )
          })}
        </ol>
      )}
      {menu && (
        <QueueContextMenu
          x={menu.x}
          y={menu.y}
          albumArtist={menu.albumArtist}
          album={menu.album}
          trackIdx={menu.trackIdx}
          filePath={menu.filePath}
          favorite={menu.favorite}
          onClose={() => setMenu(null)}
        />
      )}
    </aside>
  )
}
