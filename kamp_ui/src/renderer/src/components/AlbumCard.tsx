import React, { useState, useEffect } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Album } from '../api/client'
import { AlbumContextMenu } from './AlbumContextMenu'
import { BandcampIcon, CloudIcon, FavoriteIcon, PlayIcon, WarnIcon } from './TransportIcons'

type MenuPos = { x: number; y: number }

function sourceIcon(source: string, size: number): React.JSX.Element {
  if (source === 'bandcamp') return <BandcampIcon size={size} />
  return <CloudIcon size={size} />
}

function rnd(min: number, max: number): number {
  return min + Math.random() * (max - min)
}

interface StarParticle {
  id: number
  left: number
  top: number
  duration: number
  delay: number
}

interface SparkParticle {
  id: number
  left: number
  top: number
  blinkDur: number
  blinkDelay: number
  sparkOpacity: number
}

// Frame 0 is the brightest gray — shown when prefers-reduced-motion is set.
// All frames use `from 180deg` so the 0/360deg seam lands at the bottom of the
// card (behind the album-info text) rather than the visible top edge.
// First and last stops match so the gradient closes without a color jump.
const STATIC_BORDER_FRAMES = [
  'conic-gradient(from 180deg, #bbb 0deg, #888 50deg, #aaa 110deg, #ccc 170deg, #999 220deg, #aaa 280deg, #bbb 360deg)',
  'conic-gradient(from 180deg, #222 0deg, #777 45deg, #111 90deg, #555 145deg, #333 195deg, #888 250deg, #111 310deg, #222 360deg)',
  'conic-gradient(from 180deg, #555 0deg, #111 55deg, #888 115deg, #222 165deg, #666 225deg, #333 280deg, #555 360deg)',
  'conic-gradient(from 180deg, #888 0deg, #333 70deg, #111 140deg, #666 205deg, #222 265deg, #888 360deg)',
  'conic-gradient(from 180deg, #111 0deg, #666 60deg, #333 125deg, #999 185deg, #444 245deg, #777 305deg, #111 360deg)',
  'conic-gradient(from 180deg, #333 0deg, #999 50deg, #111 105deg, #666 160deg, #444 215deg, #111 270deg, #333 360deg)',
  'conic-gradient(from 180deg, #777 0deg, #222 65deg, #555 135deg, #111 200deg, #888 270deg, #777 360deg)',
  'conic-gradient(from 180deg, #444 0deg, #888 80deg, #111 160deg, #777 220deg, #333 290deg, #444 360deg)'
]

export function AlbumCard({ album }: { album: Album }): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setActiveView = useStore((s) => s.setActiveView)
  const activeView = useStore((s) => s.activeView)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const highlightEnabled = useStore((s) => s.highlightEnabled)
  const highlightCutoffSecs = useStore((s) => s.highlightCutoffSecs)
  const highlightStyle = useStore((s) => s.highlightStyle)
  const dismissedHighlightKeys = useStore((s) => s.dismissedHighlightKeys)
  const dismissHighlight = useStore((s) => s.dismissHighlight)
  const configValues = useStore((s) => s.configValues)
  const downloadingAlbumIds = useStore((s) => s.downloadingAlbumIds)
  const connected = configValues?.['bandcamp.connected'] ?? false
  const isRemote = album.source !== 'local'
  const isOffline = isRemote && !connected
  const isDownloading = album.sale_item_id != null && downloadingAlbumIds.has(album.sale_item_id)
  const [artLoaded, setArtLoaded] = useState(false)
  const [menu, setMenu] = useState<MenuPos | null>(null)

  const isActive = album.missing_album
    ? currentTrack?.file_path === album.file_path
    : currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  const albumHighlightKey = album.missing_album
    ? (album.file_path ?? '')
    : `${album.album_artist}::${album.album}`
  const isNew =
    highlightEnabled &&
    album.added_at !== null &&
    album.added_at >= highlightCutoffSecs &&
    album.last_played_at === null &&
    !dismissedHighlightKeys.has(albumHighlightKey)

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

  // Start mounting=true so the fast sweep fires immediately; cleared after 1.2s
  const [isMounting, setIsMounting] = useState(isNew)
  const [starParticles, setStarParticles] = useState<StarParticle[]>([])
  const [sparkParticles, setSparkParticles] = useState<SparkParticle[]>([])
  const [hoverSparkParticles, setHoverSparkParticles] = useState<SparkParticle[]>([])
  const [isHovered, setIsHovered] = useState(false)
  const [auraActive, setAuraActive] = useState(false)
  const [borderFrame, setBorderFrame] = useState(0)

  useEffect(() => {
    if (!isNew) return
    // Math.random() and setState must be in callbacks, not the effect body directly
    const initTimer = setTimeout(() => {
      const count = 3 + Math.floor(Math.random() * 3) // 3–5
      setStarParticles(
        Array.from({ length: count }, (_, i) => ({
          id: i,
          left: 10 + Math.random() * 80,
          top: 15 + Math.random() * 50,
          duration: 2.8 + Math.random() * 1.6,
          delay: Math.random() * 2
        }))
      )
      const sparkCount = 25 + Math.floor(Math.random() * 16) // 25–40
      setSparkParticles(
        Array.from({ length: sparkCount }, (_, i) => ({
          id: i,
          left: rnd(5, 85),
          top: rnd(5, 85),
          blinkDur: rnd(0.08, 0.22),
          blinkDelay: rnd(0, 0.5),
          sparkOpacity: rnd(0.4, 1.0)
        }))
      )
      // pre-generate hover spark positions so they don't jump on every hover
      setHoverSparkParticles(
        Array.from({ length: 6 }, (_, i) => ({
          id: i + 100,
          left: rnd(5, 85),
          top: rnd(5, 85),
          blinkDur: rnd(0.3, 0.5),
          blinkDelay: rnd(0, 0.5),
          sparkOpacity: rnd(0.4, 1.0)
        }))
      )
    }, 0)
    const mountTimer = setTimeout(() => setIsMounting(false), 1200)
    return () => {
      clearTimeout(initTimer)
      clearTimeout(mountTimer)
    }
  }, [isNew])

  // Randomize spark positions over time — updating top/left without touching the
  // animation props so blink cycles continue uninterrupted (no jarring reset)
  useEffect(() => {
    if (!isNew || highlightStyle !== 'static') return
    const id = setInterval(() => {
      setSparkParticles((prev) => prev.map((p) => ({ ...p, left: rnd(5, 85), top: rnd(5, 85) })))
    }, 150)
    return () => clearInterval(id)
  }, [isNew, highlightStyle])

  // Gradient border: cycle through gray/black frames at random ~60–150ms intervals.
  // Skip when prefers-reduced-motion is set — frame 0 (brightest) stays active.
  useEffect(() => {
    if (!isNew || highlightStyle !== 'static') return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    let cancelled = false
    const schedule = (): void => {
      setTimeout(
        () => {
          if (cancelled) return
          setBorderFrame(Math.floor(Math.random() * STATIC_BORDER_FRAMES.length))
          schedule()
        },
        rnd(60, 150)
      )
    }
    schedule()
    return () => {
      cancelled = true
    }
  }, [isNew, highlightStyle])

  // White aura that fires at random intervals — like a voltage surge on a CRT
  useEffect(() => {
    if (!isNew || highlightStyle !== 'static') return
    let cancelled = false

    const schedule = (): void => {
      setTimeout(
        () => {
          if (cancelled) return
          setAuraActive(true)
          setTimeout(
            () => {
              if (cancelled) return
              setAuraActive(false)
              schedule()
            },
            rnd(10, 300)
          )
        },
        rnd(30, 1000)
      )
    }

    schedule()
    return () => {
      cancelled = true
    }
  }, [isNew, highlightStyle])

  const handleSelect = (): void => {
    if (activeView !== 'library') void setActiveView('library')
    void selectAlbum(album)
  }

  const cardClass = [
    'album-card',
    isActive ? 'playing' : '',
    isRemote ? 'album-card--remote' : '',
    isOffline ? 'album-card--offline' : '',
    isDownloading ? 'album-card--downloading' : '',
    isNew ? `album-card--highlight-${highlightStyle}` : '',
    isNew && isMounting ? 'is-mounting' : ''
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={cardClass}
      style={
        isNew && highlightStyle === 'static'
          ? ({
              '--static-border-gradient': STATIC_BORDER_FRAMES[borderFrame]
            } as React.CSSProperties)
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
            file_path: album.file_path
          })
        )
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div
        className={`album-art${artLoaded ? ' has-art' : ''}${artBlurred ? ' album-art--blurred' : ''}`}
      >
        {album.has_art && (
          <img
            className="album-art-img"
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => setArtLoaded(false)}
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
            <em>{album.album}</em>
          </div>
        ) : (
          <div className="album-title">{album.album}</div>
        )}
        <div className="album-artist">{album.album_artist}</div>
        <div className="album-year">{album.year}</div>
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
