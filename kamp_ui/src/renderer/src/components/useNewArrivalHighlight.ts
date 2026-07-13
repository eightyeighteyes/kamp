import { useState, useEffect } from 'react'
import { useStore } from '../store'
import type { Album } from '../api/client'

export interface StarParticle {
  id: number
  left: number
  top: number
  duration: number
  delay: number
}

export interface SparkParticle {
  id: number
  left: number
  top: number
  blinkDur: number
  blinkDelay: number
  sparkOpacity: number
}

function rnd(min: number, max: number): number {
  return min + Math.random() * (max - min)
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

export interface NewArrivalHighlight {
  isNew: boolean
  highlightStyle: string
  isMounting: boolean
  starParticles: StarParticle[]
  sparkParticles: SparkParticle[]
  hoverSparkParticles: SparkParticle[]
  isHovered: boolean
  setIsHovered: (v: boolean) => void
  auraActive: boolean
  // Defined only when isNew && highlightStyle === 'static'; set as --static-border-gradient
  staticBorderGradient: string | undefined
  dismissHighlight: (album: Album) => void
}

export function useNewArrivalHighlight(album: Album): NewArrivalHighlight {
  const highlightEnabled = useStore((s) => s.highlightEnabled)
  const highlightCutoffSecs = useStore((s) => s.highlightCutoffSecs)
  const highlightStyle = useStore((s) => s.highlightStyle)
  const dismissedHighlightKeys = useStore((s) => s.dismissedHighlightKeys)
  const dismissHighlight = useStore((s) => s.dismissHighlight)

  const albumHighlightKey = album.missing_album
    ? String(album.track_id ?? '')
    : `${album.album_artist}::${album.album}`
  const isNew =
    highlightEnabled &&
    album.added_at !== null &&
    album.added_at >= highlightCutoffSecs &&
    album.last_played_at === null &&
    !dismissedHighlightKeys.has(albumHighlightKey)

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

  const staticBorderGradient =
    isNew && highlightStyle === 'static' ? STATIC_BORDER_FRAMES[borderFrame] : undefined

  return {
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
  }
}
