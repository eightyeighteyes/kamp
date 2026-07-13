import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useStore } from '../store'
import { applyPlaylistArtLocal, playlistArtUrl } from '../api/client'
import type { Album, PlaylistTrack } from '../api/client'
import { TrackContextMenu } from './TrackContextMenu'
import { SortControl } from './SortControl'
import { MagicPlaylistModal } from './MagicPlaylistModal'
import { AlbumCard } from './AlbumCard'
import { computeNewOrder } from '../utils/computeNewOrder'
import { truncateTitle } from '../utils/truncateTitle'
import {
  FavoriteIcon,
  GridViewIcon,
  PauseIcon,
  PlayIcon,
  PlayNextIcon,
  QueueAddIcon,
  QueueIcon,
  SparkleIcon,
  WarnIcon
} from './TransportIcons'
import { formatTime, formatLongDuration } from '../utils/formatTime'

const HERO_DEFAULT = 45
const HERO_MIN = 15
const HERO_KEY = 'kamp:playlist-hero-height-pct'

const TRACK_SORT_OPTIONS = [
  { key: 'position', label: 'Playlist Order' },
  { key: 'title', label: 'Title' },
  { key: 'artist', label: 'Artist' },
  { key: 'album', label: 'Album' },
  { key: 'duration', label: 'Duration' },
  { key: 'last_played', label: 'Last Played' },
  { key: 'most_played', label: 'Most Played' },
  { key: 'date_added', label: 'Date Added' },
  { key: 'release_date', label: 'Release Date' }
]

type TrackSortOrder =
  | 'position'
  | 'title'
  | 'artist'
  | 'album'
  | 'duration'
  | 'last_played'
  | 'most_played'
  | 'date_added'
  | 'release_date'

function sortKey(playlistId: number): string {
  return `kamp:playlist:${playlistId}:sort`
}

function displayModeKey(playlistId: number): string {
  return `kamp:playlist:${playlistId}:display-mode`
}

function loadTrackSort(
  playlistId: number,
  isMagic: boolean
): { order: TrackSortOrder; dir: 'asc' | 'desc' } {
  try {
    const raw = localStorage.getItem(sortKey(playlistId))
    if (raw) {
      const parsed = JSON.parse(raw) as { order: TrackSortOrder; dir: 'asc' | 'desc' }
      if (parsed.order && parsed.dir && !(isMagic && parsed.order === 'position')) return parsed
    }
  } catch {
    // ignore malformed storage
  }
  return { order: isMagic ? 'artist' : 'position', dir: 'asc' }
}

function applySortToTracks(
  tracks: PlaylistTrack[],
  order: TrackSortOrder,
  dir: 'asc' | 'desc'
): PlaylistTrack[] {
  if (order === 'position') return tracks
  const sorted = [...tracks].sort((a, b) => {
    let cmp = 0
    switch (order) {
      case 'title':
        cmp = a.title.localeCompare(b.title, undefined, { sensitivity: 'base' })
        break
      case 'artist':
        cmp = a.artist.localeCompare(b.artist, undefined, { sensitivity: 'base' })
        break
      case 'album':
        cmp = a.album.localeCompare(b.album, undefined, { sensitivity: 'base' })
        if (cmp === 0) cmp = a.disc_number - b.disc_number
        if (cmp === 0) cmp = a.track_number - b.track_number
        break
      case 'duration':
        cmp = a.duration - b.duration
        break
      case 'last_played': {
        // Unplayed tracks always sort last regardless of direction, so return
        // early to bypass the dir === 'desc' ? -cmp : cmp sign-flip at the end.
        const aT = a.last_played
        const bT = b.last_played
        if (aT === null && bT === null) return 0
        if (aT === null) return 1 // a always last
        if (bT === null) return -1 // b always last
        cmp = aT - bT
        break
      }
      case 'most_played':
        cmp = a.play_count - b.play_count
        break
      case 'date_added': {
        // Tracks without a date_added (pre-column) always sort last.
        const aD = a.date_added
        const bD = b.date_added
        if (aD === null && bD === null) return 0
        if (aD === null) return 1
        if (bD === null) return -1
        cmp = aD - bD
        break
      }
      case 'release_date': {
        // Tracks with no release_date always sort last regardless of direction.
        const aY = a.release_date || null
        const bY = b.release_date || null
        if (aY === null && bY === null) return 0
        if (aY === null) return 1
        if (bY === null) return -1
        cmp = parseInt(aY, 10) - parseInt(bY, 10)
        break
      }
    }
    return dir === 'desc' ? -cmp : cmp
  })
  return sorted
}

type TrackMenu = { x: number; y: number; track: PlaylistTrack }

function HeroImage({ src }: { src: string }): React.JSX.Element {
  const [loaded, setLoaded] = useState(false)
  return (
    <img
      className={`track-list-hero-img${loaded ? ' loaded' : ''}`}
      src={src}
      alt=""
      draggable={false}
      onLoad={() => setLoaded(true)}
    />
  )
}

export function PlaylistView(): React.JSX.Element | null {
  const playlist = useStore((s) => s.library.selectedPlaylist)
  const playlistTracks = useStore((s) => s.library.playlistTracks)
  const playlistTracksLoading = useStore((s) => s.library.playlistTracksLoading)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const reorderPlaylistTracks = useStore((s) => s.reorderPlaylistTracks)
  const removeTrackFromPlaylist = useStore((s) => s.removeTrackFromPlaylist)
  const setPlaylistFavorite = useStore((s) => s.setPlaylistFavorite)
  const setFavorite = useStore((s) => s.setFavorite)
  const renamePlaylist = useStore((s) => s.renamePlaylist)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const playPlaylist = useStore((s) => s.playPlaylist)
  const playFiles = useStore((s) => s.playFiles)
  const recordPlaylistPlayed = useStore((s) => s.recordPlaylistPlayed)
  const togglePlayPause = useStore((s) => s.togglePlayPause)
  const playNext = useStore((s) => s.playNext)
  const addToQueue = useStore((s) => s.addToQueue)
  const patchOpenPlaylist = useStore((s) => s.patchOpenPlaylist)
  const configValues = useStore((s) => s.configValues)
  const connected = configValues?.['bandcamp.connected'] ?? false
  const libraryAlbums = useStore((s) => s.library.albums)

  const [menu, setMenu] = useState<TrackMenu | null>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [magicModalOpen, setMagicModalOpen] = useState(false)
  const [magicModalKey, setMagicModalKey] = useState(0)
  const [titleDraft, setTitleDraft] = useState('')
  const [heroHeightPct, setHeroHeightPct] = useState<number>(() => {
    const saved = parseFloat(localStorage.getItem(HERO_KEY) ?? '')
    return isNaN(saved) ? HERO_DEFAULT : Math.min(HERO_DEFAULT, Math.max(HERO_MIN, saved))
  })
  const [isResizing, setIsResizing] = useState(false)
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set())
  const [anchorIdx, setAnchorIdx] = useState<number | null>(null)

  const titleInputRef = useRef<HTMLInputElement>(null)
  const artInputRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const dragFromIdx = useRef<number | null>(null)
  const prevTrackIdsRef = useRef<Set<number>>(new Set())
  const [flashIds, setFlashIds] = useState<Set<number>>(new Set())
  const [displayMode, setDisplayMode] = useState<'tracks' | 'albums'>(() => {
    const stored = localStorage.getItem(displayModeKey(playlist?.id ?? 0))
    return stored === 'albums' ? 'albums' : 'tracks'
  })

  // Must be declared before the early return — hook call order must be unconditional.
  // playlistTracks.length === displayTracks.length (sort never adds/removes items).
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: playlistTracks.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 38,
    overscan: 8
  })
  const didDragRef = useRef(false)
  const dragStartYRef = useRef(0)
  const heroAtDragStartRef = useRef(HERO_DEFAULT)
  const pendingSingleSelect = useRef<number | null>(null)

  // Per-playlist sort state — loaded from localStorage when playlist changes.
  const isMagic = !!playlist?.criteria
  const playlistId = playlist?.id ?? 0
  const storedSort = loadTrackSort(playlistId, isMagic)
  const [trackSortOrder, setTrackSortOrder] = useState<TrackSortOrder>(storedSort.order)
  const [trackSortDir, setTrackSortDir] = useState<'asc' | 'desc'>(storedSort.dir)

  const trackSortOptions = isMagic
    ? TRACK_SORT_OPTIONS.filter((o) => o.key !== 'position')
    : TRACK_SORT_OPTIONS

  // Reload sort state when navigating to a different playlist.
  useEffect(() => {
    if (!playlist) return
    const s = loadTrackSort(playlist.id, !!playlist.criteria)
    setTrackSortOrder(s.order)
    setTrackSortDir(s.dir)
  }, [playlist])

  // Clear selection when tracks change or sort changes (display indices shift).
  useEffect(() => {
    setSelectedIndices(new Set())
    setAnchorIdx(null)
  }, [playlistTracks.length, trackSortOrder])

  // Memoized before the early return (hooks must be unconditional).
  // Spreading + sorting 9000 tracks on every currentTrack/playing re-render
  // was causing ~500 ms UI lag on large magic playlists.
  const displayTracks = useMemo(
    () => applySortToTracks(playlistTracks, trackSortOrder, trackSortDir),
    [playlistTracks, trackSortOrder, trackSortDir]
  )
  const totalDuration = useMemo(
    () => playlistTracks.reduce((sum, t) => sum + (t.duration || 0), 0),
    [playlistTracks]
  )

  // Re-read display mode from localStorage when navigating between playlists.
  useEffect(() => {
    if (playlistId === 0) return
    const stored = localStorage.getItem(displayModeKey(playlistId))
    setDisplayMode(stored === 'albums' ? 'albums' : 'tracks')
  }, [playlistId])

  // Diff track IDs on WS refresh and flash newly-added rows (magic playlists only).
  useEffect(() => {
    const currentIds = new Set(playlistTracks.map((t) => t.id))
    if (isMagic && prevTrackIdsRef.current.size > 0) {
      const newIds = new Set([...currentIds].filter((id) => !prevTrackIdsRef.current.has(id)))
      if (newIds.size > 0) setFlashIds(newIds)
    }
    prevTrackIdsRef.current = currentIds
  }, [playlistTracks, isMagic])

  // Clear flash after 700ms.
  useEffect(() => {
    if (flashIds.size === 0) return
    const timer = setTimeout(() => setFlashIds(new Set()), 700)
    return () => clearTimeout(timer)
  }, [flashIds])

  // Synthesize Album objects from playlist tracks for the album-grid display mode.
  // Iterates displayTracks (already sorted) so tile order matches the active sort.
  const albumGroups = useMemo<Album[]>(() => {
    if (displayMode !== 'albums') return []
    // Build a quick lookup for album-level favorite status from the store's album
    // list (populated when the user has browsed the library). Falls back to false.
    const albumFavMap = new Map<string, boolean>(
      libraryAlbums.map((a) => [`${a.album_artist}::${a.album}`, a.favorite])
    )
    const seen = new Map<string, Album>()
    for (const t of displayTracks) {
      const albumArtist = t.album_artist || t.artist
      const key = `${albumArtist}::${t.album}`
      if (!seen.has(key)) {
        seen.set(key, {
          album_artist: albumArtist,
          album: t.album,
          release_date: t.release_date,
          track_count: 1,
          // Non-local tracks have server-side art even without embedded art.
          has_art: t.embedded_art || t.source !== 'local',
          missing_album: false,
          // file_path is the unique key only for missing_album=true albums.
          // Passing a track's file_path here causes /api/v1/tracks to receive
          // a bandcamp:// URI as a query param, which the server rejects (400).
          file_path: '',
          art_version: null,
          added_at: null,
          last_played_at: null,
          play_count_avg: 0,
          favorite: albumFavMap.get(key) ?? false,
          has_favorite_track: t.favorite,
          source: t.source === 'bandcamp' ? 'bandcamp' : 'local',
          has_remote_tracks: t.source !== 'local'
        })
      } else {
        const alb = seen.get(key)!
        alb.track_count++
        if (t.favorite) alb.has_favorite_track = true
        if (t.embedded_art || t.source !== 'local') alb.has_art = true
        if (t.source !== 'local') {
          alb.has_remote_tracks = true
          alb.source = alb.source === 'local' ? (t.source as 'bandcamp') : 'mixed'
        }
      }
    }
    return [...seen.values()]
  }, [displayTracks, displayMode, libraryAlbums])

  const albumTrackIds = useMemo<Map<string, number[]>>(() => {
    if (displayMode !== 'albums') return new Map()
    const map = new Map<string, number[]>()
    for (const t of displayTracks) {
      const key = `${t.album_artist || t.artist}::${t.album}`
      const ids = map.get(key)
      if (ids) {
        ids.push(t.id)
      } else {
        map.set(key, [t.id])
      }
    }
    return map
  }, [displayTracks, displayMode])

  if (!playlist) return null

  const persistTrackSort = (order: TrackSortOrder, dir: 'asc' | 'desc'): void => {
    try {
      localStorage.setItem(sortKey(playlist.id), JSON.stringify({ order, dir }))
    } catch {
      // ignore
    }
  }

  const setDisplayModeAndPersist = (mode: 'tracks' | 'albums'): void => {
    setDisplayMode(mode)
    try {
      localStorage.setItem(displayModeKey(playlist.id), mode)
    } catch {
      // ignore
    }
  }

  const handleArtFileChosen = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    applyPlaylistArtLocal(playlist.id, file)
      .then((updated) => patchOpenPlaylist(updated))
      .catch((err: unknown) => console.error('Failed to set playlist art:', err))
  }

  const handleTrackSortChange = (key: string): void => {
    const order = key as TrackSortOrder
    // Default 'date_added' to descending (newest first) when first selected.
    const dir: 'asc' | 'desc' = order === 'date_added' ? 'desc' : trackSortDir
    setTrackSortOrder(order)
    setTrackSortDir(dir)
    persistTrackSort(order, dir)
  }

  const handleTrackDirChange = (dir: 'asc' | 'desc'): void => {
    setTrackSortDir(dir)
    persistTrackSort(trackSortOrder, dir)
  }

  // Apply sort for display only. When not in playlist-order mode,
  // drag-to-reorder is disabled (it's nonsensical on a sorted view).
  const isDragEnabled = trackSortOrder === 'position'

  const handleResizeMouseDown = (e: React.MouseEvent): void => {
    e.preventDefault()
    didDragRef.current = false
    dragStartYRef.current = e.clientY
    heroAtDragStartRef.current = heroHeightPct
    setIsResizing(true)

    const onMove = (ev: MouseEvent): void => {
      const deltaVh = ((ev.clientY - dragStartYRef.current) / window.innerHeight) * 100
      if (Math.abs(ev.clientY - dragStartYRef.current) > 4) didDragRef.current = true
      if (!didDragRef.current) return
      setHeroHeightPct(
        Math.min(HERO_DEFAULT, Math.max(HERO_MIN, heroAtDragStartRef.current + deltaVh))
      )
    }

    const onUp = (): void => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      setIsResizing(false)
      if (didDragRef.current) {
        setHeroHeightPct((h) => {
          localStorage.setItem(HERO_KEY, String(Math.round(h)))
          return h
        })
      }
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  const handleResizeReset = (): void => {
    setHeroHeightPct(HERO_DEFAULT)
    localStorage.setItem(HERO_KEY, String(HERO_DEFAULT))
  }

  const handleTitleDoubleClick = (): void => {
    setTitleDraft(playlist.title)
    setEditingTitle(true)
    setTimeout(() => titleInputRef.current?.select(), 0)
  }

  const commitTitle = (): void => {
    const trimmed = titleDraft.trim()
    if (trimmed && trimmed !== playlist.title) {
      void renamePlaylist(playlist.id, trimmed)
    }
    setEditingTitle(false)
  }

  // Insert all playlist tracks at "play next" position. playNext always inserts at
  // currentPosition+1, so iterating in reverse lands them in the correct order.
  const handlePlayNext = (): void => {
    if (playlistTracks.length === 0) return
    void (async () => {
      for (let i = playlistTracks.length - 1; i >= 0; i--) {
        await playNext({ id: playlistTracks[i].id })
      }
    })()
  }

  const isCurrentPlaylist =
    currentTrack !== null && playlistTracks.some((t) => t.id === currentTrack.id)

  // If a playlist track is already in the queue: couple to transport (pause/resume).
  // Otherwise: replace the queue with this playlist's tracks and start playing.
  const handlePlay = (): void => {
    if (playlistTracks.length === 0) return
    if (isCurrentPlaylist) {
      void togglePlayPause()
    } else {
      void playPlaylist(playlist.id)
    }
  }

  const handleAddToQueue = (): void => {
    void (async () => {
      for (const t of playlistTracks) {
        await addToQueue({ id: t.id })
      }
    })()
  }

  const handleRowMouseDown = (e: React.MouseEvent, idx: number): void => {
    if (e.button !== 0) return
    if (e.shiftKey && anchorIdx !== null) {
      const lo = Math.min(anchorIdx, idx)
      const hi = Math.max(anchorIdx, idx)
      setSelectedIndices(new Set(Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)))
    } else if (e.metaKey || e.ctrlKey) {
      setSelectedIndices((prev) => {
        const next = new Set(prev)
        next.has(idx) ? next.delete(idx) : next.add(idx)
        return next
      })
      setAnchorIdx(idx)
    } else if (selectedIndices.has(idx) && selectedIndices.size > 1) {
      // Defer collapse to mouseup so a drag can start with the full selection.
      pendingSingleSelect.current = idx
    } else {
      setSelectedIndices(new Set([idx]))
      setAnchorIdx(idx)
    }
  }

  const handleRowMouseUp = (idx: number): void => {
    if (pendingSingleSelect.current === idx) {
      pendingSingleSelect.current = null
      setSelectedIndices(new Set([idx]))
      setAnchorIdx(idx)
    }
  }

  // Drag handlers — always fire so queue drops work even when sorted.
  // Playlist-internal reorder data (kamp-playlist-*) is only set when
  // isDragEnabled; the queue-compatible types are always set.
  const handleDragStart = (e: React.DragEvent, idx: number): void => {
    pendingSingleSelect.current = null
    dragFromIdx.current = idx
    const isMulti = selectedIndices.has(idx) && selectedIndices.size > 1
    if (isMulti) {
      const sorted = [...selectedIndices].sort((a, b) => a - b)
      const ids = sorted.map((i) => displayTracks[i].id)
      e.dataTransfer.setData('text/kamp-track-ids', JSON.stringify(ids))
      if (isDragEnabled) {
        e.dataTransfer.setData('text/kamp-playlist-track-idx', String(idx))
        e.dataTransfer.setData('text/kamp-playlist-multi', JSON.stringify(sorted))
      }
      const ghost = document.createElement('div')
      ghost.textContent = `${sorted.length} tracks`
      ghost.style.cssText =
        'position:fixed;top:-100px;background:var(--accent);color:#fff;padding:4px 10px;border-radius:3px;font-size:12px;font-weight:600'
      document.body.appendChild(ghost)
      e.dataTransfer.setDragImage(ghost, 0, 0)
      requestAnimationFrame(() => document.body.removeChild(ghost))
    } else {
      setSelectedIndices(new Set())
      setAnchorIdx(null)
      e.dataTransfer.setData('text/kamp-track-id', String(displayTracks[idx].id))
      if (isDragEnabled) {
        e.dataTransfer.setData('text/kamp-playlist-track-idx', String(idx))
      }
    }
    e.dataTransfer.effectAllowed = 'move'
  }

  const handleDragEnd = (): void => {
    setSelectedIndices(new Set())
    setAnchorIdx(null)
  }

  const isPlaylistDrop = (types: DOMStringList | readonly string[]): boolean =>
    Array.from(types).some(
      (t) => t === 'text/kamp-playlist-track-idx' || t === 'text/kamp-playlist-multi'
    )

  const handleDragOver = (e: React.DragEvent): void => {
    if (!isPlaylistDrop(e.dataTransfer.types)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    e.currentTarget.classList.add('drag-over')
  }

  const handleDragLeave = (e: React.DragEvent): void => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      e.currentTarget.classList.remove('drag-over')
    }
  }

  const handleDrop = (e: React.DragEvent, dropIdx: number): void => {
    e.preventDefault()
    e.currentTarget.classList.remove('drag-over')
    const multiJson = e.dataTransfer.getData('text/kamp-playlist-multi')
    const fromStr = e.dataTransfer.getData('text/kamp-playlist-track-idx')
    if (multiJson) {
      const sorted: number[] = JSON.parse(multiJson)
      const newOrder = computeNewOrder(playlistTracks.length, sorted, dropIdx)
      void reorderPlaylistTracks(
        playlist.id,
        newOrder.map((i) => playlistTracks[i].playlist_track_id)
      )
    } else if (fromStr) {
      const from = Number(fromStr)
      if (from === dropIdx) return
      const newOrder = computeNewOrder(playlistTracks.length, [from], dropIdx)
      void reorderPlaylistTracks(
        playlist.id,
        newOrder.map((i) => playlistTracks[i].playlist_track_id)
      )
    }
  }

  return (
    <div
      className={`track-list-view${isResizing ? ' track-list-view--resizing' : ''}`}
      style={{ '--hero-height-pct': heroHeightPct } as React.CSSProperties}
    >
      <div className="track-list-hero has-art">
        <HeroImage src={playlistArtUrl(playlist.id, playlist.updated_at)} />
        <input
          type="file"
          accept="image/*"
          ref={artInputRef}
          className="art-upload-input"
          onChange={handleArtFileChosen}
        />
        <button
          className="hero-art-btn"
          title="Change art"
          onClick={() => artInputRef.current?.click()}
        >
          Change art
        </button>
      </div>
      <div className="track-list-hero-overlay" />

      <nav className="breadcrumb" aria-label="Navigation">
        <button onClick={() => void selectPlaylist(null)}>Playlists</button>
        <span className="breadcrumb-sep" aria-hidden="true">
          ›
        </span>
        <span title={playlist.title}>{truncateTitle(playlist.title)}</span>
      </nav>

      <div className="track-list-identity">
        <div className="track-list-identity-text">
          <button
            className={`track-list-album-fav-btn favorite-btn${playlist.favorite ? ' active' : ''}`}
            aria-label={playlist.favorite ? 'Remove from favorites' : 'Add to favorites'}
            aria-pressed={playlist.favorite}
            onClick={() => void setPlaylistFavorite(playlist.id, !playlist.favorite)}
          >
            <FavoriteIcon active={playlist.favorite} size={36} />
          </button>
          {editingTitle ? (
            <input
              ref={titleInputRef}
              className="track-list-album-title"
              value={titleDraft}
              autoFocus
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={commitTitle}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitTitle()
                if (e.key === 'Escape') setEditingTitle(false)
              }}
              style={{
                background: 'transparent',
                border: 'none',
                outline: '1px solid var(--accent)',
                width: '100%'
              }}
            />
          ) : (
            <h1 className="track-list-album-title" onDoubleClick={handleTitleDoubleClick}>
              {playlist.title}
            </h1>
          )}
          <div className="track-list-album-year">
            <span
              key={isMagic ? playlistTracks.length : 0}
              className={isMagic ? 'track-count-tick' : undefined}
            >
              {playlistTracks.length === 1 ? '1 track' : `${playlistTracks.length} tracks`}
            </span>
            {totalDuration > 0 && ` · ${formatLongDuration(totalDuration)}`}
          </div>
        </div>
        <div className="album-controls-group">
          <div className="album-controls">
            <button
              className="album-secondary-btn"
              aria-label="Add all to queue"
              onClick={handleAddToQueue}
            >
              <QueueAddIcon size={16} />
            </button>
            <button
              className="album-secondary-btn"
              aria-label="Play all next"
              onClick={handlePlayNext}
            >
              <PlayNextIcon size={16} />
            </button>
            <button
              className="play-all-btn"
              aria-label={isCurrentPlaylist && playing ? 'Pause' : 'Play'}
              onClick={handlePlay}
            >
              {isCurrentPlaylist && playing ? <PauseIcon size={18} /> : <PlayIcon size={18} />}
            </button>
          </div>
        </div>
      </div>

      {/* Drag-to-resize handle; toolbar lives inside so they share the same
          horizontal band. stopPropagation on the toolbar prevents mousedown
          from bubbling up and starting a drag when the user clicks a control. */}
      <div
        className="album-meta-toggle"
        aria-label="Resize hero"
        onMouseDown={handleResizeMouseDown}
        onDoubleClick={handleResizeReset}
      >
        <div
          className="album-grid-toolbar album-grid-toolbar--embedded"
          onMouseDown={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
        >
          <div className="view-mode-toggle">
            <button
              className={`view-mode-btn${displayMode === 'tracks' ? ' view-mode-btn--active' : ''}`}
              title="Track list"
              onClick={() => setDisplayModeAndPersist('tracks')}
            >
              <QueueIcon size={14} />
            </button>
            <button
              className={`view-mode-btn${displayMode === 'albums' ? ' view-mode-btn--active' : ''}`}
              title="Album grid"
              onClick={() => setDisplayModeAndPersist('albums')}
            >
              <GridViewIcon size={14} />
            </button>
          </div>
          <SortControl
            value={trackSortOrder}
            options={trackSortOptions}
            dir={trackSortDir}
            onChange={handleTrackSortChange}
            onDirChange={handleTrackDirChange}
            showDir={trackSortOrder !== 'position'}
          />
          {playlist.criteria !== null && (
            <button
              className="playlist-cta-btn playlist-cta-btn--magic"
              onClick={() => {
                setMagicModalKey((k) => k + 1)
                setMagicModalOpen(true)
              }}
            >
              <SparkleIcon size={12} />
              Edit criteria
            </button>
          )}
        </div>
      </div>

      {displayMode === 'albums' ? (
        <div className="track-list-body">
          <div className="album-grid" style={{ padding: 10 }}>
            {albumGroups.map((a) => (
              <AlbumCard
                key={`${a.album_artist}::${a.album}`}
                album={a}
                dragTrackIds={albumTrackIds.get(`${a.album_artist}::${a.album}`)}
              />
            ))}
          </div>
          {albumGroups.length === 0 && (
            <div className="album-grid-empty">
              {playlistTracksLoading
                ? 'Loading…'
                : isMagic
                  ? 'No tracks match these criteria yet.'
                  : 'No tracks yet. Right-click any track or album and choose Add to Playlist.'}
            </div>
          )}
        </div>
      ) : (
        <div className="track-list-body" ref={scrollRef}>
          {/* Tall positioning context; only visible rows are in the DOM. */}
          <ol
            className="track-rows"
            style={{
              position: 'relative',
              height: virtualizer.getTotalSize() + 80,
              padding: 0
            }}
          >
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const i = virtualRow.index
              const track = displayTracks[i]
              const isCurrent = currentTrack?.id === track.id
              const isRemote = track.source !== 'local'
              const isOffline = isRemote && !connected
              const isSelected = selectedIndices.has(i)
              return (
                <li
                  key={track.playlist_track_id ?? track.id}
                  className={[
                    'track-row',
                    isCurrent ? 'current' : '',
                    isOffline ? 'track-row--offline' : '',
                    isSelected ? 'selected' : '',
                    flashIds.has(track.id) ? 'track-row--new-flash' : ''
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: 20,
                    right: 20,
                    transform: `translateY(${virtualRow.start}px)`
                  }}
                  tabIndex={0}
                  draggable
                  onMouseDown={(e) => handleRowMouseDown(e, i)}
                  onMouseUp={() => handleRowMouseUp(i)}
                  onDragStart={(e) => handleDragStart(e, i)}
                  onDragEnd={handleDragEnd}
                  onDragOver={isDragEnabled ? handleDragOver : undefined}
                  onDragLeave={isDragEnabled ? handleDragLeave : undefined}
                  onDrop={isDragEnabled ? (e) => handleDrop(e, i) : undefined}
                  onDoubleClick={() => {
                    if (isOffline) return
                    if (isCurrent) {
                      void togglePlayPause()
                    } else if (isDragEnabled) {
                      // Playlist order — i maps directly to stored position
                      void playPlaylist(playlist.id, i)
                    } else {
                      // Sorted view — queue in display order so the right track plays
                      void playFiles(
                        displayTracks.map((t) => t.id),
                        i
                      )
                      void recordPlaylistPlayed(playlist.id)
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter') return
                    if (isOffline) return
                    if (isCurrent) {
                      void togglePlayPause()
                    } else if (isDragEnabled) {
                      void playPlaylist(playlist.id, i)
                    } else {
                      void playFiles(
                        displayTracks.map((t) => t.id),
                        i
                      )
                      void recordPlaylistPlayed(playlist.id)
                    }
                  }}
                  onContextMenu={(e) => {
                    e.preventDefault()
                    // Right-click on an unselected row: select only that row.
                    const nextIndices = isSelected ? selectedIndices : new Set([i])
                    if (!isSelected) {
                      setSelectedIndices(nextIndices)
                      setAnchorIdx(i)
                    }
                    setMenu({ x: e.clientX, y: e.clientY, track })
                  }}
                >
                  <span className="track-row-fav">
                    <button
                      className={`track-row-fav-btn${track.favorite ? ' active' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation()
                        void setFavorite(track, !track.favorite)
                      }}
                      aria-label={track.favorite ? 'Remove from favorites' : 'Add to favorites'}
                      aria-pressed={track.favorite}
                    >
                      <FavoriteIcon active={track.favorite} size={10} />
                    </button>
                  </span>
                  <span className="track-row-num">{i + 1}</span>
                  <span className="track-row-title-cell">
                    {isOffline && (
                      <span
                        className="track-row-offline-icon"
                        title="Track unavailable offline"
                        aria-hidden="true"
                      >
                        <WarnIcon size={11} />
                      </span>
                    )}
                    <span
                      className={
                        isOffline ? 'track-row-title track-row-title--offline' : 'track-row-title'
                      }
                    >
                      {track.title}
                    </span>
                  </span>
                  <span className="track-row-artist">{track.artist}</span>
                  <span className="track-row-duration">
                    {track.duration > 0 ? formatTime(track.duration) : '—'}
                  </span>
                </li>
              )
            })}
          </ol>
          {playlistTracks.length === 0 && (
            <div className="album-grid-empty">
              {playlistTracksLoading
                ? 'Loading…'
                : playlist.criteria !== null
                  ? 'No tracks match these criteria yet.'
                  : 'No tracks yet. Right-click any track or album and choose Add to Playlist.'}
            </div>
          )}
        </div>
      )}

      <MagicPlaylistModal
        key={magicModalKey}
        open={magicModalOpen}
        onClose={() => setMagicModalOpen(false)}
        playlist={playlist.criteria !== null ? playlist : undefined}
      />

      {menu && (
        <TrackContextMenu
          x={menu.x}
          y={menu.y}
          track={menu.track}
          selectedTracks={
            selectedIndices.size > 1
              ? [...selectedIndices].sort((a, b) => a - b).map((i) => displayTracks[i])
              : undefined
          }
          onClose={() => setMenu(null)}
          onRemoveFromPlaylist={
            playlist.criteria === null
              ? () => {
                  const targets =
                    selectedIndices.size > 0
                      ? [...selectedIndices]
                          .sort((a, b) => b - a)
                          .map((i) => displayTracks[i].playlist_track_id!)
                      : [menu.track.playlist_track_id!]
                  targets.forEach((ptId) => void removeTrackFromPlaylist(playlist.id, ptId))
                }
              : undefined
          }
        />
      )}
    </div>
  )
}
