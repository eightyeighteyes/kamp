import React, { useState } from 'react'
import { artUrl } from '../api/client'
import type { Track } from '../api/client'

interface QueueAlbumCardProps {
  // KAMP-633: albumArtist/album are the CANONICAL albums-row key (art + nav);
  // displayAlbum/displayAlbumArtist are the render-only label (may be renamed).
  albumArtist: string
  album: string
  displayAlbum: string
  displayAlbumArtist: string
  artVersion: number | null
  tracks: Track[]
  trackIndices: number[]
  isDragging: boolean
  readOnly?: boolean
  onPointerDown?: (trackIndices: number[], startX: number, startY: number) => void
  onContextMenu?: (e: React.MouseEvent) => void
  // HTML5 drop handlers so external drags (library album/track/files) can target the card,
  // mirroring the track-row drop wiring. The card is drop-target only — it never sets draggable.
  onDragOver?: React.DragEventHandler<HTMLLIElement>
  onDragLeave?: React.DragEventHandler<HTMLLIElement>
  onDrop?: React.DragEventHandler<HTMLLIElement>
}

export function QueueAlbumCard({
  albumArtist,
  album,
  displayAlbum,
  displayAlbumArtist,
  artVersion,
  tracks,
  trackIndices,
  isDragging,
  readOnly,
  onPointerDown,
  onContextMenu,
  onDragOver,
  onDragLeave,
  onDrop
}: QueueAlbumCardProps): React.JSX.Element {
  const [artLoaded, setArtLoaded] = useState(false)
  const [artError, setArtError] = useState(false)
  const firstTrack = tracks[0]
  // Art resolves on the canonical key + version (KAMP-633); the label shows the
  // display name.
  const src = artUrl(albumArtist, album, { trackId: firstTrack?.id ?? null, version: artVersion })

  return (
    <li
      className={`queue-album-card${isDragging ? ' queue-album-card--dragging' : ''}${readOnly ? ' queue-album-card--read-only' : ''}`}
      data-drop-idx={readOnly ? undefined : trackIndices[0]}
      onPointerDown={
        readOnly
          ? undefined
          : (e) => {
              if (e.button !== 0) return
              e.preventDefault()
              onPointerDown!(trackIndices, e.clientX, e.clientY)
            }
      }
      onContextMenu={readOnly ? undefined : onContextMenu}
      onDragOver={readOnly ? undefined : onDragOver}
      onDragLeave={readOnly ? undefined : onDragLeave}
      onDrop={readOnly ? undefined : onDrop}
    >
      <div className="queue-album-card-art">
        {!artError && (
          <img
            src={src}
            alt=""
            draggable={false}
            onLoad={() => setArtLoaded(true)}
            onError={() => setArtError(true)}
            style={{ opacity: artLoaded ? 1 : 0 }}
          />
        )}
        {(!artLoaded || artError) && <span className="queue-album-card-art-placeholder">♪</span>}
      </div>
      <div className="queue-album-card-info">
        <span className="queue-album-card-name">{displayAlbum}</span>
        <span className="queue-album-card-artist">{displayAlbumArtist}</span>
      </div>
      <span className="queue-album-card-count">{tracks.length}</span>
    </li>
  )
}
