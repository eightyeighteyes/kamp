/**
 * Zustand store.
 *
 * All player and library state lives here. The renderer is a pure view layer:
 * components read from the store and dispatch actions — they hold no state
 * of their own that belongs in the daemon.
 */

import { create } from 'zustand'
import * as api from './api/client'
import type {
  Album,
  PlayerState,
  QueueState,
  ScanProgress,
  ScanResult,
  SearchResult,
  Track
} from './api/client'

type LibraryState = {
  albums: Album[]
  artists: string[]
  selectedArtist: string | null
  selectedAlbum: Album | null
  tracks: Track[]
  tracksAlbumKey: string | null // "artist\0album" key for the loaded track list
}

type PlayerStore = {
  player: PlayerState
  library: LibraryState
  serverStatus: 'connected' | 'reconnecting' | 'disconnected'
  scanStatus: 'idle' | 'scanning' | 'done' | 'error'
  lastScanResult: ScanResult | null
  scanError: string | null
  scanProgress: ScanProgress | null

  configuredLibraryPath: string | null
  activeView: 'library' | 'now-playing'
  sortOrder: 'album_artist' | 'album' | 'date_added' | 'last_played'
  searchQuery: string
  searchResults: SearchResult | null
  queueVisible: boolean
  queue: QueueState | null

  // Actions
  setServerStatus: (status: 'connected' | 'reconnecting' | 'disconnected') => void
  toggleQueuePanel: () => void
  loadQueue: () => Promise<void>
  setSearchQuery: (q: string) => void
  setSortOrder: (sort: 'album_artist' | 'album' | 'date_added' | 'last_played') => Promise<void>
  setActiveView: (view: 'library' | 'now-playing') => Promise<void>
  loadLibrary: () => Promise<void>
  loadUiState: () => Promise<void>
  selectArtist: (artist: string | null) => void
  selectAlbum: (album: Album | null) => Promise<void>
  loadTracks: (albumArtist: string, album: string) => Promise<void>
  playAlbum: (albumArtist: string, album: string, trackIndex?: number) => Promise<void>
  playTrack: (albumArtist: string, album: string, trackIndex: number) => Promise<void>
  togglePlayPause: () => Promise<void>
  stop: () => Promise<void>
  next: () => Promise<void>
  prev: () => Promise<void>
  seek: (position: number) => Promise<void>
  setVolume: (volume: number) => Promise<void>
  setShuffle: (shuffle: boolean) => Promise<void>
  setRepeat: (repeat: boolean) => Promise<void>
  addAlbumToQueue: (albumArtist: string, album: string) => Promise<void>
  playAlbumNext: (albumArtist: string, album: string) => Promise<void>
  insertAlbumAt: (albumArtist: string, album: string, index: number) => Promise<void>
  addToQueue: (filePath: string) => Promise<void>
  insertIntoQueue: (filePath: string, index: number) => Promise<void>
  playNext: (filePath: string) => Promise<void>
  moveQueueTrack: (fromIndex: number, toIndex: number) => Promise<void>
  skipToQueueTrack: (position: number) => Promise<void>
  clearQueue: () => Promise<void>
  clearRemainingQueue: (position: number) => Promise<void>
  setFavorite: (filePath: string, favorite: boolean) => Promise<void>
  refreshOpenAlbum: () => Promise<void>
  scanLibrary: () => Promise<void>
  setLibraryPath: (path: string) => Promise<void>
  applyServerState: (state: PlayerState) => void
}

const initialPlayer: PlayerState = {
  playing: false,
  position: 0,
  duration: 0,
  volume: 100,
  current_track: null
}

export const useStore = create<PlayerStore>((set, get) => ({
  player: initialPlayer,
  library: {
    albums: [],
    artists: [],
    selectedArtist: null,
    selectedAlbum: null,
    tracks: [],
    tracksAlbumKey: null
  },
  serverStatus: 'reconnecting',
  scanStatus: 'idle',
  lastScanResult: null,
  scanError: null,
  scanProgress: null,
  configuredLibraryPath: null,
  activeView: 'library',
  sortOrder: 'album_artist',
  searchQuery: '',
  searchResults: null,
  queueVisible: false,
  queue: null,

  setServerStatus: (status) => set({ serverStatus: status }),

  toggleQueuePanel: () => {
    const next = !get().queueVisible
    set({ queueVisible: next })
    void api.setQueuePanelApi(next)
  },

  loadQueue: async () => {
    try {
      const queue = await api.getQueue()
      set({ queue })
    } catch {
      // Best-effort — stale or empty queue is fine.
    }
  },

  setSortOrder: async (sort) => {
    set({ sortOrder: sort })
    await get().loadLibrary()
    const q = get().searchQuery
    if (q.trim()) await get().setSearchQuery(q)
    try {
      await api.setSortOrderApi(sort)
    } catch {
      // Best-effort — preference is already applied locally.
    }
  },

  setSearchQuery: async (q) => {
    set({ searchQuery: q })
    if (!q.trim()) {
      set({ searchResults: null })
      return
    }
    try {
      const results = await api.search(q, get().sortOrder)
      // Only apply if the query hasn't changed since we fired the request.
      if (get().searchQuery === q) {
        set({ searchResults: results })
      }
    } catch {
      // Ignore transient errors — stale results are better than a broken UI.
    }
  },

  setActiveView: async (view) => {
    set({ activeView: view })
    try {
      await api.setActiveViewApi(view)
    } catch {
      // Best-effort — view is already updated locally; daemon will sync on next connect.
    }
  },

  loadUiState: async () => {
    try {
      const ui = await api.getUiState()
      set({
        activeView: ui.active_view,
        sortOrder: ui.sort_order,
        queueVisible: ui.queue_panel_open
      })
    } catch {
      // Server unreachable — keep default.
    }
  },

  applyServerState: (state) => {
    const prevTrack = get().player.current_track
    set({ player: state })
    // When the track changes (e.g. auto-advance at end-of-track), the queue
    // position has moved server-side — reload so the panel stays in sync.
    if (state.current_track?.file_path !== prevTrack?.file_path) {
      void get().loadQueue()
    }
  },

  loadLibrary: async () => {
    try {
      const sort = get().sortOrder
      const [albums, artists] = await Promise.all([api.getAlbums(sort), api.getArtists()])
      set((s) => ({ library: { ...s.library, albums, artists }, serverStatus: 'connected' }))
    } catch {
      set({ serverStatus: 'disconnected' })
    }
  },

  refreshOpenAlbum: async () => {
    // Force-reload the track list for the currently open album, bypassing the
    // key guard in loadTracks. Called after background scans so additions and
    // deletions are reflected immediately without the user having to navigate away.
    const { selectedAlbum } = get().library
    if (!selectedAlbum) return
    try {
      const tracks = await api.getTracksForAlbum(selectedAlbum.album_artist, selectedAlbum.album)
      set((s) => ({
        library: {
          ...s.library,
          tracks,
          tracksAlbumKey: `${selectedAlbum.album_artist}\0${selectedAlbum.album}`
        }
      }))
    } catch {
      // Best-effort — stale track list is better than a broken UI.
    }
  },

  selectArtist: (artist) =>
    set((s) => ({ library: { ...s.library, selectedArtist: artist, selectedAlbum: null } })),

  selectAlbum: async (album) => {
    set((s) => ({ library: { ...s.library, selectedAlbum: album } }))
    if (album) await get().loadTracks(album.album_artist, album.album)
  },

  loadTracks: async (albumArtist, album) => {
    const key = `${albumArtist}\0${album}`
    if (get().library.tracksAlbumKey === key) return
    const tracks = await api.getTracksForAlbum(albumArtist, album)
    set((s) => ({ library: { ...s.library, tracks, tracksAlbumKey: key } }))
  },

  playAlbum: async (albumArtist, album, trackIndex = 0) => {
    await api.playAlbum(albumArtist, album, trackIndex)
    void get().loadQueue()
  },

  playTrack: async (albumArtist, album, trackIndex) => {
    await api.playAlbum(albumArtist, album, trackIndex)
    void get().loadQueue()
  },

  togglePlayPause: async () => {
    const { playing } = get().player
    if (playing) {
      await api.pause()
    } else {
      await api.resume()
    }
  },

  stop: async () => {
    await api.stop()
  },

  next: async () => {
    await api.nextTrack()
    void get().loadQueue()
  },

  prev: async () => {
    await api.prevTrack()
    void get().loadQueue()
  },

  seek: async (position) => {
    await api.seek(position)
  },

  setVolume: async (volume) => {
    await api.setVolume(volume)
    set((s) => ({ player: { ...s.player, volume } }))
  },

  setShuffle: async (shuffle) => {
    await api.setShuffle(shuffle)
    void get().loadQueue()
  },

  setRepeat: async (repeat) => {
    await api.setRepeat(repeat)
  },

  addAlbumToQueue: async (albumArtist, album) => {
    await api.addAlbumToQueue(albumArtist, album)
    void get().loadQueue()
  },

  playAlbumNext: async (albumArtist, album) => {
    await api.playAlbumNext(albumArtist, album)
    void get().loadQueue()
  },

  insertAlbumAt: async (albumArtist, album, index) => {
    await api.insertAlbumAt(albumArtist, album, index)
    void get().loadQueue()
  },

  addToQueue: async (filePath) => {
    await api.addToQueue(filePath)
    void get().loadQueue()
  },

  insertIntoQueue: async (filePath, index) => {
    await api.insertIntoQueue(filePath, index)
    void get().loadQueue()
  },

  playNext: async (filePath) => {
    await api.playNext(filePath)
    void get().loadQueue()
  },

  moveQueueTrack: async (fromIndex, toIndex) => {
    await api.moveQueueTrack(fromIndex, toIndex)
    void get().loadQueue()
  },

  skipToQueueTrack: async (position) => {
    await api.skipToQueueTrack(position)
    void get().loadQueue()
  },

  clearQueue: async () => {
    await api.clearQueue()
    void get().loadQueue()
  },

  clearRemainingQueue: async (position) => {
    await api.clearRemainingQueue(position)
    void get().loadQueue()
  },

  setFavorite: async (filePath, favorite) => {
    await api.setTrackFavorite(filePath, favorite)
    // Keep the player state in sync if the favorited track is currently playing.
    if (get().player.current_track?.file_path === filePath) {
      set((s) => ({
        player: {
          ...s.player,
          current_track: s.player.current_track ? { ...s.player.current_track, favorite } : null
        }
      }))
    }
    // Patch any matching tracks in the queue so the indicator updates immediately.
    set((s) => ({
      queue: s.queue
        ? {
            ...s.queue,
            tracks: s.queue.tracks.map((t) => (t.file_path === filePath ? { ...t, favorite } : t))
          }
        : s.queue
    }))
    // Patch search results so the favorite glyph updates without a re-search.
    set((s) => ({
      searchResults: s.searchResults
        ? {
            ...s.searchResults,
            tracks: s.searchResults.tracks.map((t) =>
              t.file_path === filePath ? { ...t, favorite } : t
            )
          }
        : s.searchResults
    }))
    // Reload the open album track list so the heart in track rows updates.
    await get().refreshOpenAlbum()
  },

  setLibraryPath: async (path) => {
    await api.setLibraryPath(path)
    set({ configuredLibraryPath: path })
  },

  scanLibrary: async () => {
    set({ scanStatus: 'scanning', scanError: null, scanProgress: null })

    // Poll the server for progress at ~2 Hz while the scan runs.
    const pollInterval = setInterval(async () => {
      try {
        const progress = await api.getScanProgress()
        set({ scanProgress: progress })
      } catch {
        // Ignore transient poll errors — the scan result is what matters.
      }
    }, 500)

    try {
      const result = await api.scanLibrary()
      set({ scanStatus: 'done', lastScanResult: result, scanProgress: null })
      await get().loadLibrary()
    } catch (err) {
      const msg =
        err instanceof Error && err.message.includes('503')
          ? 'Library path not configured. Use the "Choose Library Folder" button.'
          : 'Scan failed. Check the server logs for details.'
      set({ scanStatus: 'error', scanError: msg, scanProgress: null })
    } finally {
      clearInterval(pollInterval)
    }
  }
}))
