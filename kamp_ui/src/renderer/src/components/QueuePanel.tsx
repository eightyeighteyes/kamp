import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { QueueContextMenu } from './QueueContextMenu'
import { QueueAlbumCard } from './QueueAlbumCard'
import { FavoriteIcon, WarnIcon, GoToAlbumIcon } from './TransportIcons'
import type { Track } from '../api/client'
import { computeNewOrder } from '../utils/computeNewOrder'

type NextUpItem =
  | { kind: 'track'; track: Track; queueIdx: number }
  | { kind: 'album'; albumArtist: string; album: string; tracks: Track[]; trackIndices: number[] }

const QUEUE_WIDTH_KEY = 'kamp:queue-width'
const QUEUE_WIDTH_DEFAULT = 280

const QUEUE_DROP_TYPES = new Set([
  'text/kamp-track-path',
  'text/kamp-file-paths',
  'text/kamp-album',
  'text/kamp-artist',
  'text/kamp-queue-idx',
  'text/kamp-playlist'
])
function isQueueDrop(types: DOMStringList | readonly string[]): boolean {
  return Array.from(types).some((t) => QUEUE_DROP_TYPES.has(t))
}

type ContextMenu = {
  x: number
  y: number
  trackIdx: number | null
  track?: Track
  selectedTracks: Track[]
  unplayedSelectedIndices: number[]
}

export function QueuePanel(): React.JSX.Element {
  const queue = useStore((s) => s.queue)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)
  const moveQueueTrack = useStore((s) => s.moveQueueTrack)
  const reorderQueue = useStore((s) => s.reorderQueue)
  const skipToQueueTrack = useStore((s) => s.skipToQueueTrack)
  const addToQueue = useStore((s) => s.addToQueue)
  const insertIntoQueue = useStore((s) => s.insertIntoQueue)
  const insertAlbumAt = useStore((s) => s.insertAlbumAt)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const insertArtistAt = useStore((s) => s.insertArtistAt)
  const loadPlaylistTracks = useStore((s) => s.loadPlaylistTracks)
  const configValues = useStore((s) => s.configValues)
  const bandcampConnected = configValues?.['bandcamp.connected'] ?? false
  // listRef is on the Next Up <ol> — used for queue-tail-drop visual
  const listRef = useRef<HTMLOListElement>(null)
  const historyListRef = useRef<HTMLOListElement>(null)
  const [menu, setMenu] = useState<ContextMenu | null>(null)
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set())
  const [anchorIdx, setAnchorIdx] = useState<number | null>(null)
  // When clicking an already-selected row, defer collapsing the selection to mouseup
  // so that a drag can start before the collapse fires. dragstart cancels it.
  const pendingSingleSelect = useRef<number | null>(null)
  const tooltip = useTooltip()
  const [historyCollapsed, setHistoryCollapsed] = useState(
    () => localStorage.getItem('kamp:queue-history-collapsed') === 'true'
  )
  const [queueWidth, setQueueWidth] = useState<number>(() => {
    const saved = parseFloat(localStorage.getItem(QUEUE_WIDTH_KEY) ?? '')
    const max = window.innerWidth * 0.33
    return isNaN(saved) ? QUEUE_WIDTH_DEFAULT : Math.min(max, Math.max(QUEUE_WIDTH_DEFAULT, saved))
  })
  const [isResizing, setIsResizing] = useState(false)
  const dragStartXRef = useRef(0)
  const widthAtDragStartRef = useRef(QUEUE_WIDTH_DEFAULT)
  const didDragRef = useRef(false)
  const [albumGroupingActive, setAlbumGroupingActive] = useState(
    () => localStorage.getItem('kamp:album-view') === 'true'
  )
  const nowPlayingListRef = useRef<HTMLOListElement>(null)
  // Tracks the currently highlighted drop-indicator element to avoid a DOM query on clear.
  const activeDropIndicatorRef = useRef<HTMLElement | null>(null)

  // Stabilise via useMemo so the ?? [] fallback never produces a new array reference
  // on renders where queue is null, which would otherwise invalidate nextUpItems every render.
  const tracks = useMemo(() => queue?.tracks ?? [], [queue])
  const position = queue?.position ?? -1

  useEffect(() => {
    historyListRef.current?.scrollTo({
      top: historyListRef.current.scrollHeight,
      behavior: 'instant'
    })
  }, [position])

  // When history is expanded after being collapsed, snap to the bottom instantly
  // so the user sees the most recent entry rather than the oldest.
  useEffect(() => {
    if (!historyCollapsed) {
      historyListRef.current?.scrollTo({
        top: historyListRef.current.scrollHeight,
        behavior: 'instant'
      })
    }
  }, [historyCollapsed])

  // Clear selection when the queue changes length or position advances —
  // indices would be stale and could refer to different tracks.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedIndices(new Set())

    setAnchorIdx(null)
  }, [tracks.length, position])

  // Persist album-view state so it survives app relaunch. A single effect keyed on
  // the state covers every toggle path — the header button, the Alt shortcut, and Escape.
  useEffect(() => {
    localStorage.setItem('kamp:album-view', String(albumGroupingActive))
  }, [albumGroupingActive])

  // Alt → enter album-grouping mode; Escape → exit it.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Alt') setAlbumGroupingActive((prev) => !prev)
      if (e.key === 'Escape' && albumGroupingActive) setAlbumGroupingActive(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [albumGroupingActive])

  // Clamp width to 33% when the window shrinks.
  useEffect(() => {
    const onWindowResize = (): void => {
      const max = window.innerWidth * 0.33
      setQueueWidth((w) => {
        if (w > max) {
          localStorage.setItem(QUEUE_WIDTH_KEY, String(Math.round(max)))
          return max
        }
        return w
      })
    }
    window.addEventListener('resize', onWindowResize)
    return () => window.removeEventListener('resize', onWindowResize)
  }, [])

  // View model: collapses Next Up tracks into album cards when grouping mode is active.
  // Card appears at each album's first occurrence in the queue; straddle album (currently
  // playing) and single-track albums remain as individual rows.
  const nextUpItems = useMemo((): NextUpItem[] => {
    if (!albumGroupingActive || position < 0) return []
    const nowPlayingTrack = tracks[position]
    const straddleKey = nowPlayingTrack
      ? `${nowPlayingTrack.album_artist}\0${nowPlayingTrack.album}`
      : ''

    // First pass: build per-album groups (preserving relative track order within each album)
    const albumGroupMap = new Map<
      string,
      { albumArtist: string; album: string; tracks: Track[]; trackIndices: number[] }
    >()
    for (let i = position + 1; i < tracks.length; i++) {
      const track = tracks[i]
      const key = `${track.album_artist}\0${track.album}`
      if (key === straddleKey) continue
      if (!albumGroupMap.has(key)) {
        albumGroupMap.set(key, {
          albumArtist: track.album_artist,
          album: track.album,
          tracks: [],
          trackIndices: []
        })
      }
      const group = albumGroupMap.get(key)!
      group.tracks.push(track)
      group.trackIndices.push(i)
    }

    // Second pass: emit items in queue order, placing each album card at its first occurrence
    const result: NextUpItem[] = []
    const emittedAlbumKeys = new Set<string>()
    for (let i = position + 1; i < tracks.length; i++) {
      const track = tracks[i]
      const key = `${track.album_artist}\0${track.album}`
      if (key === straddleKey) {
        result.push({ kind: 'track', track, queueIdx: i })
        continue
      }
      if (emittedAlbumKeys.has(key)) continue
      emittedAlbumKeys.add(key)
      const group = albumGroupMap.get(key)!
      if (group.tracks.length < 2) {
        result.push({ kind: 'track', track: group.tracks[0], queueIdx: group.trackIndices[0] })
      } else {
        result.push({ kind: 'album', ...group })
      }
    }
    return result
  }, [albumGroupingActive, tracks, position])

  function clearAlbumDropIndicators(): void {
    const el = activeDropIndicatorRef.current
    if (el) {
      el.classList.remove('queue-drop-above', 'queue-drop-below', 'queue-now-playing-drop')
      activeDropIndicatorRef.current = null
    }
  }

  function updateAlbumDropIndicators(x: number, y: number): void {
    clearAlbumDropIndicators()
    const el = document.elementFromPoint(x, y)
    const li = el?.closest('li[data-drop-idx]') as HTMLElement | null
    if (!li) return
    const dropIdx = parseInt(li.dataset.dropIdx ?? '')
    if (isNaN(dropIdx)) return
    if (dropIdx === position) {
      li.classList.add('queue-now-playing-drop')
      activeDropIndicatorRef.current = li
      return
    }
    if (dropIdx < position) return
    const rect = li.getBoundingClientRect()
    const cls = y < rect.top + rect.height / 2 ? 'queue-drop-above' : 'queue-drop-below'
    li.classList.add(cls)
    activeDropIndicatorRef.current = li
  }

  function resolveAlbumDropIdx(x: number, y: number): number | null {
    const el = document.elementFromPoint(x, y)
    const li = el?.closest('li[data-drop-idx]') as HTMLElement | null
    if (!li) {
      // Tail drop: pointer in the Next Up list area but not on any row
      const nextUpEl = listRef.current
      if (nextUpEl) {
        const r = nextUpEl.getBoundingClientRect()
        if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return tracks.length
      }
      return null
    }
    const dropIdx = parseInt(li.dataset.dropIdx ?? '')
    if (isNaN(dropIdx)) return null
    // Now Playing row: insert immediately after current track
    if (dropIdx === position) return position + 1
    // History rows: not a valid target in grouping mode
    if (dropIdx < position) return null
    const rect = li.getBoundingClientRect()
    const isTopHalf = y < rect.top + rect.height / 2
    if (isTopHalf) return dropIdx
    const next = li.nextElementSibling as HTMLElement | null
    const nextDropIdx = next?.dataset.dropIdx ? parseInt(next.dataset.dropIdx) : undefined
    return nextDropIdx ?? tracks.length
  }

  function handleAlbumCardPointerDown(
    trackIndices: number[],
    startX: number,
    startY: number
  ): void {
    let dragStarted = false
    let ghost: HTMLDivElement | null = null

    const onMove = (ev: PointerEvent): void => {
      if (!dragStarted) {
        if (Math.abs(ev.clientX - startX) < 4 && Math.abs(ev.clientY - startY) < 4) return
        dragStarted = true
        ghost = document.createElement('div')
        ghost.textContent = `${trackIndices.length} tracks`
        ghost.style.cssText =
          'position:fixed;top:-100px;left:-100px;background:var(--accent);color:#fff;padding:4px 10px;border-radius:3px;font-size:12px;font-weight:600;pointer-events:none;z-index:9999'
        document.body.appendChild(ghost)
      }
      if (ghost) {
        ghost.style.left = `${ev.clientX + 12}px`
        ghost.style.top = `${ev.clientY - 12}px`
      }
      updateAlbumDropIndicators(ev.clientX, ev.clientY)
    }

    const cleanup = (): void => {
      document.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerup', onUp)
      document.removeEventListener('keydown', onEscape)
      if (ghost) {
        document.body.removeChild(ghost)
        ghost = null
      }
      clearAlbumDropIndicators()
    }

    const onUp = (ev: PointerEvent): void => {
      const wasDrag = dragStarted
      cleanup()
      if (wasDrag) {
        const dropIdx = resolveAlbumDropIdx(ev.clientX, ev.clientY)
        if (dropIdx !== null) {
          void reorderQueue(computeNewOrder(tracks.length, trackIndices, dropIdx))
        }
      }
    }

    const onEscape = (ev: KeyboardEvent): void => {
      if (ev.key === 'Escape') cleanup()
    }

    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
    document.addEventListener('keydown', onEscape)
  }

  function handleResizeMouseDown(e: React.MouseEvent): void {
    e.preventDefault()
    didDragRef.current = false
    dragStartXRef.current = e.clientX
    widthAtDragStartRef.current = queueWidth
    setIsResizing(true)

    const onMove = (ev: MouseEvent): void => {
      // Dragging left (smaller clientX) widens the panel.
      const delta = dragStartXRef.current - ev.clientX
      if (Math.abs(delta) > 4) didDragRef.current = true
      if (!didDragRef.current) return
      const max = window.innerWidth * 0.33
      setQueueWidth(
        Math.min(max, Math.max(QUEUE_WIDTH_DEFAULT, widthAtDragStartRef.current + delta))
      )
    }

    const onUp = (): void => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      setIsResizing(false)
      if (didDragRef.current) {
        setQueueWidth((w) => {
          localStorage.setItem(QUEUE_WIDTH_KEY, String(Math.round(w)))
          return w
        })
      }
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  function handleResizeDoubleClick(): void {
    setQueueWidth(QUEUE_WIDTH_DEFAULT)
    localStorage.setItem(QUEUE_WIDTH_KEY, String(QUEUE_WIDTH_DEFAULT))
  }

  function toggleHistoryCollapsed(): void {
    const next = !historyCollapsed
    setHistoryCollapsed(next)
    localStorage.setItem('kamp:queue-history-collapsed', String(next))
  }

  // Persistence is handled by the localStorage effect keyed on albumGroupingActive.
  function toggleAlbumGrouping(): void {
    setAlbumGroupingActive((prev) => !prev)
  }

  function handleRowMouseDown(e: React.MouseEvent, idx: number): void {
    if (e.button !== 0) return
    // While grouping mode is active, individual track rows are non-interactive.
    if (albumGroupingActive) return
    if (e.shiftKey && anchorIdx !== null) {
      const lo = Math.min(anchorIdx, idx)
      const hi = Math.max(anchorIdx, idx)
      setSelectedIndices(new Set(Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)))
      // anchorIdx does NOT change on shift-click — it stays at the range origin
    } else if (e.metaKey || e.ctrlKey) {
      setSelectedIndices((prev) => {
        const next = new Set(prev)
        next.has(idx) ? next.delete(idx) : next.add(idx)
        return next
      })
      setAnchorIdx(idx)
    } else if (selectedIndices.has(idx) && selectedIndices.size > 1) {
      // Plain click on an already-selected row: defer the collapse to mouseup so
      // a drag can start with the full selection intact. dragstart will cancel it.
      pendingSingleSelect.current = idx
    } else {
      setSelectedIndices(new Set([idx]))
      setAnchorIdx(idx)
    }
  }

  function handleRowMouseUp(idx: number): void {
    if (pendingSingleSelect.current === idx) {
      pendingSingleSelect.current = null
      setSelectedIndices(new Set([idx]))
      setAnchorIdx(idx)
    }
  }

  function handleDrop(e: React.DragEvent, dropIdx: number): void {
    e.stopPropagation()
    e.currentTarget.classList.remove('drag-over')
    listRef.current?.classList.remove('queue-tail-drop')
    const multiJson = e.dataTransfer.getData('text/kamp-queue-multi')
    const queueIdx = e.dataTransfer.getData('text/kamp-queue-idx')
    const trackPath = e.dataTransfer.getData('text/kamp-track-path')
    const albumJson = e.dataTransfer.getData('text/kamp-album')
    if (multiJson !== '') {
      const sorted: number[] = JSON.parse(multiJson)
      void reorderQueue(computeNewOrder(tracks.length, sorted, dropIdx))
    } else if (queueIdx !== '') {
      const from = Number(queueIdx)
      if (from !== dropIdx) void moveQueueTrack(from, dropIdx)
    } else if (trackPath) {
      void insertIntoQueue(trackPath, dropIdx)
    } else if (e.dataTransfer.getData('text/kamp-file-paths')) {
      try {
        const paths: string[] = JSON.parse(e.dataTransfer.getData('text/kamp-file-paths'))
        void (async () => {
          for (let i = 0; i < paths.length; i++) await insertIntoQueue(paths[i], dropIdx + i)
        })()
      } catch {
        // malformed — ignore
      }
    } else if (albumJson) {
      try {
        const {
          album_artist,
          album,
          file_path = ''
        } = JSON.parse(albumJson) as {
          album_artist: string
          album: string
          file_path?: string
        }
        void insertAlbumAt(album_artist, album, dropIdx, file_path)
      } catch {
        // malformed drag data — ignore
      }
    } else if (e.dataTransfer.getData('text/kamp-artist')) {
      try {
        const { name } = JSON.parse(e.dataTransfer.getData('text/kamp-artist')) as { name: string }
        void insertArtistAt(name, dropIdx)
      } catch {
        // malformed drag data — ignore
      }
    } else {
      const playlistIdStr = e.dataTransfer.getData('text/kamp-playlist')
      if (playlistIdStr) {
        void (async () => {
          await loadPlaylistTracks(Number(playlistIdStr))
          const paths = useStore.getState().library.playlistTracks.map((t) => t.file_path)
          for (let i = 0; i < paths.length; i++) await insertIntoQueue(paths[i], dropIdx + i)
        })()
      }
    }
  }

  function handleListDrop(e: React.DragEvent): void {
    // Fires only when dropping on the empty space below all track rows (li handlers
    // stop propagation, so this never fires when the target is a track row).
    e.currentTarget.classList.remove('queue-tail-drop')
    const multiJson = e.dataTransfer.getData('text/kamp-queue-multi')
    const queueIdx = e.dataTransfer.getData('text/kamp-queue-idx')
    const trackPath = e.dataTransfer.getData('text/kamp-track-path')
    const albumJson = e.dataTransfer.getData('text/kamp-album')
    if (multiJson !== '') {
      const sorted: number[] = JSON.parse(multiJson)
      // tail drop = insert after all tracks
      void reorderQueue(computeNewOrder(tracks.length, sorted, tracks.length))
    } else if (queueIdx !== '') {
      const from = Number(queueIdx)
      const last = tracks.length - 1
      if (from !== last) void moveQueueTrack(from, last)
    } else if (trackPath) {
      void addToQueue(trackPath)
    } else if (e.dataTransfer.getData('text/kamp-file-paths')) {
      try {
        const paths: string[] = JSON.parse(e.dataTransfer.getData('text/kamp-file-paths'))
        void (async () => {
          for (const p of paths) await addToQueue(p)
        })()
      } catch {
        // malformed — ignore
      }
    } else if (albumJson) {
      try {
        const {
          album_artist,
          album,
          file_path = ''
        } = JSON.parse(albumJson) as {
          album_artist: string
          album: string
          file_path?: string
        }
        void addAlbumToQueue(album_artist, album, file_path)
      } catch {
        // malformed drag data — ignore
      }
    } else if (e.dataTransfer.getData('text/kamp-artist')) {
      try {
        const { name } = JSON.parse(e.dataTransfer.getData('text/kamp-artist')) as { name: string }
        void insertArtistAt(name, tracks.length)
      } catch {
        // malformed drag data — ignore
      }
    } else {
      const playlistIdStr = e.dataTransfer.getData('text/kamp-playlist')
      if (playlistIdStr) {
        void (async () => {
          await loadPlaylistTracks(Number(playlistIdStr))
          const paths = useStore.getState().library.playlistTracks.map((t) => t.file_path)
          for (const p of paths) await addToQueue(p)
        })()
      }
    }
  }

  function renderTrackRow(track: Track, idx: number): React.JSX.Element {
    const isCurrent = idx === position
    const isPlayed = position >= 0 && idx < position
    const isUnplayed = position >= 0 && idx > position
    const isSelected = selectedIndices.has(idx)
    const isOffline = (track.source !== 'local' && !bandcampConnected) || !track.reachable
    return (
      <li
        key={idx}
        data-drop-idx={idx}
        className={[
          'queue-track-row',
          isCurrent ? 'current' : '',
          isPlayed ? 'played' : '',
          isSelected ? 'selected' : '',
          isOffline ? 'queue-track-row--offline' : ''
        ]
          .filter(Boolean)
          .join(' ')}
        draggable={!isCurrent && !albumGroupingActive}
        onMouseDown={(e) => handleRowMouseDown(e, idx)}
        onMouseUp={() => handleRowMouseUp(idx)}
        onDragStart={(e) => {
          if (isCurrent) return
          // A drag started — cancel any pending selection collapse so the full
          // selection is available for the multi-drag path below.
          pendingSingleSelect.current = null
          // Filter the current playing track out of multi-drag —
          // it is not draggable and including it would corrupt _pos.
          const dragCandidates = [...selectedIndices].filter((i) => i !== position)
          const isMulti = isSelected && dragCandidates.length > 1
          if (isMulti) {
            const sorted = dragCandidates.sort((a, b) => a - b)
            e.dataTransfer.setData('text/kamp-queue-idx', String(idx))
            e.dataTransfer.setData('text/kamp-queue-multi', JSON.stringify(sorted))
            const ghost = document.createElement('div')
            ghost.textContent = `${sorted.length} tracks`
            ghost.style.cssText =
              'position:fixed;top:-100px;background:var(--accent);color:#fff;padding:4px 10px;border-radius:3px;font-size:12px;font-weight:600'
            document.body.appendChild(ghost)
            e.dataTransfer.setDragImage(ghost, 0, 0)
            // Browser latches the ghost image synchronously; safe to remove next frame
            requestAnimationFrame(() => document.body.removeChild(ghost))
          } else {
            // Solo drag: clear any selection so the drop handler uses single-move path
            setSelectedIndices(new Set())
            setAnchorIdx(null)
            e.dataTransfer.setData('text/kamp-queue-idx', String(idx))
          }
          e.dataTransfer.effectAllowed = 'move'
        }}
        onDragEnd={() => {
          // Always clear after drag — avoids stale index mapping when loadQueue()
          // returns the reordered array with the same length.
          setSelectedIndices(new Set())
          setAnchorIdx(null)
        }}
        onDragOver={(e) => {
          if (!isQueueDrop(e.dataTransfer.types)) return
          e.preventDefault()
          e.stopPropagation()
          e.currentTarget.classList.add('drag-over')
          // Clear tail-drop outline when pointer enters a row.
          listRef.current?.classList.remove('queue-tail-drop')
        }}
        onDragLeave={(e) => {
          e.stopPropagation()
          if (!e.currentTarget.contains(e.relatedTarget as Node)) {
            e.currentTarget.classList.remove('drag-over')
          }
        }}
        onDrop={(e) => handleDrop(e, idx)}
        onDoubleClick={() => void skipToQueueTrack(idx)}
        onContextMenu={(e) => {
          e.preventDefault()
          e.stopPropagation()
          // Compute the intended new selection synchronously — setSelectedIndices
          // is async in React so we can't read it back in the same event handler.
          const nextIndices = isSelected ? selectedIndices : new Set([idx])
          if (!isSelected) {
            setSelectedIndices(nextIndices)
            setAnchorIdx(idx)
          }
          const sortedIndices = [...nextIndices].sort((a, b) => a - b)
          const selectedTracks = sortedIndices.map((i) => tracks[i])
          const unplayedSelectedIndices = sortedIndices.filter((i) => i > position)
          setMenu({
            x: e.clientX,
            y: e.clientY,
            trackIdx: isUnplayed ? idx : null,
            track,
            selectedTracks,
            unplayedSelectedIndices
          })
        }}
      >
        <span className="queue-track-fav">
          {track.favorite && <FavoriteIcon active size={10} />}
        </span>
        <span className="queue-track-num">{idx + 1}</span>
        <span className="queue-track-title">
          {isOffline && (
            <span className="queue-track-offline-icon" title="Track unavailable" aria-hidden="true">
              <WarnIcon size={11} />
            </span>
          )}
          {track.title}
        </span>
        <span className="queue-track-artist">{track.artist}</span>
      </li>
    )
  }

  // Mirrors renderTrackRow: keeping the album-card JSX (and its ref-reading drop
  // handlers) inside a named function rather than inline in the render map keeps
  // the react-hooks/refs lint rule from flagging handleDrop's listRef access.
  function renderAlbumCard(item: Extract<NextUpItem, { kind: 'album' }>): React.JSX.Element {
    return (
      <QueueAlbumCard
        key={`album:${item.albumArtist}\0${item.album}`}
        albumArtist={item.albumArtist}
        album={item.album}
        tracks={item.tracks}
        trackIndices={item.trackIndices}
        isDragging={false}
        onPointerDown={handleAlbumCardPointerDown}
        onDragOver={(e) => {
          if (!isQueueDrop(e.dataTransfer.types)) return
          e.preventDefault()
          e.stopPropagation()
          e.currentTarget.classList.add('drag-over')
          listRef.current?.classList.remove('queue-tail-drop')
        }}
        onDragLeave={(e) => {
          e.stopPropagation()
          if (!e.currentTarget.contains(e.relatedTarget as Node)) {
            e.currentTarget.classList.remove('drag-over')
          }
        }}
        onDrop={(e) => handleDrop(e, item.trackIndices[0])}
        onContextMenu={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setMenu({
            x: e.clientX,
            y: e.clientY,
            // clear_remaining keeps through trackIdx and removes
            // everything after — pass the last track of the album so
            // the full album is kept and everything beyond is cleared.
            trackIdx: item.trackIndices[item.trackIndices.length - 1],
            track: item.tracks[0],
            selectedTracks: item.tracks,
            unplayedSelectedIndices: item.trackIndices
          })
        }}
      />
    )
  }

  const listContextMenu = (e: React.MouseEvent): void => {
    e.preventDefault()
    setMenu({
      x: e.clientX,
      y: e.clientY,
      trackIdx: null,
      selectedTracks: [],
      unplayedSelectedIndices: []
    })
  }

  const hasHistory = position > 0
  const historyCount = Math.max(0, position)
  const isPlaying = position >= 0 && position < tracks.length

  return (
    <aside
      className={`queue-panel${isResizing ? ' queue-panel--resizing' : ''}`}
      style={{ width: queueWidth }}
    >
      <div
        className="queue-resize-handle"
        onMouseDown={handleResizeMouseDown}
        onDoubleClick={handleResizeDoubleClick}
      />
      <div className="queue-panel-header">
        <span className="queue-panel-label">QUEUE</span>
        <button
          className="queue-close-btn"
          onClick={toggleQueuePanel}
          {...tooltip(TOOLTIPS.QUEUE_CLOSE)}
        >
          ✕
        </button>
      </div>
      {tracks.length === 0 ? (
        <div
          className="queue-empty"
          onDragOver={(e) => {
            if (!isQueueDrop(e.dataTransfer.types)) return
            e.preventDefault()
          }}
          onDrop={(e) => {
            const trackPath = e.dataTransfer.getData('text/kamp-track-path')
            const albumJson = e.dataTransfer.getData('text/kamp-album')
            if (trackPath) {
              void addToQueue(trackPath)
            } else if (albumJson) {
              try {
                const {
                  album_artist,
                  album,
                  file_path = ''
                } = JSON.parse(albumJson) as {
                  album_artist: string
                  album: string
                  file_path?: string
                }
                void addAlbumToQueue(album_artist, album, file_path)
              } catch {
                // malformed drag data — ignore
              }
            }
          }}
        >
          No tracks in queue.
        </div>
      ) : isPlaying ? (
        <>
          {/* Pinned block: History + Now Playing — always visible */}
          <div className="queue-pinned">
            <div
              className={`queue-section-header queue-section-header--history${!hasHistory ? ' disabled' : ''}`}
              onMouseDown={(e) => e.stopPropagation()}
              onContextMenu={(e) => {
                e.stopPropagation()
                e.preventDefault()
              }}
            >
              <span>HISTORY ({historyCount})</span>
              {hasHistory && (
                <button className="queue-history-toggle" onClick={toggleHistoryCollapsed}>
                  {historyCollapsed ? '▸' : '▾'}
                </button>
              )}
            </div>
            {!historyCollapsed && historyCount > 0 && (
              <ol
                ref={historyListRef}
                className="queue-history-list"
                onContextMenu={listContextMenu}
                onDragOver={(e) => {
                  if (!isQueueDrop(e.dataTransfer.types)) return
                  e.preventDefault()
                }}
                onDrop={(e) => e.preventDefault()}
              >
                {tracks.slice(0, position).map((track, i) => renderTrackRow(track, i))}
              </ol>
            )}
            <div
              className="queue-section-header queue-section-header--now-playing"
              onMouseDown={(e) => e.stopPropagation()}
              onContextMenu={(e) => {
                e.stopPropagation()
                e.preventDefault()
              }}
            >
              <span>NOW PLAYING</span>
            </div>
            <ol ref={nowPlayingListRef} className="queue-now-playing-list">
              {renderTrackRow(tracks[position], position)}
            </ol>
          </div>

          {/* Scrollable block: Next Up */}
          <div className="queue-next-up">
            <div
              className="queue-section-header queue-section-header--next-up"
              onMouseDown={(e) => e.stopPropagation()}
              onContextMenu={(e) => {
                e.stopPropagation()
                e.preventDefault()
              }}
            >
              <span>NEXT UP{albumGroupingActive ? ' — ALBUM VIEW' : ''}</span>
              <button
                className={`queue-album-toggle${albumGroupingActive ? ' queue-album-toggle--active' : ''}`}
                onClick={toggleAlbumGrouping}
                {...tooltip(TOOLTIPS.QUEUE_ALBUM_VIEW)}
                aria-pressed={albumGroupingActive}
              >
                <GoToAlbumIcon size={14} />
              </button>
            </div>
            <ol
              ref={listRef}
              className="queue-track-list"
              onContextMenu={listContextMenu}
              onDragOver={(e) => {
                if (!isQueueDrop(e.dataTransfer.types)) return
                e.preventDefault()
                e.currentTarget.classList.add('queue-tail-drop')
              }}
              onDragLeave={(e) => {
                // Only remove the indicator when the pointer leaves the <ol> entirely,
                // not when entering a child <li> (which stops its own drag events).
                if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                  e.currentTarget.classList.remove('queue-tail-drop')
                }
              }}
              onDrop={handleListDrop}
            >
              {albumGroupingActive
                ? nextUpItems.map((item) =>
                    item.kind === 'track'
                      ? renderTrackRow(item.track, item.queueIdx)
                      : renderAlbumCard(item)
                  )
                : tracks
                    .slice(position + 1)
                    .map((track, i) => renderTrackRow(track, position + 1 + i))}
            </ol>
          </div>
        </>
      ) : (
        // Nothing playing — flat list
        <ol
          ref={listRef}
          className="queue-track-list"
          onContextMenu={listContextMenu}
          onDragOver={(e) => {
            if (!isQueueDrop(e.dataTransfer.types)) return
            e.preventDefault()
            e.currentTarget.classList.add('queue-tail-drop')
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node)) {
              e.currentTarget.classList.remove('queue-tail-drop')
            }
          }}
          onDrop={handleListDrop}
        >
          {tracks.map((track, idx) => renderTrackRow(track, idx))}
        </ol>
      )}
      {menu && (
        <QueueContextMenu
          x={menu.x}
          y={menu.y}
          trackIdx={menu.trackIdx}
          track={menu.track}
          selectedTracks={menu.selectedTracks}
          unplayedSelectedIndices={menu.unplayedSelectedIndices}
          position={position}
          onClearSelection={() => setSelectedIndices(new Set())}
          onClose={() => setMenu(null)}
        />
      )}
    </aside>
  )
}
