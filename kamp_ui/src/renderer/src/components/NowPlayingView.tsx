import React, { useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'

export function NowPlayingView(): React.JSX.Element {
  const player = useStore((s) => s.player)
  const { current_track } = player
  const [artLoaded, setArtLoaded] = useState(false)

  if (!current_track) {
    return (
      <div className="now-playing-empty">
        <div className="now-playing-empty-icon">♫</div>
        <div className="now-playing-empty-hint">Nothing playing</div>
      </div>
    )
  }

  return (
    <div className="now-playing">
      <div className={`now-playing-art${artLoaded ? ' has-art' : ''}`}>
        <span className="now-playing-art-placeholder">♪</span>
        <img
          src={artUrl(current_track.album_artist, current_track.album)}
          onLoad={() => setArtLoaded(true)}
          onError={() => setArtLoaded(false)}
        />
      </div>
      <div className="now-playing-meta">
        <div className="now-playing-title">{current_track.title}</div>
        <div className="now-playing-artist">{current_track.artist}</div>
        <div className="now-playing-album">
          {current_track.album}
          {current_track.year ? ` · ${current_track.year}` : ''}
        </div>
      </div>
    </div>
  )
}
