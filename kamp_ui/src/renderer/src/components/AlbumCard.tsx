import React, { useState, useEffect } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Album } from '../api/client'
import { AlbumContextMenu } from './AlbumContextMenu'

type MenuPos = { x: number; y: number }

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

export function AlbumCard({ album }: { album: Album }): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setActiveView = useStore((s) => s.setActiveView)
  const activeView = useStore((s) => s.activeView)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const highlightEnabled = useStore((s) => s.highlightEnabled)
  const highlightCutoffSecs = useStore((s) => s.highlightCutoffSecs)
  const highlightStyle = useStore((s) => s.highlightStyle)
  const [artLoaded, setArtLoaded] = useState(false)
  const [menu, setMenu] = useState<MenuPos | null>(null)

  const isActive = album.missing_album
    ? currentTrack?.file_path === album.file_path
    : currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  const isNew = highlightEnabled && album.added_at !== null && album.added_at >= highlightCutoffSecs

  // Start mounting=true so the fast sweep fires immediately; cleared after 1.2s
  const [isMounting, setIsMounting] = useState(isNew)
  const [starParticles, setStarParticles] = useState<StarParticle[]>([])
  const [sparkParticles, setSparkParticles] = useState<SparkParticle[]>([])
  const [hoverSparkParticles, setHoverSparkParticles] = useState<SparkParticle[]>([])
  const [isHovered, setIsHovered] = useState(false)
  const [auraActive, setAuraActive] = useState(false)

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
            rnd(60, 120)
          )
        },
        rnd(300, 4000)
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
    isNew ? `album-card--highlight-${highlightStyle}` : '',
    isNew && isMounting ? 'is-mounting' : ''
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={cardClass}
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
      <div className={`album-art${artLoaded ? ' has-art' : ''}`}>
        {album.has_art && (
          <img
            className="album-art-img"
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => setArtLoaded(false)}
          />
        )}
        {playing && isActive && <div className="now-playing-badge">▶</div>}
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
        {album.missing_album ? (
          <div className="album-title">
            <em>{album.album}</em>
          </div>
        ) : (
          <div className="album-title">{album.album}</div>
        )}
        <div className="album-artist">{album.album_artist}</div>
        <div className="album-year">{album.year}</div>
      </div>

      {menu && (
        <AlbumContextMenu x={menu.x} y={menu.y} album={album} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}
