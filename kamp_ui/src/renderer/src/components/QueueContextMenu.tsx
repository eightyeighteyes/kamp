import React from 'react'
import { useStore } from '../store'
import { ContextMenu } from './ContextMenu'

interface Props {
  x: number
  y: number
  albumArtist?: string
  album?: string
  trackIdx: number | null
  onClose: () => void
}

export function QueueContextMenu({
  x,
  y,
  albumArtist,
  album,
  trackIdx,
  onClose
}: Props): React.JSX.Element {
  const albums = useStore((s) => s.library.albums)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)
  const clearQueue = useStore((s) => s.clearQueue)
  const clearRemainingQueue = useStore((s) => s.clearRemainingQueue)

  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      {albumArtist && album && (
        <>
          <button
            className="track-context-menu-item"
            onClick={() => {
              const found = albums.find(
                (a) => a.album_artist === albumArtist && a.album === album
              ) ?? {
                album_artist: albumArtist,
                album,
                year: '',
                track_count: 0,
                has_art: false,
                missing_album: false,
                file_path: '',
                art_version: null,
                added_at: null,
                last_played_at: null,
                play_count_avg: 0
              }
              void setActiveView('library')
              void selectAlbum(found)
              onClose()
            }}
          >
            ⌾ Go to Album
          </button>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void setActiveView('library')
              selectArtist(albumArtist)
              onClose()
            }}
          >
            ♫ Go to Artist
          </button>
          <div className="track-context-menu-divider" />
        </>
      )}
      <button
        className="track-context-menu-item"
        onClick={() => {
          void clearQueue()
          onClose()
        }}
      >
        Clear Queue
      </button>
      {trackIdx !== null && (
        <button
          className="track-context-menu-item"
          onClick={() => {
            void clearRemainingQueue(trackIdx)
            onClose()
          }}
        >
          Clear Remaining
        </button>
      )}
    </ContextMenu>
  )
}
