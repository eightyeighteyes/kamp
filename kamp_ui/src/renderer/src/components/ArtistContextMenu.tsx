import React from 'react'
import { useStore } from '../store'
import type { Artist } from '../api/client'
import { ContextMenu } from './ContextMenu'
import { PlayIcon, PlayNextIcon, QueueAddIcon } from './TransportIcons'

interface Props {
  x: number
  y: number
  artist: Artist
  onClose: () => void
}

export function ArtistContextMenu({ x, y, artist, onClose }: Props): React.JSX.Element {
  const albums = useStore((s) => s.library.albums)
  const playAlbum = useStore((s) => s.playAlbum)
  const playAlbumNext = useStore((s) => s.playAlbumNext)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)

  // All albums by this artist, ranked by play_count_avg descending.
  const artistAlbums = albums
    .filter((a) => a.album_artist === artist.name)
    .sort((a, b) => (b.play_count_avg ?? 0) - (a.play_count_avg ?? 0))

  const handlePlayNow = (): void => {
    if (artistAlbums.length === 0) return
    void (async () => {
      const [first, ...rest] = artistAlbums
      await playAlbum(first.album_artist, first.album, 0, first.track_id)
      // Append remaining albums in order after the first; addAlbumToQueue is
      // safe here because playAlbum just replaced the queue with only the first
      // album's tracks. insertAlbumAt with a track-level index is wrong when
      // the first album has more than one track.
      for (const a of rest) {
        await addAlbumToQueue(a.album_artist, a.album, a.track_id)
      }
    })()
    onClose()
  }

  const handlePlayNext = (): void => {
    // Insert in reverse so the top album ends up immediately after the current track.
    void (async () => {
      for (let i = artistAlbums.length - 1; i >= 0; i--) {
        await playAlbumNext(
          artistAlbums[i].album_artist,
          artistAlbums[i].album,
          artistAlbums[i].track_id
        )
      }
    })()
    onClose()
  }

  const handleAddToQueue = (): void => {
    void (async () => {
      for (const a of artistAlbums) {
        await addAlbumToQueue(a.album_artist, a.album, a.track_id)
      }
    })()
    onClose()
  }

  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      <button className="track-context-menu-item" onClick={handlePlayNow}>
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <PlayIcon size={12} />
        </span>
        Play Artist Now
      </button>
      <button className="track-context-menu-item" onClick={handlePlayNext}>
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <PlayNextIcon size={12} />
        </span>
        Play Artist Next
      </button>
      <button className="track-context-menu-item" onClick={handleAddToQueue}>
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <QueueAddIcon size={12} />
        </span>
        Add Artist to Queue
      </button>
    </ContextMenu>
  )
}
