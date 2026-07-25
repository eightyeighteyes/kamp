import React from 'react'
import type { Album } from '../../api/client'
import { AlbumCard } from '../AlbumCard'

interface GridViewProps {
  albums: Album[]
  showPlayCount?: boolean
}

export function GridView({ albums, showPlayCount = false }: GridViewProps): React.JSX.Element {
  return (
    <div className="album-grid module-grid">
      {albums.map((album) => (
        <AlbumCard
          key={
            album.missing_album ? `id:${album.track_id}` : `${album.album_artist}\0${album.album}`
          }
          album={album}
          showPlayCount={showPlayCount}
        />
      ))}
    </div>
  )
}
