import React, { useState } from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { artUrl } from '../api/client'
import { ContextMenu } from './ContextMenu'
import { ShareIcon } from './TransportIcons'

export function NowPlayingView(): React.JSX.Element {
  const player = useStore((s) => s.player)
  const { current_track } = player
  const [artLoaded, setArtLoaded] = useState(false)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const deferredOps = useStore((s) => s.deferredOps)
  const albums = useStore((s) => s.library.albums)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)
  const tooltip = useTooltip()

  if (!current_track) {
    return (
      <div className="now-playing-empty">
        <div className="now-playing-empty-icon">♫</div>
        <div className="now-playing-empty-hint">Nothing playing</div>
      </div>
    )
  }

  const currentAlbum = current_track
    ? albums.find(
        (a) => a.album === current_track.album && a.album_artist === current_track.album_artist
      )
    : undefined

  function goToAlbum(): void {
    if (!currentAlbum) return
    void setActiveView('library')
    void selectAlbum(currentAlbum)
  }

  function goToArtist(): void {
    if (!current_track) return
    void setActiveView('library')
    selectArtist(current_track.album_artist)
  }

  return (
    <div className="now-playing">
      <div
        className={`now-playing-art${artLoaded ? ' has-art' : ''}`}
        onContextMenu={(e) => {
          if (!currentAlbum?.album_url) return
          e.preventDefault()
          setMenu({ x: e.clientX, y: e.clientY })
        }}
      >
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
        {menu && currentAlbum?.album_url && (
          <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)}>
            <button
              className="track-context-menu-item"
              onClick={() => {
                void navigator.clipboard.writeText(currentAlbum.album_url!)
                setMenu(null)
              }}
            >
              <span
                style={{
                  marginRight: 6,
                  verticalAlign: 'middle',
                  flexShrink: 0,
                  display: 'inline-flex'
                }}
              >
                <ShareIcon size={12} />
              </span>
              Copy Bandcamp link
            </button>
          </ContextMenu>
        )}
      </div>
      <div className="now-playing-meta">
        <div className="now-playing-title">
          {current_track.title}
          {current_track.id in deferredOps && (
            <span
              className="deferred-op-pip"
              {...tooltip(TOOLTIPS.META_WILL_REORGANIZE)}
              aria-label="Pending rename"
            />
          )}
        </div>
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
