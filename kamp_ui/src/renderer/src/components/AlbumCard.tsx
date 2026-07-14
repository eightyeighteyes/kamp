import React, { useState, useEffect } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Album } from '../api/client'
import { AlbumContextMenu } from './AlbumContextMenu'
import { BandcampIcon, CloudIcon, FavoriteIcon, PlayIcon, WarnIcon } from './TransportIcons'
import { useNewArrivalHighlight } from './useNewArrivalHighlight'

type MenuPos = { x: number; y: number }

function sourceIcon(source: string, size: number): React.JSX.Element {
  if (source === 'bandcamp') return <BandcampIcon size={size} />
  return <CloudIcon size={size} />
}

export function AlbumCard({
  album,
  onAfterSelect,
  showPlayCount = false,
  dragTrackIds
}: {
  album: Album
  onAfterSelect?: () => void
  showPlayCount?: boolean
  // KAMP-552: album-group drags (e.g. from a playlist) carry canonical track ids.
  dragTrackIds?: number[]
}): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setActiveView = useStore((s) => s.setActiveView)
  const activeView = useStore((s) => s.activeView)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const configValues = useStore((s) => s.configValues)
  const downloadingAlbumIds = useStore((s) => s.downloadingAlbumIds)
  const queuedAlbumIds = useStore((s) => s.queuedAlbumIds)
  const downloadProgress = useStore((s) => s.downloadProgress)
  const connected = configValues?.['bandcamp.connected'] ?? false
  const isRemote = album.source !== 'local'
  const isOffline = isRemote && !connected
  const isDownloading = album.sale_item_id != null && downloadingAlbumIds.has(album.sale_item_id)
  const isQueued = album.sale_item_id != null && queuedAlbumIds.has(album.sale_item_id)
  // KAMP-436: a known byte-progress percent drives the bottom-up art reveal;
  // when it's absent the card keeps the indeterminate download pulse.
  const progress = album.sale_item_id != null ? downloadProgress.get(album.sale_item_id) : undefined
  const isRevealing = isDownloading && typeof progress === 'number'
  const [artLoaded, setArtLoaded] = useState(false)
  const [artError, setArtError] = useState(false)
  const [menu, setMenu] = useState<MenuPos | null>(null)

  const isActive = album.missing_album
    ? album.track_id != null && currentTrack?.id === album.track_id
    : currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  const {
    isNew,
    highlightStyle,
    isMounting,
    starParticles,
    sparkParticles,
    hoverSparkParticles,
    isHovered,
    setIsHovered,
    auraActive,
    staticBorderGradient,
    dismissHighlight
  } = useNewArrivalHighlight(album)

  // Dismiss the highlight the first time this album becomes the active playing track.
  useEffect(() => {
    if (isNew && isActive && playing) dismissHighlight(album)
  }, [isNew, isActive, playing]) // eslint-disable-line react-hooks/exhaustive-deps

  // artBlurred persists through the post-download rescan window:
  // set true when download starts, cleared only when has_art returns true.
  // setState is inside setTimeout so it's a callback, not synchronous in the effect.
  const [artBlurred, setArtBlurred] = useState(isDownloading)
  useEffect(() => {
    if (!isDownloading) return
    const t = setTimeout(() => setArtBlurred(true), 0)
    return () => clearTimeout(t)
  }, [isDownloading])
  useEffect(() => {
    if (isDownloading || !album.has_art) return
    const t = setTimeout(() => setArtBlurred(false), 0)
    return () => clearTimeout(t)
  }, [album.has_art, isDownloading])

  const handleSelect = (): void => {
    if (activeView !== 'library') void setActiveView('library')
    void selectAlbum(album)
    onAfterSelect?.()
  }

  const cardClass = [
    'album-card',
    isActive ? 'playing' : '',
    isRemote ? 'album-card--remote' : '',
    isOffline ? 'album-card--offline' : '',
    isDownloading ? 'album-card--downloading' : '',
    isQueued ? 'album-card--queued' : '',
    isNew ? `album-card--highlight-${highlightStyle}` : '',
    isNew && isMounting ? 'is-mounting' : ''
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={cardClass}
      style={
        staticBorderGradient !== undefined
          ? ({ '--static-border-gradient': staticBorderGradient } as React.CSSProperties)
          : undefined
      }
      tabIndex={0}
      draggable
      onClick={handleSelect}
      onKeyDown={(e) => e.key === 'Enter' && handleSelect()}
      onMouseEnter={isNew && highlightStyle === 'static' ? () => setIsHovered(true) : undefined}
      onMouseLeave={isNew && highlightStyle === 'static' ? () => setIsHovered(false) : undefined}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY })
      }}
      onDragStart={(e) => {
        if (dragTrackIds && dragTrackIds.length > 0) {
          e.dataTransfer.setData('text/kamp-track-ids', JSON.stringify(dragTrackIds))
          const count = dragTrackIds.length
          const ghost = document.createElement('div')
          ghost.textContent = `${count} track${count === 1 ? '' : 's'}`
          ghost.style.cssText =
            'position:fixed;top:-100px;background:var(--accent);color:#fff;padding:4px 10px;border-radius:3px;font-size:12px;font-weight:600'
          document.body.appendChild(ghost)
          e.dataTransfer.setDragImage(ghost, 0, 0)
          requestAnimationFrame(() => document.body.removeChild(ghost))
        } else {
          e.dataTransfer.setData(
            'text/kamp-album',
            JSON.stringify({
              album_artist: album.album_artist,
              album: album.album,
              track_id: album.track_id
            })
          )
        }
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div
        className={`album-art${artLoaded ? ' has-art' : ''}${artBlurred ? ' album-art--blurred' : ''}${isRevealing ? ' album-art--revealing' : ''}`}
        style={isRevealing ? ({ '--reveal': progress } as React.CSSProperties) : undefined}
      >
        {album.has_art && !artError && (
          <img
            className="album-art-img"
            src={artUrl(album.album_artist, album.album, {
              trackId: album.track_id,
              version: album.art_version
            })}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => {
              setArtLoaded(false)
              setArtError(true)
            }}
          />
        )}
        {isRevealing && album.has_art && !artError && (
          // Sharp copy of the art, masked to reveal from the bottom up as the
          // download progresses. Same src → served from cache, no extra fetch.
          <img
            className="album-art-reveal"
            src={artUrl(album.album_artist, album.album, {
              trackId: album.track_id,
              version: album.art_version
            })}
            alt=""
            aria-hidden="true"
          />
        )}
        {playing && isActive && (
          <div className="now-playing-badge">
            <PlayIcon size={10} />
          </div>
        )}
        {isOffline && (
          <div className="album-art-offline-overlay" aria-hidden="true">
            <div className="album-art-offline-msg">
              <WarnIcon size={20} />
              <span>Not available</span>
            </div>
          </div>
        )}
        {album.is_preorder && (
          <div className="album-preorder-banner" aria-label="Pre-Order">
            pre-order
          </div>
        )}
        {isNew && highlightStyle === 'shiny' && <span className="shiny-sweep" aria-hidden="true" />}
        {isNew && highlightStyle === 'boring' && (
          <span className="boring-hover" aria-hidden="true">
            wow!
          </span>
        )}
        {isNew && highlightStyle === 'vaporwave' && (
          <span className="vaporwave-scanlines" aria-hidden="true" />
        )}
        {isNew && highlightStyle === 'pressed' && (
          <>
            <span className="pressed-glint" aria-hidden="true" />
            <span className="pressed-glint-hover" aria-hidden="true" />
          </>
        )}
        {isNew && highlightStyle === 'static' && (
          <div className="static-aura" style={{ opacity: auraActive ? 1 : 0 }} aria-hidden="true" />
        )}
        {isNew && highlightStyle === 'static' && (
          <div
            className="static-sparks"
            style={{ '--spark-speed-mult': isHovered ? 1.4 : 1 } as React.CSSProperties}
            aria-hidden="true"
          >
            {sparkParticles.map((p) => (
              <span
                key={p.id}
                className="static-spark"
                style={
                  {
                    '--blink-dur': `${p.blinkDur}s`,
                    '--blink-delay': `${p.blinkDelay}s`,
                    '--spark-opacity': p.sparkOpacity,
                    top: `${p.top}%`,
                    left: `${p.left}%`
                  } as React.CSSProperties
                }
              />
            ))}
            {isHovered &&
              hoverSparkParticles.map((p) => (
                <span
                  key={p.id}
                  className="static-spark"
                  style={
                    {
                      '--blink-dur': `${p.blinkDur}s`,
                      '--blink-delay': `${p.blinkDelay}s`,
                      '--spark-opacity': p.sparkOpacity,
                      top: `${p.top}%`,
                      left: `${p.left}%`
                    } as React.CSSProperties
                  }
                />
              ))}
          </div>
        )}
      </div>

      {isNew &&
        highlightStyle === 'shiny' &&
        starParticles.map((p) => (
          <span
            key={p.id}
            className="shiny-star"
            aria-hidden="true"
            style={
              {
                '--star-left': `${p.left}%`,
                '--star-top': `${p.top}%`,
                '--star-dur': `${p.duration}s`,
                '--star-delay': `${p.delay}s`
              } as React.CSSProperties
            }
          />
        ))}

      <div className="album-info">
        {isNew && highlightStyle === 'newmoji' && (
          <span className="newmoji-badge" aria-hidden="true">
            🆕
          </span>
        )}
        {isRemote && (
          <div className="album-source-badge" aria-label={`Remote source: ${album.source}`}>
            {sourceIcon(album.source, 10)}
          </div>
        )}
        {album.missing_album ? (
          <div className="album-title">
            <em>{album.display_album ?? album.album}</em>
          </div>
        ) : (
          <div className="album-title">{album.display_album ?? album.album}</div>
        )}
        <div className="album-artist">{album.display_album_artist ?? album.album_artist}</div>
        <div className="album-year">{album.release_date}</div>
        {showPlayCount && album.play_count_avg > 0 && (
          <div className="album-play-count">avg {album.play_count_avg.toFixed(1)}</div>
        )}
        {album.favorite && (
          <div className="album-fav-badge">
            <FavoriteIcon active size={14} />
          </div>
        )}
      </div>

      {menu && (
        <AlbumContextMenu x={menu.x} y={menu.y} album={album} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}
