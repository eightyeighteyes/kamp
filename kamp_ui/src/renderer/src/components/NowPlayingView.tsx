import React, { useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'

export function NowPlayingView(): React.JSX.Element {
  const player = useStore((s) => s.player)
  const { current_track } = player
  const [artLoaded, setArtLoaded] = useState(false)
  const albums = useStore((s) => s.library.albums)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)

  if (!current_track) {
    return (
      <div className="now-playing-empty">
        <div className="now-playing-empty-icon">♫</div>
        <div className="now-playing-empty-hint">Nothing playing</div>
      </div>
    )
  }

  function goToAlbum(): void {
    if (!current_track) return
    const album = albums.find(
      (a) => a.album === current_track.album && a.album_artist === current_track.album_artist
    )
    if (!album) return
    void setActiveView('library')
    void selectAlbum(album)
  }

  function goToArtist(): void {
    if (!current_track) return
    void setActiveView('library')
    selectArtist(current_track.album_artist)
  }

  return (
    <div className="now-playing">
      <div className={`now-playing-art${artLoaded ? ' has-art' : ''}`}>
        <span className="now-playing-art-placeholder">♪</span>
        <img
          src={artUrl(
            current_track.album_artist,
            current_track.album,
            current_track.album ? '' : current_track.file_path
          )}
          onLoad={() => setArtLoaded(true)}
          onError={() => setArtLoaded(false)}
        />
      </div>
      <div className="now-playing-meta">
        <div className="now-playing-title">{current_track.title}</div>
        <button className="now-playing-artist now-playing-link" onClick={goToArtist}>
          {current_track.artist}
        </button>
        <button className="now-playing-album now-playing-link" onClick={goToAlbum}>
          {current_track.album}
          {current_track.year ? ` · ${current_track.year}` : ''}
        </button>
      </div>
    </div>
  )
}
