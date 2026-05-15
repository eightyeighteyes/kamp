import React from 'react'
import { useStore } from '../store'
import { ContextMenu } from './ContextMenu'
import { FavoriteIcon, GoToAlbumIcon, GoToArtistIcon } from './TransportIcons'

interface Props {
  x: number
  y: number
  albumArtist?: string
  album?: string
  trackIdx: number | null
  filePath?: string
  favorite?: boolean
  onClose: () => void
}

export function QueueContextMenu({
  x,
  y,
  albumArtist,
  album,
  trackIdx,
  filePath,
  favorite,
  onClose
}: Props): React.JSX.Element {
  const albums = useStore((s) => s.library.albums)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)
  const clearQueue = useStore((s) => s.clearQueue)
  const clearRemainingQueue = useStore((s) => s.clearRemainingQueue)
  const setFavorite = useStore((s) => s.setFavorite)

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
                play_count_avg: 0,
                favorite: false
              }
              void setActiveView('library')
              void selectAlbum(found)
              onClose()
            }}
          >
            <span
              style={{
                marginRight: 6,
                verticalAlign: 'middle',
                flexShrink: 0,
                display: 'inline-flex'
              }}
            >
              <GoToAlbumIcon size={12} />
            </span>
            Go to Album
          </button>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void setActiveView('library')
              selectArtist(albumArtist)
              onClose()
            }}
          >
            <span
              style={{
                marginRight: 6,
                verticalAlign: 'middle',
                flexShrink: 0,
                display: 'inline-flex'
              }}
            >
              <GoToArtistIcon size={12} />
            </span>
            Go to Artist
          </button>
          {filePath && (
            <button
              className="track-context-menu-item"
              onClick={() => {
                void setFavorite(filePath, !favorite)
                onClose()
              }}
            >
              <span
                style={{
                  marginRight: 6,
                  verticalAlign: 'middle',
                  flexShrink: 0,
                  display: 'inline-flex'
                }}
              >
                <FavoriteIcon active={!favorite} size={12} />
              </span>
              {favorite ? 'Remove from Favorites' : 'Add to Favorites'}
            </button>
          )}
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
