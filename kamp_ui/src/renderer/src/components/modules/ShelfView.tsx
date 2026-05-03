import React, { useRef } from 'react'
import type { Album } from '../../api/client'
import { AlbumCard } from '../AlbumCard'

interface ShelfViewProps {
  albums: Album[]
}

type Anim = { from: number; to: number; startTime: number }

const SCROLL_PX = 500
const DURATION = 380

function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2
}

function step(
  el: HTMLDivElement,
  animRef: React.MutableRefObject<Anim | null>,
  rafRef: React.MutableRefObject<number>
): void {
  const anim = animRef.current
  if (!anim) return
  const t = Math.min((performance.now() - anim.startTime) / DURATION, 1)
  el.scrollLeft = anim.from + (anim.to - anim.from) * easeInOutCubic(t)
  if (t < 1) {
    rafRef.current = requestAnimationFrame(() => step(el, animRef, rafRef))
  } else {
    animRef.current = null
  }
}

function scroll(
  dir: 'left' | 'right',
  el: HTMLDivElement,
  animRef: React.MutableRefObject<Anim | null>,
  rafRef: React.MutableRefObject<number>
): void {
  const maxScroll = el.scrollWidth - el.clientWidth
  const prevTarget = animRef.current?.to ?? el.scrollLeft
  const to = Math.max(
    0,
    Math.min(prevTarget + (dir === 'right' ? SCROLL_PX : -SCROLL_PX), maxScroll)
  )
  cancelAnimationFrame(rafRef.current)
  animRef.current = { from: el.scrollLeft, to, startTime: performance.now() }
  rafRef.current = requestAnimationFrame(() => step(el, animRef, rafRef))
}

export function ShelfView({ albums }: ShelfViewProps): React.JSX.Element {
  const scrollRef = useRef<HTMLDivElement>(null)
  const animRef = useRef<Anim | null>(null)
  const rafRef = useRef<number>(0)

  const handleScroll = (dir: 'left' | 'right'): void => {
    if (scrollRef.current) scroll(dir, scrollRef.current, animRef, rafRef)
  }

  return (
    <div className="module-shelf-wrapper">
      <button
        className="module-shelf-arrow module-shelf-arrow--left"
        onClick={() => handleScroll('left')}
        aria-label="Scroll left"
        tabIndex={-1}
      >
        ‹
      </button>
      <div className="module-shelf" ref={scrollRef} role="region" aria-label="Album shelf">
        {albums.map((album) => (
          <AlbumCard
            key={album.missing_album ? album.file_path : `${album.album_artist}\0${album.album}`}
            album={album}
          />
        ))}
      </div>
      <button
        className="module-shelf-arrow module-shelf-arrow--right"
        onClick={() => handleScroll('right')}
        aria-label="Scroll right"
        tabIndex={-1}
      >
        ›
      </button>
    </div>
  )
}
