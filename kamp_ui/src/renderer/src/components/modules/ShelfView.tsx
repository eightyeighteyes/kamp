import React, { useEffect, useRef } from 'react'
import type { Album } from '../../api/client'
import { AlbumCard } from '../AlbumCard'
import { useStore } from '../../store'

interface ShelfViewProps {
  albums: Album[]
  scrollToPlaying?: boolean
}

const SCROLL_PX = 500
// Mirror the next/prev debuff timer on the server: wait this long after a
// track change before auto-scrolling, so rapid skipping doesn't thrash the
// shelf position.
const SCROLL_DEBOUNCE_MS = 5000

export function ShelfView({ albums, scrollToPlaying = false }: ShelfViewProps): React.JSX.Element {
  const scrollRef = useRef<HTMLDivElement>(null)
  const hasMounted = useRef(false)
  const currentTrack = useStore((s) => s.player.current_track)
  // undefined = not yet initialized (skip scroll on first load)
  const prevFirstAddedAt = useRef<number | null | undefined>(undefined)
  // Always-fresh albums snapshot read by the scroll timer callback without
  // making albums a dependency of the scroll effect.
  const albumsRef = useRef(albums)
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    albumsRef.current = albums
  }, [albums])

  // Cancel any pending scroll timer on unmount.
  useEffect(
    () => () => {
      if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current)
    },
    []
  )

  useEffect(() => {
    const firstAddedAt = albums[0]?.added_at ?? null
    if (prevFirstAddedAt.current !== undefined) {
      const prev = prevFirstAddedAt.current
      if (firstAddedAt !== null && (prev === null || firstAddedAt > prev)) {
        scrollRef.current?.scrollTo({ left: 0, behavior: 'smooth' })
      }
    }
    prevFirstAddedAt.current = firstAddedAt
  }, [albums])

  const scroll = (dir: 'left' | 'right'): void => {
    scrollRef.current?.scrollBy({
      left: dir === 'right' ? SCROLL_PX : -SCROLL_PX,
      behavior: 'smooth'
    })
  }

  useEffect(() => {
    const shelf = scrollRef.current
    if (!shelf || !scrollToPlaying) return

    // Cancel any scroll queued by the previous track.
    if (scrollTimerRef.current) {
      clearTimeout(scrollTimerRef.current)
      scrollTimerRef.current = null
    }

    // Helper: find current track in the freshest albums snapshot and scroll.
    const doScroll = (behavior: ScrollBehavior): void => {
      const s = scrollRef.current
      if (!s || !currentTrack) return
      const idx = albumsRef.current.findIndex((a) =>
        a.missing_album
          ? a.file_path === currentTrack.file_path
          : a.album === currentTrack.album && a.album_artist === currentTrack.album_artist
      )
      if (idx === -1) return
      // Matches the CSS layout: padding-left(12) + first-child margin(5) + idx*(card(180)+gap(15)) - scroll-padding(5)
      s.scrollTo({ left: 12 + idx * 195, behavior })
    }

    if (!hasMounted.current) {
      // First render: position instantly so the shelf opens in the right place.
      hasMounted.current = true
      doScroll('instant')
      return
    }

    if (!currentTrack) {
      shelf.scrollTo({ left: 0, behavior: 'smooth' })
      return
    }

    // Debounce subsequent track changes so rapid next/prev pressing doesn't
    // thrash the shelf. After SCROLL_DEBOUNCE_MS of stable playback, scroll
    // to wherever the album landed in the freshest list.
    scrollTimerRef.current = setTimeout(() => {
      scrollTimerRef.current = null
      doScroll('smooth')
    }, SCROLL_DEBOUNCE_MS)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTrack?.album_artist, currentTrack?.album, currentTrack?.file_path, scrollToPlaying])

  return (
    <div className="module-shelf-wrapper">
      <button
        className="module-shelf-arrow module-shelf-arrow--left"
        onClick={() => scroll('left')}
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
        onClick={() => scroll('right')}
        aria-label="Scroll right"
        tabIndex={-1}
      >
        ›
      </button>
    </div>
  )
}
