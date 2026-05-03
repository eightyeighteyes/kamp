import React from 'react'
import type { Album } from '../../api/client'
import { AlbumCard } from '../AlbumCard'

interface GridViewProps {
  albums: Album[]
}

export function GridView({ albums }: GridViewProps): React.JSX.Element {
  return (
    <div className="album-grid module-grid">
      {albums.map((album) => (
        <AlbumCard
          key={album.missing_album ? album.file_path : `${album.album_artist}\0${album.album}`}
          album={album}
        />
      ))}
    </div>
  )
}
