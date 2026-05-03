import React, { useEffect, useState } from 'react'
import { getAlbums } from '../../api/client'
import type { Album } from '../../api/client'
import { AlbumCard } from '../AlbumCard'
import { useStore } from '../../store'

export function LastPlayedModule(): React.JSX.Element {
  const count = useStore((s) => s.lastPlayedCount)
  // Re-fetch whenever the current track changes — the server updates last_played
  // at EOF before broadcasting track.changed, so the list is already stale by
  // the time this selector fires.
  const currentFilePath = useStore((s) => s.player?.current_track?.file_path ?? null)
  const [albums, setAlbums] = useState<Album[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getAlbums('last_played')
      .then((all) => {
        const played = all.filter((a) => a.last_played_at !== null).slice(0, count)
        setAlbums(played)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [count, currentFilePath])

  if (loading) {
    return (
      <div className="module-skeleton-row">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="module-skeleton-card" />
        ))}
      </div>
    )
  }

  if (albums.length === 0) {
    return <div className="module-empty">No albums played yet.</div>
  }

  return (
    <div className="module-last-played">
      {albums.map((album) => (
        <AlbumCard
          key={album.missing_album ? album.file_path : `${album.album_artist}\0${album.album}`}
          album={album}
        />
      ))}
    </div>
  )
}
