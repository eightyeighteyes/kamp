import React, { useState, useEffect } from 'react'
import type { Album } from '../../api/client'
import { artUrl } from '../../api/client'
import { useStore } from '../../store'
import { AlbumContextMenu } from '../AlbumContextMenu'
import { PlayIcon } from '../TransportIcons'
import { useNewArrivalHighlight } from '../useNewArrivalHighlight'

type MenuPos = { x: number; y: number }

interface ListViewProps {
  albums: Album[]
  showPlayCount?: boolean
}

function ListRow({
  album,
  showPlayCount = false
}: {
  album: Album
  showPlayCount?: boolean
}): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setActiveView = useStore((s) => s.setActiveView)
  const activeView = useStore((s) => s.activeView)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
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

  const handleSelect = (): void => {
    if (activeView !== 'library') void setActiveView('library')
    void selectAlbum(album)
  }

  const rowClass = [
    'module-list-row',
    isActive ? 'playing' : '',
    isNew ? `module-list-row--highlight-${highlightStyle}` : '',
    isNew && isMounting ? 'is-mounting' : ''
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={rowClass}
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
        e.dataTransfer.setData(
          'text/kamp-album',
          JSON.stringify({
            album_artist: album.album_artist,
            album: album.album,
            track_id: album.track_id
          })
        )
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className="module-list-thumb">
        {album.has_art && (
          <img
            src={artUrl(album.album_artist, album.album, {
              trackId: album.track_id,
              version: album.art_version
            })}
            alt=""
          />
        )}
        {playing && isActive && (
          <div className="module-list-playing-badge">
            <PlayIcon size={16} />
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
      <div className="module-list-info">
        {isNew && highlightStyle === 'newmoji' && (
          <span className="newmoji-badge" aria-hidden="true">
            🆕
          </span>
        )}
        <div className="module-list-title">
          {album.missing_album ? <em>{album.album}</em> : album.album}
        </div>
        <div className="module-list-artist">{album.album_artist}</div>
      </div>
      {showPlayCount && album.play_count_avg > 0 && (
        <span className="module-list-play-count">avg {album.play_count_avg.toFixed(1)}</span>
      )}
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
      {menu && (
        <AlbumContextMenu x={menu.x} y={menu.y} album={album} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}

export function ListView({ albums, showPlayCount = false }: ListViewProps): React.JSX.Element {
  return (
    <div className="module-list">
      {albums.map((album) => (
        <ListRow
          key={
            album.missing_album ? `id:${album.track_id}` : `${album.album_artist}\0${album.album}`
          }
          album={album}
          showPlayCount={showPlayCount}
        />
      ))}
    </div>
  )
}
