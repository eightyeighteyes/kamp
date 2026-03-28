import React from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'

export function TrackList(): React.JSX.Element | null {
  const album = useStore((s) => s.library.selectedAlbum)
  const tracks = useStore((s) => s.library.tracks)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const playTrack = useStore((s) => s.playTrack)
  const togglePlayPause = useStore((s) => s.togglePlayPause)

  if (!album) return null

  const isCurrentAlbum =
    currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  return (
    <div className="track-list-view">
      <div className="track-list-header">
        <button className="back-btn" onClick={() => selectAlbum(null)}>
          ← Albums
        </button>
        <div className="track-list-album-info">
          {album.has_art && (
            <img
              className="track-list-album-art"
              src={artUrl(album.album_artist, album.album)}
              alt=""
            />
          )}
          <div className="track-list-album-text">
            <span className="track-list-album-title">{album.album}</span>
            <span className="track-list-album-artist">{album.album_artist}</span>
            <span className="track-list-album-year">{album.year}</span>
          </div>
        </div>
        <button
          className="play-all-btn"
          onClick={() => playTrack(album.album_artist, album.album, 0)}
        >
          ▶ Play All
        </button>
      </div>

      <ol className="track-rows">
        {tracks.map((track, i) => {
          const isCurrent = isCurrentAlbum && currentTrack?.track_number === track.track_number
          return (
            <li
              key={`${track.disc_number}-${track.track_number}`}
              className={`track-row${isCurrent ? ' current' : ''}`}
              tabIndex={0}
              onClick={() => {
                if (isCurrent) {
                  togglePlayPause()
                } else {
                  playTrack(album.album_artist, album.album, i)
                }
              }}
              onKeyDown={(e) => {
                if (e.key !== 'Enter') return
                if (isCurrent) togglePlayPause()
                else playTrack(album.album_artist, album.album, i)
              }}
            >
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
  )
}
