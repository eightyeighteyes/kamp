import React, { useEffect, useRef } from 'react'
import { useStore } from '../store'

export function QueuePanel(): React.JSX.Element {
  const queue = useStore((s) => s.queue)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)
  const moveQueueTrack = useStore((s) => s.moveQueueTrack)
  const addToQueue = useStore((s) => s.addToQueue)
  const insertIntoQueue = useStore((s) => s.insertIntoQueue)
  const insertAlbumAt = useStore((s) => s.insertAlbumAt)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const activeRef = useRef<HTMLLIElement>(null)

  const tracks = queue?.tracks ?? []
  const position = queue?.position ?? -1

  // Scroll the active track into view whenever it changes.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' })
  }, [position])

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
        <ol className="queue-track-list">
          {tracks.map((track, idx) => {
            const isCurrent = idx === position
            const isPlayed = position >= 0 && idx < position
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
              >
                <span className="queue-track-num">{idx + 1}</span>
                <span className="queue-track-title">{track.title}</span>
                <span className="queue-track-artist">{track.artist}</span>
              </li>
            )
          })}
        </ol>
      )}
    </aside>
  )
}
