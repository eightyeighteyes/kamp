import React from 'react'
import type { Album } from '../../api/client'
import { artUrl } from '../../api/client'
import { useStore } from '../../store'

interface ListViewProps {
  albums: Album[]
}

function ListRow({ album }: { album: Album }): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setActiveView = useStore((s) => s.setActiveView)
  const activeView = useStore((s) => s.activeView)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)

  const isActive = album.missing_album
    ? currentTrack?.file_path === album.file_path
    : currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  const handleSelect = (): void => {
    if (activeView !== 'library') void setActiveView('library')
    void selectAlbum(album)
  }

  return (
    <div
      className={`module-list-row${isActive ? ' playing' : ''}`}
      tabIndex={0}
      onClick={handleSelect}
      onKeyDown={(e) => e.key === 'Enter' && handleSelect()}
    >
      <div className="module-list-thumb">
        {album.has_art && (
          <img
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
            alt=""
          />
        )}
        {playing && isActive && <div className="module-list-playing-badge">▶</div>}
      </div>
      <div className="module-list-info">
        <div className="module-list-title">
          {album.missing_album ? <em>{album.album}</em> : album.album}
        </div>
        <div className="module-list-artist">{album.album_artist}</div>
      </div>
    </div>
  )
}

export function ListView({ albums }: ListViewProps): React.JSX.Element {
  return (
    <div className="module-list">
      {albums.map((album) => (
        <ListRow
          key={album.missing_album ? album.file_path : `${album.album_artist}\0${album.album}`}
          album={album}
        />
      ))}
    </div>
  )
}
