/**
 * Zustand store.
 *
 * All player and library state lives here. The renderer is a pure view layer:
 * components read from the store and dispatch actions — they hold no state
 * of their own that belongs in the daemon.
 */

import { create } from 'zustand'
import * as api from './api/client'
import type { RepeatMode } from './api/client'
import type {
  Album,
  AlbumTagsCollision,
  ConfigValues,
  CriteriaDoc,
  DownloadItem,
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
import { themes, applyTheme } from '../../shared/theme'
import type { ThemeName } from '../../shared/theme'
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
  activeView: 'library' | 'now-playing' | 'home' | 'downloads'
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
  // KAMP-544: ephemeral, non-persisted "played this session" echo — album key ->
  // epoch-seconds captured when playback started. Masks the latency between
  // hitting play and the server's last_played_at syncing back into the album list
  // so the new-arrival sparkle vanishes instantly. Not written to localStorage:
  // on restart last_played_at alone governs, and a later content bump (added_at >
  // this timestamp) re-shows the highlight for free.
  playedHighlights: Map<string, number>
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
  sortOrder:
    | 'album_artist'
    | 'album'
    | 'date_added'
    | 'last_played'
    | 'most_played'
    | 'release_date'
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
  // KAMP-436: byte-progress percent (0–100) per downloading album, keyed by
  // sale_item_id. Absent entry → progress unknown → card shows the pulse.
  downloadProgress: Map<string, number>
  // KAMP-562: albums currently in the post-download pipeline (Extracting→Moving),
  // keyed by sale_item_id. Drives the pulsing tag badge until the rescan flips
  // the album to local.
  taggingAlbumIds: Set<string>
  // KAMP-568: full download-queue snapshot (Now Downloading / Queued / Failed) for
  // the Downloads view. Loaded once via GET /api/v1/downloads and kept live by the
  // `download.queue` WS event. Distinct from the per-album KAMP-436 sets above,
  // which decorate the library grid.
  downloadQueue: DownloadItem[]
  flashToast: string | null
  styleRailVisible: boolean
  selectedTheme: ThemeName

  // Actions
  setAudioLevel: (leftDb: number, rightDb: number, crestDb: number, peakDb: number) => void
  setServerStatus: (status: 'connected' | 'reconnecting' | 'disconnected') => void
  toggleQueuePanel: () => void
  toggleArtistPanel: () => void
  loadQueue: () => Promise<void>
  setSearchQuery: (q: string) => void
  setSortOrder: (
    sort: 'album_artist' | 'album' | 'date_added' | 'last_played' | 'most_played' | 'release_date'
  ) => Promise<void>
  setSortDir: (dir: 'asc' | 'desc') => Promise<void>
  setLibraryFilter: (filters: string[]) => void
  setActiveView: (view: 'library' | 'now-playing' | 'home' | 'downloads') => Promise<void>
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
  setAlbumProgress: (saleItemId: string, progress: number) => void
  clearAlbumProgress: (saleItemId: string) => void
  markAlbumTagging: (saleItemId: string) => void
  clearAlbumTagging: (saleItemId: string) => void
  removeDownload: (saleItemId: string) => Promise<void>
  // KAMP-568: download-queue snapshot (Downloads view)
  loadDownloads: () => Promise<void>
  setDownloadQueue: (items: DownloadItem[]) => void
  showFlashToast: (msg: string) => void
  setRecentlyAddedCount: (n: number) => void
  setRecentlyAddedDays: (n: number) => void
  setHighlightEnabled: (enabled: boolean) => void
  setHighlightStyle: (style: string) => void
  markHighlightPlayed: (album: Album) => void
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
  toggleStyleRail: () => void
  setTheme: (name: ThemeName) => void
  setStereoRackTrackSize: (v: TrackDisplaySize) => void
  setStereoRackPlasmaMode: (v: PlasmaMode) => void
  setStereoRackTraceStyle: (v: TraceStyle) => void
  setAlbumEditMode: (mode: boolean) => void
  setAlbumMetaExpanded: (expanded: boolean) => void
  patchAlbumMeta: (
    albumArtist: string,
    album: string,
    opts: { genre?: string; label?: string; release_date?: string; mb_release_id?: string }
  ) => Promise<void>
  loadLibrary: () => Promise<void>
  loadUiState: () => Promise<void>
  selectArtist: (artist: string | null) => void
  selectAlbum: (album: Album | null) => Promise<void>
  loadTracks: (albumArtist: string, album: string, trackId?: number | null) => Promise<void>
  setCollectionType: (type: 'albums' | 'playlists') => void
  playPlaylist: (playlistId: number, startIndex?: number) => Promise<void>
  playFiles: (trackIds: number[], startIndex?: number) => Promise<void>
  recordPlaylistPlayed: (playlistId: number) => Promise<void>
  loadPlaylists: () => Promise<void>
  createPlaylist: (title: string) => Promise<Playlist>
  createMagicPlaylist: (title: string, criteria: CriteriaDoc) => Promise<Playlist>
  updateMagicPlaylistCriteria: (id: number, criteria: CriteriaDoc) => Promise<Playlist>
  selectPlaylist: (playlist: Playlist | null) => Promise<void>
  loadPlaylistTracks: (playlistId: number) => Promise<void>
  addTrackToPlaylist: (playlistId: number, trackId: number) => Promise<void>
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
    trackId?: number | null
  ) => Promise<void>
  playTrack: (
    albumArtist: string,
    album: string,
    trackIndex: number,
    trackId?: number | null
  ) => Promise<void>
  togglePlayPause: () => Promise<void>
  stop: () => Promise<void>
  next: () => Promise<void>
  prev: () => Promise<void>
  seek: (position: number) => Promise<void>
  setVolume: (volume: number) => Promise<void>
  setAlbumGroupingActive: (active: boolean) => void
  setShuffle: (shuffle: boolean) => Promise<void>
  setRepeat: () => Promise<void>
  addAlbumToQueue: (albumArtist: string, album: string, trackId?: number | null) => Promise<void>
  playAlbumNext: (albumArtist: string, album: string, trackId?: number | null) => Promise<void>
  insertAlbumAt: (
    albumArtist: string,
    album: string,
    index: number,
    trackId?: number | null
  ) => Promise<void>
  addToQueue: (ref: api.TrackRef) => Promise<void>
  insertIntoQueue: (ref: api.TrackRef, index: number) => Promise<void>
  playNext: (ref: api.TrackRef) => Promise<void>
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

// Cache key for an album's track list. A missing-album card (track_id set) keys
// on its unique track id so two no-album tracks with the same title don't collide;
// a real album keys on (album_artist, album). (KAMP-554)
const _albumTracksKey = (albumArtist: string, album: string, trackId: number | null): string =>
  trackId != null ? `id:${trackId}` : `${albumArtist}\0${album}`

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
  playedHighlights: new Map<string, number>(),
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
  baseKampEditMode: false,
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
  downloadProgress: new Map<string, number>(),
  taggingAlbumIds: new Set<string>(),
  downloadQueue: [],
  flashToast: null,
  styleRailVisible: false,
  selectedTheme: (localStorage.getItem('kamp:selected-theme') as ThemeName | null) ?? 'kamp',
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
    const naturalDir: 'asc' | 'desc' = [
      'date_added',
      'last_played',
      'most_played',
      'release_date'
    ].includes(sort)
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
      // KAMP-436: intentionally do NOT drop downloadProgress here. The reveal
      // must stay at 100% through the post-download tag/rescan window (while the
      // card is still blurred) so it doesn't flash back to full blur. The card
      // clears the entry itself via clearAlbumProgress once its blur resolves.
      return { downloadingAlbumIds: next }
    }),
  setAlbumProgress: (saleItemId, progress) =>
    set((s) => {
      const next = new Map(s.downloadProgress)
      next.set(saleItemId, progress)
      return { downloadProgress: next }
    }),
  clearAlbumProgress: (saleItemId) =>
    set((s) => {
      // No-op (return the same state ref) when absent, so repeated calls from a
      // card effect don't churn a new Map / re-render.
      if (!s.downloadProgress.has(saleItemId)) return s
      const next = new Map(s.downloadProgress)
      next.delete(saleItemId)
      return { downloadProgress: next }
    }),
  markAlbumQueued: (saleItemId) =>
    set((s) => ({ queuedAlbumIds: new Set([...s.queuedAlbumIds, saleItemId]) })),
  clearAlbumQueued: (saleItemId) =>
    set((s) => {
      const next = new Set(s.queuedAlbumIds)
      next.delete(saleItemId)
      return { queuedAlbumIds: next }
    }),
  markAlbumTagging: (saleItemId) =>
    set((s) => ({ taggingAlbumIds: new Set([...s.taggingAlbumIds, saleItemId]) })),
  clearAlbumTagging: (saleItemId) =>
    set((s) => {
      // No-op (same state ref) when absent so the card's !isRemote cleanup effect
      // doesn't churn a new Set / re-render (KAMP-562).
      if (!s.taggingAlbumIds.has(saleItemId)) return s
      const next = new Set(s.taggingAlbumIds)
      next.delete(saleItemId)
      return { taggingAlbumIds: next }
    }),
  removeDownload: async (saleItemId) => {
    try {
      await api.removeDownload(saleItemId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not remove download'
      get().showFlashToast(msg)
    }
  },
  // KAMP-568: seed the Downloads view from the REST snapshot; live updates then
  // arrive via the `download.queue` WS event (setDownloadQueue).
  loadDownloads: async () => {
    try {
      const { items } = await api.getDownloads()
      set({ downloadQueue: items })
    } catch {
      // best-effort; the WS snapshot will populate on the next transition
    }
  },
  setDownloadQueue: (items) => set({ downloadQueue: items }),
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

  markHighlightPlayed: (album) => {
    // KAMP-544: record the moment this album started playing so its new-arrival
    // sparkle clears immediately (before last_played_at round-trips from the
    // server). Ephemeral — never persisted. A later sync that adds a track bumps
    // added_at past this timestamp, which re-shows the highlight.
    const key = album.missing_album
      ? String(album.track_id ?? '')
      : `${album.album_artist}::${album.album}`
    const nowSecs = Date.now() / 1000
    const current = get().playedHighlights
    if (current.get(key) === nowSecs) return
    const next = new Map(current)
    next.set(key, nowSecs)
    set({ playedHighlights: next })
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
      await api.insertAlbumAt(album.album_artist, album.album, idx + offset, album.track_id)
      offset += album.track_count
    }
    void get().loadQueue()
  },

  setFlashTrackId: (id) => {
    set({ flashTrackId: id })
    setTimeout(() => set({ flashTrackId: null }), 2100)
  },

  toggleBaseKampEditMode: () => {
    set({ baseKampEditMode: !get().baseKampEditMode })
  },

  toggleStyleRail: () => {
    set({ styleRailVisible: !get().styleRailVisible })
  },

  setTheme: (name) => {
    localStorage.setItem('kamp:selected-theme', name)
    applyTheme(name, document.documentElement)
    window.api.setBgColor(themes[name].bg)
    set({ selectedTheme: name })
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
                a.track_id === selectedAlbum.track_id
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
        selectedAlbum.track_id
      )
      const key = _albumTracksKey(
        selectedAlbum.album_artist,
        selectedAlbum.album,
        selectedAlbum.track_id
      )
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
    if (album) await get().loadTracks(album.album_artist, album.album, album.track_id)
  },

  loadTracks: async (albumArtist, album, trackId = null) => {
    // For a missing-album card, track_id is the unique key so that two no-album
    // tracks with the same title don't share a cache entry.
    const key = _albumTracksKey(albumArtist, album, trackId)
    if (get().library.tracksAlbumKey === key) return
    const tracks = await api.getTracksForAlbum(albumArtist, album, trackId)
    set((s) => ({ library: { ...s.library, tracks, tracksAlbumKey: key } }))
  },

  setCollectionType: (type) => set((s) => ({ library: { ...s.library, collectionType: type } })),

  playPlaylist: async (playlistId, startIndex = 0) => {
    await api.playPlaylist(playlistId, startIndex)
    void get().loadQueue()
    void get().loadPlaylists()
  },

  playFiles: async (trackIds, startIndex = 0) => {
    await api.playFiles(trackIds, startIndex)
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

  addTrackToPlaylist: async (playlistId, trackId) => {
    await api.addTrackToPlaylist(playlistId, trackId)
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

  playAlbum: async (albumArtist, album, trackIndex = 0, trackId = null) => {
    await api.playAlbum(albumArtist, album, trackIndex, trackId)
    void get().loadQueue()
  },

  playTrack: async (albumArtist, album, trackIndex, trackId = null) => {
    await api.playAlbum(albumArtist, album, trackIndex, trackId)
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

  setRepeat: async () => {
    const { queue, albumGroupingActive } = get()
    const current = (queue?.repeat ?? 'off') as RepeatMode
    const modes: RepeatMode[] = albumGroupingActive
      ? ['off', 'queue', 'album', 'single']
      : ['off', 'queue', 'single']
    const idx = modes.indexOf(current)
    const nextMode = modes[(idx === -1 ? 0 : idx + 1) % modes.length]
    await api.setRepeat(nextMode)
    void get().loadQueue()
  },

  addAlbumToQueue: async (albumArtist, album, trackId = null) => {
    await api.addAlbumToQueue(albumArtist, album, trackId)
    void get().loadQueue()
  },

  playAlbumNext: async (albumArtist, album, trackId = null) => {
    await api.playAlbumNext(albumArtist, album, trackId)
    void get().loadQueue()
  },

  insertAlbumAt: async (albumArtist, album, index, trackId = null) => {
    await api.insertAlbumAt(albumArtist, album, index, trackId)
    void get().loadQueue()
  },

  addToQueue: async (ref) => {
    await api.addToQueue(ref)
    void get().loadQueue()
  },

  insertIntoQueue: async (ref, index) => {
    await api.insertIntoQueue(ref, index)
    void get().loadQueue()
  },

  playNext: async (ref) => {
    await api.playNext(ref)
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
    // Patch the open album track list and the playlist track list in place, keyed
    // on the canonical id — which never diverges between the streaming and
    // downloaded views (KAMP-538 fixes KAMP-532). A favorite never adds or removes
    // a row, so the old refreshOpenAlbum() refetch was both unnecessary and racy:
    // for a freshly-downloaded track it could read back a not-yet-committed value
    // and clobber this optimistic patch (the very reload race KAMP-532 describes).
    set((s) => ({
      library: {
        ...s.library,
        tracks: s.library.tracks.map((t) => (t.id === track.id ? { ...t, favorite } : t)),
        playlistTracks: s.library.playlistTracks.map((t) =>
          t.id === track.id ? { ...t, favorite } : t
        )
      }
    }))
  },

  setFavorites: async (tracks, favorite) => {
    // allSettled so a partial 404 doesn't silently mis-patch state for succeeded tracks
    const results = await Promise.allSettled(tracks.map((t) => api.setTrackFavorite(t, favorite)))
    // KAMP-538: key the patch set directly on the canonical id of each succeeded track.
    const ids = new Set(tracks.filter((_, i) => results[i].status === 'fulfilled').map((t) => t.id))
    if (ids.size === 0) return
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
        tracks: s.library.tracks.map((t) => (ids.has(t.id) ? { ...t, favorite } : t)),
        playlistTracks: s.library.playlistTracks.map((t) =>
          ids.has(t.id) ? { ...t, favorite } : t
        )
      }
    }))
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
