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
  const showFlashToast = useStore((s) => s.showFlashToast)
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
        (a) =>
          (a.album === current_track.album || a.display_album === current_track.album) &&
          (a.album_artist === current_track.album_artist ||
            a.display_album_artist === current_track.album_artist)
      )
    : undefined

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
            currentAlbum?.album_artist ?? current_track.album_artist,
            currentAlbum?.album ?? current_track.album,
            {
              trackId: current_track.album ? null : current_track.id
            }
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
                showFlashToast(`Copied link to ${currentAlbum.album}`)
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
      </div>
    </div>
  )
}
