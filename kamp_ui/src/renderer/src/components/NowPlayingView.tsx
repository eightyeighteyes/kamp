import React, { useState } from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { artUrl } from '../api/client'
import { ContextMenu } from './ContextMenu'
import { ShareIcon } from './TransportIcons'
import { NowPlayingBackground } from './nowplaying/NowPlayingBackground'

// Owns artLoaded so hiding the art (unmounting this) resets it; otherwise
// re-showing would apply has-art before the fresh <img> loads. It stays mounted
// across same-album tracks, whose unchanged src never re-fires onLoad.
function NowPlayingArt({ src }: { src: string }): React.JSX.Element {
  const [artLoaded, setArtLoaded] = useState(false)
  return (
    <div className={`now-playing-art${artLoaded ? ' has-art' : ''}`}>
      <span className="now-playing-art-placeholder">♪</span>
      <img src={src} onLoad={() => setArtLoaded(true)} onError={() => setArtLoaded(false)} />
    </div>
  )
}

export function NowPlayingView({ active = false }: { active?: boolean }): React.JSX.Element {
  const player = useStore((s) => s.player)
  const { current_track } = player
  // The view stays mounted across "Nothing playing", so the menu records the
  // track it was opened on and only renders for that track, never at stale
  // coordinates on the next one.
  const [menu, setMenu] = useState<{ x: number; y: number; trackId: number } | null>(null)
  const deferredOps = useStore((s) => s.deferredOps)
  const albums = useStore((s) => s.library.albums)
  const showFlashToast = useStore((s) => s.showFlashToast)
  const artVisible = useStore((s) => s.nowPlayingArtVisible)
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

  const nowPlayingArtUrl = artUrl(
    currentAlbum?.album_artist ?? current_track.album_artist,
    currentAlbum?.album ?? current_track.album,
    { trackId: current_track.album ? null : current_track.id }
  )

  return (
    <div
      className="now-playing"
      // KAMP-769: the menu covers the whole panel so it survives hidden art.
      onContextMenu={(e) => {
        // ContextMenu portals, but React events still bubble through the
        // component tree; ignore right-clicks inside the open menu itself.
        if (!e.currentTarget.contains(e.target as Node)) return
        if (!currentAlbum?.album_url) return
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY, trackId: current_track.id })
      }}
    >
      {/* Always pass the art URL: the background derives its palette from it
          even when the art itself is hidden. */}
      <NowPlayingBackground active={active} artUrl={nowPlayingArtUrl} />
      {/* The title hides with the art: on its own it just floats over the
          background, so "art hidden" means the animation fills the panel. */}
      {artVisible && (
        <>
          <NowPlayingArt src={nowPlayingArtUrl} />
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
        </>
      )}
      {menu && menu.trackId === current_track.id && currentAlbum?.album_url && (
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
  )
}
