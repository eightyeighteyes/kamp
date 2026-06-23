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
  AlbumTagsCollision,
  ConfigValues,
  CriteriaDoc,
  PlayerState,
  Playlist,
  PlaylistTrack,
  QueueState,
  ScanProgress,
  ScanResult,
  SearchResult,
  Track
} from './api/client'
import type { DisplayStyle } from './components/modules/registry'
import type { MagicPlaylistContents, MagicPlaylistSort } from './api/client'

export type TrackDisplaySize = 'teeny' | 'less-teeny' | 'large-print'

export type MagicPlaylistModuleConfig = {
  playlistId: number | null
  sort: MagicPlaylistSort
  contents: MagicPlaylistContents
  items: number
}
export type PlasmaMode = 'always' | 'sometimes' | 'never'
export type TraceStyle = 'clean' | 'glowy' | 'trippy'

type LibraryState = {
  albums: Album[]
  artists: string[]
  selectedArtist: string | null
  selectedAlbum: Album | null
  tracks: Track[]
  tracksAlbumKey: string | null // "artist\0album" key for the loaded track list
  collectionType: 'albums' | 'playlists'
  playlists: Playlist[]
  selectedPlaylist: Playlist | null
  playlistTracks: PlaylistTrack[]
  playlistTracksLoading: boolean
}

type PlayerStore = {
  player: PlayerState
  library: LibraryState
  serverStatus: 'connected' | 'reconnecting' | 'disconnected'
  scanStatus: 'idle' | 'scanning' | 'done' | 'error'
  lastScanResult: ScanResult | null
  scanError: string | null
  scanProgress: ScanProgress | null

  leftDb: number | null
  rightDb: number | null
  crestDb: number | null
  peakDb: number | null

  configuredLibraryPath: string | null
  activeView: 'library' | 'now-playing' | 'home'
  moduleOrder: string[]
  hiddenModules: string[]
  moduleDisplayStyles: Record<string, DisplayStyle>
  lastPlayedCount: number
  lastPlayedDays: number
  lastPlayedVersion: number
  recentlyAddedCount: number
  recentlyAddedDays: number
  highlightEnabled: boolean
  highlightCutoffSecs: number
  highlightStyle: string
  dismissedHighlightKeys: Set<string>
  topAlbumsCount: number
  topTracksCount: number
  topArtistsCount: number
  statsTopTracksCount: number
  favoritePlaylistsCount: number
  favoritePlaylistsSortOrder: 'last_played_at' | 'title'
  magicPlaylistConfigs: Record<string, MagicPlaylistModuleConfig>
  magicPlaylistVersion: number
  flashTrackId: number | null
  baseKampEditMode: boolean
  stereoRackTrackSize: TrackDisplaySize
  stereoRackPlasmaMode: PlasmaMode
  stereoRackTraceStyle: TraceStyle
  albumEditMode: boolean
  albumMetaExpanded: boolean
  albumRenameProgress: { done: number; total: number } | null
  sortOrder: 'album_artist' | 'album' | 'date_added' | 'last_played' | 'most_played'
  sortDir: 'asc' | 'desc'
  libraryFilter: string[]
  searchQuery: string
  searchResults: SearchResult | null
  queueVisible: boolean
  artistPanelVisible: boolean
  artistPanelSnapshot: boolean | null // saved visibility before entering Now Playing
  queue: QueueState | null
  albumGroupingActive: boolean
  downloadingAlbumIds: Set<string>
  queuedAlbumIds: Set<string>
  flashToast: string | null

  // Actions
  setAudioLevel: (leftDb: number, rightDb: number, crestDb: number, peakDb: number) => void
  setServerStatus: (status: 'connected' | 'reconnecting' | 'disconnected') => void
  toggleQueuePanel: () => void
  toggleArtistPanel: () => void
  loadQueue: () => Promise<void>
  setSearchQuery: (q: string) => void
  setSortOrder: (
    sort: 'album_artist' | 'album' | 'date_added' | 'last_played' | 'most_played'
  ) => Promise<void>
  setSortDir: (dir: 'asc' | 'desc') => Promise<void>
  setLibraryFilter: (filters: string[]) => void
  setActiveView: (view: 'library' | 'now-playing' | 'home') => Promise<void>
  setModuleOrder: (ids: string[]) => void
  hideModule: (id: string) => void
  showModule: (id: string) => void
  setModuleDisplayStyle: (id: string, style: DisplayStyle) => void
  setLastPlayedCount: (n: number) => void
  setLastPlayedDays: (n: number) => void
  bumpLastPlayedVersion: () => void
  markAlbumDownloading: (saleItemId: string) => void
  clearAlbumDownloading: (saleItemId: string) => void
  markAlbumQueued: (saleItemId: string) => void
  clearAlbumQueued: (saleItemId: string) => void
  removeDownload: (saleItemId: string) => Promise<void>
  showFlashToast: (msg: string) => void
  setRecentlyAddedCount: (n: number) => void
  setRecentlyAddedDays: (n: number) => void
  setHighlightEnabled: (enabled: boolean) => void
  setHighlightStyle: (style: string) => void
  dismissHighlight: (album: Album) => void
  setTopAlbumsCount: (n: number) => void
  setTopTracksCount: (n: number) => void
  setTopArtistsCount: (n: number) => void
  setStatsTopTracksCount: (n: number) => void
  setFavoritePlaylistsCount: (n: number) => void
  setFavoritePlaylistsSortOrder: (order: 'last_played_at' | 'title') => void
  setMagicPlaylistConfig: (moduleId: string, config: MagicPlaylistModuleConfig) => void
  bumpMagicPlaylistVersion: () => void
  setFlashTrackId: (id: number) => void
  insertArtistAt: (artistName: string, idx: number) => Promise<void>
  toggleBaseKampEditMode: () => void
  setStereoRackTrackSize: (v: TrackDisplaySize) => void
  setStereoRackPlasmaMode: (v: PlasmaMode) => void
  setStereoRackTraceStyle: (v: TraceStyle) => void
  setAlbumEditMode: (mode: boolean) => void
  setAlbumMetaExpanded: (expanded: boolean) => void
  patchAlbumMeta: (
    albumArtist: string,
    album: string,
    opts: { genre?: string; label?: string; year?: string; mb_release_id?: string }
  ) => Promise<void>
  loadLibrary: () => Promise<void>
  loadUiState: () => Promise<void>
  selectArtist: (artist: string | null) => void
  selectAlbum: (album: Album | null) => Promise<void>
  loadTracks: (albumArtist: string, album: string, filePath?: string) => Promise<void>
  setCollectionType: (type: 'albums' | 'playlists') => void
  playPlaylist: (playlistId: number, startIndex?: number) => Promise<void>
  playFiles: (filePaths: string[], startIndex?: number) => Promise<void>
  recordPlaylistPlayed: (playlistId: number) => Promise<void>
  loadPlaylists: () => Promise<void>
  createPlaylist: (title: string) => Promise<Playlist>
  createMagicPlaylist: (title: string, criteria: CriteriaDoc) => Promise<Playlist>
  updateMagicPlaylistCriteria: (id: number, criteria: CriteriaDoc) => Promise<Playlist>
  selectPlaylist: (playlist: Playlist | null) => Promise<void>
  loadPlaylistTracks: (playlistId: number) => Promise<void>
  addTrackToPlaylist: (playlistId: number, filePath: string) => Promise<void>
  addAlbumToPlaylist: (playlistId: number, albumArtist: string, album: string) => Promise<void>
  removeTrackFromPlaylist: (playlistId: number, playlistTrackId: number) => Promise<void>
  reorderPlaylistTracks: (playlistId: number, trackIds: number[]) => Promise<void>
  setPlaylistFavorite: (playlistId: number, favorite: boolean) => Promise<void>
  renamePlaylist: (playlistId: number, title: string) => Promise<void>
  deletePlaylist: (playlistId: number) => Promise<void>
  patchOpenPlaylist: (playlist: Playlist) => void
  playAlbum: (
    albumArtist: string,
    album: string,
    trackIndex?: number,
    filePath?: string
  ) => Promise<void>
  playTrack: (
    albumArtist: string,
    album: string,
    trackIndex: number,
    filePath?: string
  ) => Promise<void>
  togglePlayPause: () => Promise<void>
  stop: () => Promise<void>
  next: () => Promise<void>
  prev: () => Promise<void>
  seek: (position: number) => Promise<void>
  setVolume: (volume: number) => Promise<void>
  setAlbumGroupingActive: (active: boolean) => void
  setShuffle: (shuffle: boolean) => Promise<void>
  setRepeat: (repeat: boolean) => Promise<void>
  addAlbumToQueue: (albumArtist: string, album: string, filePath?: string) => Promise<void>
  playAlbumNext: (albumArtist: string, album: string, filePath?: string) => Promise<void>
  insertAlbumAt: (
    albumArtist: string,
    album: string,
    index: number,
    filePath?: string
  ) => Promise<void>
  addToQueue: (filePath: string) => Promise<void>
  insertIntoQueue: (filePath: string, index: number) => Promise<void>
  playNext: (filePath: string) => Promise<void>
  moveQueueTrack: (fromIndex: number, toIndex: number) => Promise<void>
  reorderQueue: (order: number[]) => Promise<void>
  skipToQueueTrack: (position: number) => Promise<void>
  clearQueue: () => Promise<void>
  clearRemainingQueue: (position: number) => Promise<void>
  removeFromQueue: (indices: number[]) => Promise<void>
  setFavorite: (track: Track, favorite: boolean) => Promise<void>
  setFavorites: (tracks: Track[], favorite: boolean) => Promise<void>
  setAlbumFavorite: (albumArtist: string, album: string, favorite: boolean) => Promise<void>
  patchTrackTitle: (
    trackId: number,
    title: string,
    overwrite?: boolean
  ) => Promise<api.TrackTagsCollision | null>
  patchTrackDisplay: (trackId: number, displayTitle: string | null) => Promise<void>
  patchAlbumTags: (
    albumArtist: string,
    album: string,
    opts: { album?: string; album_artist?: string; overwrite?: boolean; skip_conflicts?: boolean }
  ) => Promise<AlbumTagsCollision | null>
  patchAlbumDisplay: (
    albumArtist: string,
    album: string,
    displayAlbum: string | null,
    displayAlbumArtist: string | null
  ) => Promise<void>
  deferredOps: Record<number, number> // track_id → op_id
  clearDeferredOp: (trackId: number) => void
  setAlbumRenameProgress: (progress: { done: number; total: number } | null) => void
  refreshOpenAlbum: () => Promise<void>
  patchOpenAlbum: (album: Album) => void
  scanLibrary: () => Promise<void>
  setLibraryPath: (path: string) => Promise<void>
  setWatchFolderPath: (path: string) => Promise<void>
  applyServerState: (state: PlayerState) => void

  // Preferences
  configValues: ConfigValues | null
  prefsOpen: boolean
  prefsInitialTab: 'general' | 'services' | 'extensions'
  loadConfig: () => Promise<void>
  setConfigValue: (key: string, value: string) => Promise<void>
  openPrefs: (tab?: 'general' | 'services' | 'extensions') => void
  closePrefs: () => void

  // Update notification
  updateAvailable: { version: string; notes: string } | null
  setUpdateAvailable: (data: { version: string; notes: string } | null) => void
}

const initialPlayer: PlayerState = {
  playing: false,
  position: 0,
  duration: 0,
  volume: 100,
  current_track: null,
  next_track: null,
  buffering: false
}

export const useStore = create<PlayerStore>((set, get) => ({
  player: initialPlayer,
  library: {
    albums: [],
    artists: [],
    selectedArtist: null,
    selectedAlbum: null,
    tracks: [],
    tracksAlbumKey: null,
    collectionType: 'albums',
    playlists: [],
    selectedPlaylist: null,
    playlistTracks: [],
    playlistTracksLoading: false
  },
  serverStatus: 'reconnecting',
  scanStatus: 'idle',
  lastScanResult: null,
  scanError: null,
  scanProgress: null,
  leftDb: null,
  rightDb: null,
  crestDb: null,
  peakDb: null,
  configuredLibraryPath: null,
  activeView: 'library',
  moduleOrder: (() => {
    const saved = localStorage.getItem('kamp:module-order')
    return saved
      ? (JSON.parse(saved) as string[])
      : ['kamp.stereo-rack', 'kamp.new-arrivals', 'kamp.last-played']
  })(),
  hiddenModules: (() => {
    const saved = localStorage.getItem('kamp:hidden-modules')
    return saved ? (JSON.parse(saved) as string[]) : []
  })(),
  moduleDisplayStyles: (() => {
    const saved = localStorage.getItem('kamp:module-display-styles')
    return saved ? (JSON.parse(saved) as Record<string, DisplayStyle>) : {}
  })(),
  lastPlayedCount: (() => {
    const saved = localStorage.getItem('kamp:last-played-count')
    return saved ? parseInt(saved) : 10
  })(),
  lastPlayedDays: (() => {
    const saved = localStorage.getItem('kamp:last-played-days')
    return saved ? parseInt(saved) : 30
  })(),
  lastPlayedVersion: 0,
  recentlyAddedCount: (() => {
    const saved = localStorage.getItem('kamp:recently-added-count')
    return saved ? parseInt(saved) : 10
  })(),
  recentlyAddedDays: (() => {
    const saved = localStorage.getItem('kamp:recently-added-days')
    return saved ? parseInt(saved) : 30
  })(),
  highlightEnabled: localStorage.getItem('kamp:highlight-enabled') !== 'false',
  highlightCutoffSecs: Date.now() / 1000 - 5 * 86400,
  highlightStyle: localStorage.getItem('kamp:highlight-style') ?? 'shiny',
  dismissedHighlightKeys: (() => {
    try {
      const saved = localStorage.getItem('kamp:dismissed-highlights')
      return new Set<string>(saved ? (JSON.parse(saved) as string[]) : [])
    } catch {
      return new Set<string>()
    }
  })(),
  topAlbumsCount: (() => {
    const saved = localStorage.getItem('kamp:top-albums-count')
    return saved ? parseInt(saved) : 10
  })(),
  topTracksCount: (() => {
    const saved = localStorage.getItem('kamp:top-tracks-count')
    return saved ? parseInt(saved) : 10
  })(),
  topArtistsCount: (() => {
    const saved = localStorage.getItem('kamp:top-artists-count')
    return saved ? parseInt(saved) : 10
  })(),
  statsTopTracksCount: (() => {
    const saved = localStorage.getItem('kamp:stats-top-tracks-count')
    return saved ? parseInt(saved) : 3
  })(),
  favoritePlaylistsCount: (() => {
    const saved = localStorage.getItem('kamp:fav-playlists-count')
    return saved ? parseInt(saved) : 10
  })(),
  favoritePlaylistsSortOrder:
    (localStorage.getItem('kamp:fav-playlists-sort') as 'last_played_at' | 'title' | null) ??
    'last_played_at',
  magicPlaylistConfigs: (() => {
    try {
      const saved = localStorage.getItem('kamp:magic-playlist-configs')
      return saved ? (JSON.parse(saved) as Record<string, MagicPlaylistModuleConfig>) : {}
    } catch {
      return {}
    }
  })(),
  magicPlaylistVersion: 0,
  flashTrackId: null,
  baseKampEditMode: localStorage.getItem('kamp:base-kamp-edit-mode') === 'true',
  stereoRackTrackSize:
    (localStorage.getItem('stereo-rack:track-size') as TrackDisplaySize) ?? 'teeny',
  stereoRackPlasmaMode:
    (localStorage.getItem('stereo-rack:plasma-mode') as PlasmaMode) ?? 'sometimes',
  stereoRackTraceStyle: (localStorage.getItem('stereo-rack:trace-style') as TraceStyle) ?? 'glowy',
  albumEditMode: false,
  albumMetaExpanded: localStorage.getItem('kamp:meta-expanded') === 'true',
  albumRenameProgress: null,
  sortOrder: 'album_artist',
  sortDir: 'asc',
  libraryFilter: [],
  searchQuery: '',
  searchResults: null,
  queueVisible: false,
  // Client-only persistence — no backend endpoint needed for this toggle.
  artistPanelVisible: localStorage.getItem('kamp:artist-panel-visible') !== 'false',
  artistPanelSnapshot: null,
  queue: null,
  albumGroupingActive: localStorage.getItem('kamp:album-view') === 'true',
  downloadingAlbumIds: new Set<string>(),
  queuedAlbumIds: new Set<string>(),
  flashToast: null,
  configValues: null,
  prefsOpen: false,
  prefsInitialTab: 'general',
  updateAvailable: null,
  deferredOps: {},

  setAudioLevel: (leftDb, rightDb, crestDb, peakDb) => set({ leftDb, rightDb, crestDb, peakDb }),

  setServerStatus: (status) => set({ serverStatus: status }),

  toggleQueuePanel: () => {
    const next = !get().queueVisible
    set({ queueVisible: next })
    void api.setQueuePanelApi(next)
  },

  toggleArtistPanel: () => {
    const next = !get().artistPanelVisible
    set({ artistPanelVisible: next })
    localStorage.setItem('kamp:artist-panel-visible', String(next))
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
    // Reset direction to the natural default for the new sort key so the
    // results make intuitive sense (e.g. "Date Added" → newest first).
    const naturalDir: 'asc' | 'desc' = ['date_added', 'last_played', 'most_played'].includes(sort)
      ? 'desc'
      : 'asc'
    set({ sortOrder: sort, sortDir: naturalDir })
    await get().loadLibrary()
    const q = get().searchQuery
    if (q.trim()) await get().setSearchQuery(q)
    try {
      await api.setSortOrderApi(sort, naturalDir)
    } catch {
      // Best-effort — preference is already applied locally.
    }
  },

  setSortDir: async (dir) => {
    set({ sortDir: dir })
    await get().loadLibrary()
    try {
      await api.setSortOrderApi(get().sortOrder, dir)
    } catch {
      // Best-effort.
    }
  },

  setLibraryFilter: (filters) => {
    set({ libraryFilter: filters })
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
    const { artistPanelVisible, artistPanelSnapshot } = get()
    if (view === 'library') {
      // Restore the artist panel to its pre-non-library state.
      const restored = artistPanelSnapshot ?? artistPanelVisible
      set({ activeView: view, artistPanelSnapshot: null, artistPanelVisible: restored })
      localStorage.setItem('kamp:artist-panel-visible', String(restored))
    } else {
      // Hide the artist panel (only relevant in Library) on any non-library view.
      // Preserve any existing snapshot so round-trips through multiple non-library views
      // (e.g. now-playing → home → library) still restore the original user preference.
      // Persist false so a restart outside the library doesn't reopen the panel.
      const snapshot = artistPanelSnapshot ?? artistPanelVisible
      set({ activeView: view, artistPanelSnapshot: snapshot, artistPanelVisible: false })
      localStorage.setItem('kamp:artist-panel-visible', 'false')
    }
    try {
      await api.setActiveViewApi(view)
    } catch {
      // Best-effort — view is already updated locally; daemon will sync on next connect.
    }
  },

  setModuleOrder: (ids) => {
    localStorage.setItem('kamp:module-order', JSON.stringify(ids))
    set({ moduleOrder: ids })
  },

  hideModule: (id) => {
    const next = [...get().hiddenModules, id]
    localStorage.setItem('kamp:hidden-modules', JSON.stringify(next))
    set({ hiddenModules: next })
  },

  showModule: (id) => {
    const { hiddenModules, moduleOrder } = get()
    const nextHidden = hiddenModules.filter((h) => h !== id)
    // Always place the re-added module at the bottom
    const nextOrder = [...moduleOrder.filter((oid) => oid !== id), id]
    localStorage.setItem('kamp:hidden-modules', JSON.stringify(nextHidden))
    localStorage.setItem('kamp:module-order', JSON.stringify(nextOrder))
    set({ hiddenModules: nextHidden, moduleOrder: nextOrder })
  },

  setModuleDisplayStyle: (id, style) => {
    const next = { ...get().moduleDisplayStyles, [id]: style }
    localStorage.setItem('kamp:module-display-styles', JSON.stringify(next))
    set({ moduleDisplayStyles: next })
  },

  setLastPlayedCount: (n) => {
    localStorage.setItem('kamp:last-played-count', String(n))
    set({ lastPlayedCount: n })
  },

  setLastPlayedDays: (n) => {
    localStorage.setItem('kamp:last-played-days', String(n))
    set({ lastPlayedDays: n })
  },

  bumpLastPlayedVersion: () => set((s) => ({ lastPlayedVersion: s.lastPlayedVersion + 1 })),
  markAlbumDownloading: (saleItemId) =>
    set((s) => ({ downloadingAlbumIds: new Set([...s.downloadingAlbumIds, saleItemId]) })),
  clearAlbumDownloading: (saleItemId) =>
    set((s) => {
      const next = new Set(s.downloadingAlbumIds)
      next.delete(saleItemId)
      return { downloadingAlbumIds: next }
    }),
  markAlbumQueued: (saleItemId) =>
    set((s) => ({ queuedAlbumIds: new Set([...s.queuedAlbumIds, saleItemId]) })),
  clearAlbumQueued: (saleItemId) =>
    set((s) => {
      const next = new Set(s.queuedAlbumIds)
      next.delete(saleItemId)
      return { queuedAlbumIds: next }
    }),
  removeDownload: async (saleItemId) => {
    try {
      await api.removeDownload(saleItemId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not remove download'
      get().showFlashToast(msg)
    }
  },
  showFlashToast: (msg) => {
    set({ flashToast: msg })
    setTimeout(() => set({ flashToast: null }), 5000)
  },

  setRecentlyAddedCount: (n) => {
    localStorage.setItem('kamp:recently-added-count', String(n))
    set({ recentlyAddedCount: n })
  },

  setRecentlyAddedDays: (n) => {
    localStorage.setItem('kamp:recently-added-days', String(n))
    set({ recentlyAddedDays: n })
  },

  setHighlightEnabled: (enabled) => {
    localStorage.setItem('kamp:highlight-enabled', String(enabled))
    set({ highlightEnabled: enabled })
  },

  setHighlightStyle: (style) => {
    localStorage.setItem('kamp:highlight-style', style)
    set({ highlightStyle: style })
  },

  dismissHighlight: (album) => {
    const key = album.missing_album
      ? (album.file_path ?? '')
      : `${album.album_artist}::${album.album}`
    const next = new Set(get().dismissedHighlightKeys)
    if (next.has(key)) return
    next.add(key)
    localStorage.setItem('kamp:dismissed-highlights', JSON.stringify([...next]))
    set({ dismissedHighlightKeys: next })
  },

  setTopAlbumsCount: (n) => {
    localStorage.setItem('kamp:top-albums-count', String(n))
    set({ topAlbumsCount: n })
  },

  setTopTracksCount: (n) => {
    localStorage.setItem('kamp:top-tracks-count', String(n))
    set({ topTracksCount: n })
  },

  setTopArtistsCount: (n) => {
    localStorage.setItem('kamp:top-artists-count', String(n))
    set({ topArtistsCount: n })
  },

  setStatsTopTracksCount: (n) => {
    localStorage.setItem('kamp:stats-top-tracks-count', String(n))
    set({ statsTopTracksCount: n })
  },

  setFavoritePlaylistsCount: (n) => {
    localStorage.setItem('kamp:fav-playlists-count', String(n))
    set({ favoritePlaylistsCount: n })
  },

  setFavoritePlaylistsSortOrder: (order) => {
    localStorage.setItem('kamp:fav-playlists-sort', order)
    set({ favoritePlaylistsSortOrder: order })
  },

  setMagicPlaylistConfig: (moduleId, config) => {
    const next = { ...get().magicPlaylistConfigs, [moduleId]: config }
    localStorage.setItem('kamp:magic-playlist-configs', JSON.stringify(next))
    set({ magicPlaylistConfigs: next })
  },

  bumpMagicPlaylistVersion: () =>
    set((s) => ({ magicPlaylistVersion: s.magicPlaylistVersion + 1 })),

  insertArtistAt: async (artistName, idx) => {
    const albums = get()
      .library.albums.filter((a) => a.album_artist === artistName)
      .sort((a, b) => (b.play_count_avg ?? 0) - (a.play_count_avg ?? 0))
    let offset = 0
    for (const album of albums) {
      await api.insertAlbumAt(album.album_artist, album.album, idx + offset, album.file_path ?? '')
      offset += album.track_count
    }
    void get().loadQueue()
  },

  setFlashTrackId: (id) => {
    set({ flashTrackId: id })
    setTimeout(() => set({ flashTrackId: null }), 2100)
  },

  toggleBaseKampEditMode: () => {
    const next = !get().baseKampEditMode
    localStorage.setItem('kamp:base-kamp-edit-mode', String(next))
    set({ baseKampEditMode: next })
  },

  setStereoRackTrackSize: (v) => {
    localStorage.setItem('stereo-rack:track-size', v)
    set({ stereoRackTrackSize: v })
  },

  setStereoRackPlasmaMode: (v) => {
    localStorage.setItem('stereo-rack:plasma-mode', v)
    set({ stereoRackPlasmaMode: v })
  },

  setStereoRackTraceStyle: (v) => {
    localStorage.setItem('stereo-rack:trace-style', v)
    set({ stereoRackTraceStyle: v })
  },

  setAlbumEditMode: (mode) => {
    // Auto-expand the liner notes panel when entering edit mode so the
    // editable fields are always visible without a separate interaction.
    if (mode && !get().albumMetaExpanded) {
      localStorage.setItem('kamp:meta-expanded', 'true')
      set({ albumEditMode: mode, albumMetaExpanded: true })
    } else {
      set({ albumEditMode: mode })
    }
  },

  setAlbumMetaExpanded: (expanded) => {
    localStorage.setItem('kamp:meta-expanded', String(expanded))
    set({ albumMetaExpanded: expanded })
  },

  loadUiState: async () => {
    try {
      const ui = await api.getUiState()
      set({
        activeView: ui.active_view,
        sortOrder: ui.sort_order,
        sortDir: ui.sort_dir ?? 'asc',
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
    if (state.current_track?.id !== prevTrack?.id) {
      void get().loadQueue()
    }
  },

  loadLibrary: async () => {
    try {
      const sort = get().sortOrder
      const dir = get().sortDir
      const [albums, artists, playlists] = await Promise.all([
        api.getAlbums(sort, dir),
        api.getArtists(),
        api.getPlaylists()
      ])
      set((s) => {
        // Refresh selectedAlbum so metadata changes (source, sale_item_id, etc.)
        // are visible immediately without needing to navigate away and back.
        const { selectedAlbum } = s.library
        const refreshedSelectedAlbum = selectedAlbum
          ? (albums.find(
              (a) =>
                a.album_artist === selectedAlbum.album_artist &&
                a.album === selectedAlbum.album &&
                a.file_path === selectedAlbum.file_path
            ) ?? selectedAlbum)
          : null
        return {
          library: {
            ...s.library,
            albums,
            artists,
            playlists,
            selectedAlbum: refreshedSelectedAlbum
          },
          serverStatus: 'connected'
        }
      })
    } catch {
      // During initial startup or mid-session reconnect the server may not be
      // ready yet — the WebSocket retry loop owns recovery. Only signal
      // 'disconnected' if we were actually connected when the fetch failed.
      if (get().serverStatus !== 'reconnecting') {
        set({ serverStatus: 'disconnected' })
      }
    }
  },

  refreshOpenAlbum: async () => {
    // Force-reload the track list for the currently open album, bypassing the
    // key guard in loadTracks. Called after background scans so additions and
    // deletions are reflected immediately without the user having to navigate away.
    const { selectedAlbum } = get().library
    if (!selectedAlbum) return
    try {
      const tracks = await api.getTracksForAlbum(
        selectedAlbum.album_artist,
        selectedAlbum.album,
        selectedAlbum.file_path
      )
      const key = selectedAlbum.file_path || `${selectedAlbum.album_artist}\0${selectedAlbum.album}`
      set((s) => ({ library: { ...s.library, tracks, tracksAlbumKey: key } }))
    } catch {
      // Best-effort — stale track list is better than a broken UI.
    }
  },

  patchOpenAlbum: (album) => set((s) => ({ library: { ...s.library, selectedAlbum: album } })),

  selectArtist: (artist) =>
    set((s) => ({ library: { ...s.library, selectedArtist: artist, selectedAlbum: null } })),

  selectAlbum: async (album) => {
    set((s) => ({
      library: { ...s.library, selectedAlbum: album, collectionType: 'albums' },
      albumEditMode: false
    }))
    if (album) await get().loadTracks(album.album_artist, album.album, album.file_path)
  },

  loadTracks: async (albumArtist, album, filePath = '') => {
    // For missing-album tracks, file_path is the unique key; use it so that
    // two no-album tracks with the same title don't share a cache entry.
    const key = filePath || `${albumArtist}\0${album}`
    if (get().library.tracksAlbumKey === key) return
    const tracks = await api.getTracksForAlbum(albumArtist, album, filePath)
    set((s) => ({ library: { ...s.library, tracks, tracksAlbumKey: key } }))
  },

  setCollectionType: (type) => set((s) => ({ library: { ...s.library, collectionType: type } })),

  playPlaylist: async (playlistId, startIndex = 0) => {
    await api.playPlaylist(playlistId, startIndex)
    void get().loadQueue()
    void get().loadPlaylists()
  },

  playFiles: async (filePaths, startIndex = 0) => {
    await api.playFiles(filePaths, startIndex)
    void get().loadQueue()
  },

  recordPlaylistPlayed: async (playlistId) => {
    await api.recordPlaylistPlayed(playlistId)
    void get().loadPlaylists()
  },

  loadPlaylists: async () => {
    const playlists = await api.getPlaylists()
    set((s) => ({ library: { ...s.library, playlists } }))
  },

  createPlaylist: async (title) => {
    const playlist = await api.createPlaylist(title)
    await get()
      .loadPlaylists()
      .catch(() => undefined)
    return playlist
  },

  createMagicPlaylist: async (title, criteria) => {
    const playlist = await api.createMagicPlaylist(title, criteria)
    await get()
      .loadPlaylists()
      .catch(() => undefined)
    return playlist
  },

  updateMagicPlaylistCriteria: async (id, criteria) => {
    const playlist = await api.updateMagicPlaylistCriteria(id, criteria)
    await get()
      .loadPlaylists()
      .catch(() => undefined)
    if (get().library.selectedPlaylist?.id === id) {
      const fresh = get().library.playlists.find((p) => p.id === id) ?? null
      set((s) => ({ library: { ...s.library, selectedPlaylist: fresh } }))
      void get().loadPlaylistTracks(id)
    }
    return playlist
  },

  selectPlaylist: async (playlist) => {
    set((s) => ({
      library: {
        ...s.library,
        selectedPlaylist: playlist,
        playlistTracks: [],
        playlistTracksLoading: !!playlist
      }
    }))
    if (playlist) await get().loadPlaylistTracks(playlist.id)
  },

  loadPlaylistTracks: async (playlistId) => {
    const playlistTracks = await api.getPlaylistTracks(playlistId)
    set((s) => ({
      library: {
        ...s.library,
        playlistTracks,
        playlistTracksLoading: false,
        // Keep the playlist card count in sync with the just-evaluated result
        playlists: s.library.playlists.map((p) =>
          p.id === playlistId && p.criteria !== null
            ? { ...p, track_count: playlistTracks.length }
            : p
        )
      }
    }))
  },

  addTrackToPlaylist: async (playlistId, filePath) => {
    await api.addTrackToPlaylist(playlistId, filePath)
    if (get().library.selectedPlaylist?.id === playlistId) {
      await get().loadPlaylistTracks(playlistId)
    }
    await get()
      .loadPlaylists()
      .catch(() => undefined)
  },

  addAlbumToPlaylist: async (playlistId, albumArtist, album) => {
    await api.addAlbumToPlaylist(playlistId, albumArtist, album)
    if (get().library.selectedPlaylist?.id === playlistId) {
      await get().loadPlaylistTracks(playlistId)
    }
    await get()
      .loadPlaylists()
      .catch(() => undefined)
  },

  removeTrackFromPlaylist: async (playlistId, playlistTrackId) => {
    await api.removeTrackFromPlaylist(playlistId, playlistTrackId)
    if (get().library.selectedPlaylist?.id === playlistId) {
      await get().loadPlaylistTracks(playlistId)
    }
    await get()
      .loadPlaylists()
      .catch(() => undefined)
  },

  reorderPlaylistTracks: async (playlistId, trackIds) => {
    // Optimistic update: reorder in-place before awaiting the API call.
    set((s) => {
      const { playlistTracks } = s.library
      const byId = new Map(playlistTracks.map((t) => [t.playlist_track_id, t]))
      const reordered = trackIds
        .map((id, pos) => {
          const t = byId.get(id)
          return t ? { ...t, position: pos } : null
        })
        .filter((t): t is PlaylistTrack => t !== null)
      return { library: { ...s.library, playlistTracks: reordered } }
    })
    await api.reorderPlaylistTracks(playlistId, trackIds)
  },

  setPlaylistFavorite: async (playlistId, favorite) => {
    await api.patchPlaylist(playlistId, { favorite })
    await get().loadPlaylists()
    if (get().library.selectedPlaylist?.id === playlistId) {
      const fresh = get().library.playlists.find((p) => p.id === playlistId) ?? null
      set((s) => ({ library: { ...s.library, selectedPlaylist: fresh } }))
    }
  },

  renamePlaylist: async (playlistId, title) => {
    await api.patchPlaylist(playlistId, { title })
    await get().loadPlaylists()
    if (get().library.selectedPlaylist?.id === playlistId) {
      // Replace selectedPlaylist from the freshly loaded list so updated_at
      // is current — the art URL includes updated_at for cache-busting.
      const fresh = get().library.playlists.find((p) => p.id === playlistId) ?? null
      set((s) => ({ library: { ...s.library, selectedPlaylist: fresh } }))
    }
  },

  deletePlaylist: async (playlistId) => {
    await api.deletePlaylist(playlistId)
    if (get().library.selectedPlaylist?.id === playlistId) {
      set((s) => ({
        library: { ...s.library, selectedPlaylist: null, playlistTracks: [] }
      }))
    }
    await get().loadPlaylists()
  },

  patchOpenPlaylist: (playlist) =>
    set((s) => ({
      library: {
        ...s.library,
        selectedPlaylist: playlist,
        playlists: s.library.playlists.map((p) => (p.id === playlist.id ? playlist : p))
      }
    })),

  playAlbum: async (albumArtist, album, trackIndex = 0, filePath = '') => {
    await api.playAlbum(albumArtist, album, trackIndex, filePath)
    void get().loadQueue()
  },

  playTrack: async (albumArtist, album, trackIndex, filePath = '') => {
    await api.playAlbum(albumArtist, album, trackIndex, filePath)
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

  setAlbumGroupingActive: (active) => {
    localStorage.setItem('kamp:album-view', String(active))
    set({ albumGroupingActive: active })
  },

  setShuffle: async (shuffle) => {
    await api.setShuffle(shuffle, get().albumGroupingActive)
    void get().loadQueue()
  },

  setRepeat: async (repeat) => {
    await api.setRepeat(repeat)
    void get().loadQueue()
  },

  addAlbumToQueue: async (albumArtist, album, filePath = '') => {
    await api.addAlbumToQueue(albumArtist, album, filePath)
    void get().loadQueue()
  },

  playAlbumNext: async (albumArtist, album, filePath = '') => {
    await api.playAlbumNext(albumArtist, album, filePath)
    void get().loadQueue()
  },

  insertAlbumAt: async (albumArtist, album, index, filePath = '') => {
    await api.insertAlbumAt(albumArtist, album, index, filePath)
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

  reorderQueue: async (order) => {
    await api.reorderQueue(order)
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

  removeFromQueue: async (indices) => {
    await api.removeFromQueue(indices)
    void get().loadQueue()
  },

  setFavorite: async (track, favorite) => {
    try {
      await api.setTrackFavorite(track, favorite)
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('404')) {
        // Track no longer at its expected path — library may have been updated.
        await get().refreshOpenAlbum()
      }
      return
    }
    // Keep the player state in sync if the favorited track is currently playing.
    if (get().player.current_track?.id === track.id) {
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
            tracks: s.queue.tracks.map((t) => (t.id === track.id ? { ...t, favorite } : t))
          }
        : s.queue
    }))
    // Patch search results so the favorite glyph updates without a re-search.
    set((s) => ({
      searchResults: s.searchResults
        ? {
            ...s.searchResults,
            tracks: s.searchResults.tracks.map((t) => (t.id === track.id ? { ...t, favorite } : t))
          }
        : s.searchResults
    }))
    // Patch playlist track list so the heart updates without a reload.
    set((s) => ({
      library: {
        ...s.library,
        playlistTracks: s.library.playlistTracks.map((t) =>
          t.id === track.id ? { ...t, favorite } : t
        )
      }
    }))
    // Reload the open album track list so the heart in track rows updates.
    await get().refreshOpenAlbum()
  },

  setFavorites: async (tracks, favorite) => {
    // allSettled so a partial 404 doesn't silently mis-patch state for succeeded tracks
    const results = await Promise.allSettled(tracks.map((t) => api.setTrackFavorite(t, favorite)))
    const succeededPaths = new Set(
      tracks.filter((_, i) => results[i].status === 'fulfilled').map((t) => t.file_path)
    )
    if (succeededPaths.size === 0) return
    const ids = new Set(tracks.filter((t) => succeededPaths.has(t.file_path)).map((t) => t.id))
    if (get().player.current_track && ids.has(get().player.current_track!.id)) {
      set((s) => ({
        player: {
          ...s.player,
          current_track: s.player.current_track ? { ...s.player.current_track, favorite } : null
        }
      }))
    }
    set((s) => ({
      queue: s.queue
        ? {
            ...s.queue,
            tracks: s.queue.tracks.map((t) => (ids.has(t.id) ? { ...t, favorite } : t))
          }
        : s.queue
    }))
    set((s) => ({
      searchResults: s.searchResults
        ? {
            ...s.searchResults,
            tracks: s.searchResults.tracks.map((t) => (ids.has(t.id) ? { ...t, favorite } : t))
          }
        : s.searchResults
    }))
    set((s) => ({
      library: {
        ...s.library,
        playlistTracks: s.library.playlistTracks.map((t) =>
          ids.has(t.id) ? { ...t, favorite } : t
        )
      }
    }))
    await get().refreshOpenAlbum()
  },

  patchTrackTitle: async (trackId, title, overwrite = false) => {
    const result = await api.patchTrackTags(trackId, title, overwrite)
    if ('collision' in result) return result
    if ('deferred' in result) {
      set((s) => ({ deferredOps: { ...s.deferredOps, [trackId]: result.op_id } }))
      return null
    }
    await get().refreshOpenAlbum()
    void get().loadQueue()
    return null
  },

  patchTrackDisplay: async (trackId, displayTitle) => {
    const updated = await api.patchTrackDisplay(trackId, displayTitle)
    // Update the track in the open track list without reloading the whole library.
    set((s) => ({
      library: {
        ...s.library,
        tracks: s.library.tracks.map((t) => (t.id === trackId ? updated : t))
      }
    }))
    void get().loadQueue()
  },

  clearDeferredOp: (trackId) =>
    set((s) => {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { [trackId]: _removed, ...rest } = s.deferredOps
      return { deferredOps: rest }
    }),

  setAlbumRenameProgress: (progress) => set({ albumRenameProgress: progress }),

  patchAlbumTags: async (albumArtist, album, opts) => {
    let result: Awaited<ReturnType<typeof api.patchAlbumTags>>
    try {
      result = await api.patchAlbumTags(albumArtist, album, opts)
    } catch (err) {
      set({ albumRenameProgress: null })
      throw err
    }
    if ('collision' in result) return result
    if (result.failed.length > 0) {
      console.error('[kamp] album rename: tag write failed for', result.failed)
    }
    if (result.deferred.length > 0) {
      set((s) => {
        const updated = { ...s.deferredOps }
        for (const d of result.deferred) updated[d.track_id] = d.op_id
        return { deferredOps: updated }
      })
    }
    // Clear progress, reload library so sidebar and album card reflect new names.
    set({ albumRenameProgress: null })
    await get().loadLibrary()
    // Refresh the queue so any queued tracks show the updated artist/album name.
    void get().loadQueue()
    // Re-select the open album under its new identity so the track list refreshes.
    const newArtist = opts.album_artist ?? albumArtist
    const newAlbum = opts.album ?? album
    const updatedAlbum = get().library.albums.find(
      (a) => a.album_artist === newArtist && a.album === newAlbum
    )
    if (updatedAlbum) {
      set((s) => ({ library: { ...s.library, selectedAlbum: updatedAlbum } }))
      await get().loadTracks(newArtist, newAlbum)
    }
    return null
  },

  patchAlbumDisplay: async (albumArtist, album, displayAlbum, displayAlbumArtist) => {
    const updated = await api.patchAlbumDisplay(
      albumArtist,
      album,
      displayAlbum,
      displayAlbumArtist
    )
    // Update the album in the library list and selectedAlbum without a full reload.
    set((s) => {
      const albums = s.library.albums.map((a) =>
        a.album_artist === albumArtist && a.album === album ? { ...a, ...updated } : a
      )
      const selectedAlbum =
        s.library.selectedAlbum?.album_artist === albumArtist &&
        s.library.selectedAlbum?.album === album
          ? { ...s.library.selectedAlbum, ...updated }
          : s.library.selectedAlbum
      return { library: { ...s.library, albums, selectedAlbum } }
    })
  },

  patchAlbumMeta: async (albumArtist, album, opts) => {
    const result = await api.patchAlbumMeta(albumArtist, album, opts)
    // Refresh the open track list so genre/label/year values update in the panel.
    const { library } = get()
    if (
      library.selectedAlbum?.album_artist === albumArtist &&
      library.selectedAlbum?.album === album
    ) {
      set((s) => ({
        library: { ...s.library, tracks: result.tracks }
      }))
    }
  },

  setAlbumFavorite: async (albumArtist, album, favorite) => {
    await api.setAlbumFavorite(albumArtist, album, favorite)
    const patchAlbum = (a: api.Album): api.Album =>
      a.album_artist === albumArtist && a.album === album ? { ...a, favorite } : a
    set((s) => ({
      library: {
        ...s.library,
        albums: s.library.albums.map(patchAlbum),
        selectedAlbum:
          s.library.selectedAlbum?.album_artist === albumArtist &&
          s.library.selectedAlbum?.album === album
            ? { ...s.library.selectedAlbum, favorite }
            : s.library.selectedAlbum
      },
      searchResults: s.searchResults
        ? { ...s.searchResults, albums: s.searchResults.albums.map(patchAlbum) }
        : s.searchResults
    }))
  },

  setLibraryPath: async (path) => {
    await api.setLibraryPath(path)
    set({ configuredLibraryPath: path })
  },

  setWatchFolderPath: async (path) => {
    await api.patchConfig('paths.watch_folder', path)
    set((s) => ({
      configValues: s.configValues
        ? { ...s.configValues, 'paths.watch_folder': path }
        : s.configValues
    }))
  },

  loadConfig: async () => {
    try {
      const configValues = await api.getConfig()
      set({
        configValues,
        configuredLibraryPath: (configValues['paths.library'] as string | null) ?? null
      })
    } catch {
      // Best-effort — preferences dialog will show empty fields.
    }
  },

  setConfigValue: async (key, value) => {
    await api.patchConfig(key, value)
    set((s) => ({
      configValues: s.configValues ? { ...s.configValues, [key]: value } : s.configValues
    }))
  },

  openPrefs: (tab) => set({ prefsOpen: true, prefsInitialTab: tab ?? 'general' }),
  closePrefs: () => set({ prefsOpen: false }),
  setUpdateAvailable: (data) => set({ updateAvailable: data }),

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
