import React, { useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import { TrackContextMenu } from './TrackContextMenu'
import { FavoriteIcon } from './TransportIcons'

type ContextMenu = { x: number; y: number; filePath: string; favorite: boolean }

function HeroImage({ src }: { src: string }): React.JSX.Element {
  const [loaded, setLoaded] = useState(false)
  return (
    <img
      className={`track-list-hero-img${loaded ? ' loaded' : ''}`}
      src={src}
      alt=""
      onLoad={() => setLoaded(true)}
      onError={() => setLoaded(false)}
    />
  )
}

export function TrackList(): React.JSX.Element | null {
  const album = useStore((s) => s.library.selectedAlbum)
  const tracks = useStore((s) => s.library.tracks)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const playTrack = useStore((s) => s.playTrack)
  const togglePlayPause = useStore((s) => s.togglePlayPause)

  const [menu, setMenu] = useState<ContextMenu | null>(null)

  if (!album) return null

  const isCurrentAlbum =
    currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  return (
    <div className="track-list-view">
      {/* Hero: full-width art — image intentionally taller than hero to bleed into track list */}
      <div className={`track-list-hero${album.has_art ? ' has-art' : ''}`}>
        {album.has_art && (
          <HeroImage
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
          />
        )}
      </div>
      {/* Overlay spans the full view so the gradient covers both hero and the top of the track list */}
      <div className="track-list-hero-overlay" />

      {/* Back button floats over the hero */}
      <button className="back-btn" onClick={() => selectAlbum(null)} aria-label="Back to albums">
        ←
      </button>

      {/* Static identity block — does not scroll */}
      <div className="track-list-identity">
        <div className="track-list-identity-text">
          <h1 className="track-list-album-title">{album.album}</h1>
          <h2 className="track-list-album-artist">
            <button
              className="track-list-artist-link"
              onClick={() => {
                selectAlbum(null)
                selectArtist(album.album_artist)
              }}
            >
              {album.album_artist}
            </button>
          </h2>
          {album.year && <div className="track-list-album-year">{album.year}</div>}
        </div>
        <button
          className="play-all-btn"
          aria-label="Play all"
          onClick={() => playTrack(album.album_artist, album.album, 0, album.file_path)}
        >
          ▶
        </button>
      </div>

      <div className="track-list-divider" />

      {/* Scrollable body */}
      <div className="track-list-body">
        <ol className="track-rows">
          {tracks.map((track, i) => {
            const isCurrent = isCurrentAlbum && currentTrack?.track_number === track.track_number
            return (
              <li
                key={`${track.disc_number}-${track.track_number}`}
                className={`track-row${isCurrent ? ' current' : ''}`}
                tabIndex={0}
                onDoubleClick={() => {
                  if (isCurrent) {
                    togglePlayPause()
                  } else {
                    playTrack(album.album_artist, album.album, i, album.file_path)
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key !== 'Enter') return
                  if (isCurrent) togglePlayPause()
                  else playTrack(album.album_artist, album.album, i, album.file_path)
                }}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('text/kamp-track-path', track.file_path)
                  e.dataTransfer.effectAllowed = 'copy'
                }}
                onContextMenu={(e) => {
                  e.preventDefault()
                  setMenu({
                    x: e.clientX,
                    y: e.clientY,
                    filePath: track.file_path,
                    favorite: track.favorite
                  })
                }}
              >
                <span className="track-row-fav">
                  {track.favorite && <FavoriteIcon active size={10} />}
                </span>
                <span className="track-row-num">
                  {isCurrent ? (playing ? '▶' : '▐▐') : track.track_number}
                </span>
                <span className="track-row-title">{track.title}</span>
                <span className="track-row-artist">{track.artist}</span>
              </li>
            )
          })}
        </ol>
      </div>

      {menu && (
        <TrackContextMenu
          x={menu.x}
          y={menu.y}
          filePath={menu.filePath}
          favorite={menu.favorite}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
}
