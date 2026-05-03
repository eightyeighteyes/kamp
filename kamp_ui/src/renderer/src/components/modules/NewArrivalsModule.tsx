import React, { useEffect, useState } from 'react'
import { getAlbums } from '../../api/client'
import type { Album } from '../../api/client'
import { AlbumCard } from '../AlbumCard'

const DAYS = 30
const CUTOFF_SECONDS = DAYS * 86400

export function NewArrivalsModule(): React.JSX.Element {
  const [albums, setAlbums] = useState<Album[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getAlbums('date_added')
      .then((all) => {
        const cutoff = Date.now() / 1000 - CUTOFF_SECONDS
        const recent = all.filter((a) => a.added_at !== null && a.added_at >= cutoff)
        setAlbums(recent)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

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
    return <div className="module-empty">No albums added in the last {DAYS} days.</div>
  }

  return (
    <div className="module-new-arrivals">
      {albums.map((album) => (
        <AlbumCard
          key={album.missing_album ? album.file_path : `${album.album_artist}\0${album.album}`}
          album={album}
        />
      ))}
    </div>
  )
}
