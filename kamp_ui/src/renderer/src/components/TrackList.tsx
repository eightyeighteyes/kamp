import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { artUrl, fetchMusicBrainzCandidates, patchTrackMeta } from '../api/client'
import type {
  AlbumTagsCollision,
  MusicBrainzRelease,
  Track,
  TrackTagsCollision
} from '../api/client'
import { TrackContextMenu } from './TrackContextMenu'
import { EditableTrackTitle } from './EditableTrackTitle'
import { EditableAlbumField } from './EditableAlbumField'
import { CollisionModal } from './CollisionModal'
import { AlbumMetaPanel } from './AlbumMetaPanel'
import { MusicBrainzModal } from './MusicBrainzModal'
import type { MBApplyPayload } from './MusicBrainzModal'
import { AlbumArtModal } from './AlbumArtModal'
import {
  FavoriteIcon,
  PencilIcon,
  PlayIcon,
  PauseIcon,
  QueueAddIcon,
  PlayNextIcon
} from './TransportIcons'

const TOAST_TTL = 10_000 // ms

type ContextMenu = { x: number; y: number; track: Track }
type AlbumRenameToast = {
  message: string
  undo: () => Promise<AlbumTagsCollision | null>
}

type MBFetchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; candidates: MusicBrainzRelease[] }
  | { status: 'error'; message: string }

function HeroImage({ src }: { src: string }): React.JSX.Element {
  const [loaded, setLoaded] = useState(false)
  return (
    <img
      className={`track-list-hero-img${loaded ? ' loaded' : ''}`}
      src={src}
      alt=""
      draggable={false}
      onLoad={() => setLoaded(true)}
      onError={() => setLoaded(false)}
    />
  )
}

export function TrackList(): React.JSX.Element | null {
  const album = useStore((s) => s.library.selectedAlbum)
  const tracks = useStore((s) => s.library.tracks)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const playTrack = useStore((s) => s.playTrack)
  const togglePlayPause = useStore((s) => s.togglePlayPause)
  const setAlbumFavorite = useStore((s) => s.setAlbumFavorite)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const playAlbumNext = useStore((s) => s.playAlbumNext)

  const albumEditMode = useStore((s) => s.albumEditMode)
  const setAlbumEditMode = useStore((s) => s.setAlbumEditMode)
  const albumMetaExpanded = useStore((s) => s.albumMetaExpanded)
  const setAlbumMetaExpanded = useStore((s) => s.setAlbumMetaExpanded)
  const patchAlbumMeta = useStore((s) => s.patchAlbumMeta)
  const patchTrackTitle = useStore((s) => s.patchTrackTitle)
  const patchAlbumTags = useStore((s) => s.patchAlbumTags)
  const refreshOpenAlbum = useStore((s) => s.refreshOpenAlbum)
  const patchOpenAlbum = useStore((s) => s.patchOpenAlbum)
  const albumRenameProgress = useStore((s) => s.albumRenameProgress)
  const deferredOps = useStore((s) => s.deferredOps)

  const albumTitleRef = useRef<HTMLHeadingElement>(null)
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mbAbortRef = useRef<AbortController | null>(null)
  const [collision, setCollision] = useState<
    (TrackTagsCollision & { pendingTrackId: number; pendingTitle: string }) | null
  >(null)
  const [albumCollision, setAlbumCollision] = useState<
    (AlbumTagsCollision & { pendingOpts: Parameters<typeof patchAlbumTags>[2] }) | null
  >(null)
  const [albumRenameToast, setAlbumRenameToast] = useState<AlbumRenameToast | null>(null)
  const [mbState, setMbState] = useState<MBFetchState>({ status: 'idle' })
  const [artSearchOpen, setArtSearchOpen] = useState(false)
  const tooltip = useTooltip()

  // Reset MB state when navigating to a different album (derived state from props).
  // Using the render-time pattern avoids a setState-in-effect lint violation.
  const albumKey = album ? `${album.album_artist}\0${album.album}` : null
  const [prevAlbumKey, setPrevAlbumKey] = useState<string | null>(albumKey)
  if (albumKey !== prevAlbumKey) {
    setPrevAlbumKey(albumKey)
    setMbState({ status: 'idle' })
    setArtSearchOpen(false)
  }

  const showRenameToast = (toast: AlbumRenameToast): void => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
    setAlbumRenameToast(toast)
    toastTimerRef.current = setTimeout(() => setAlbumRenameToast(null), TOAST_TTL)
  }

  // Focus the album title heading when entering edit mode so screen readers
  // receive the live-region announcement in context.
  useEffect(() => {
    if (albumEditMode) albumTitleRef.current?.focus()
  }, [albumEditMode])

  // Abort any in-flight MB request when the album changes or on unmount.
  useEffect(() => {
    return () => {
      mbAbortRef.current?.abort()
      mbAbortRef.current = null
    }
  }, [albumKey])

  const handleFetchMB = (): void => {
    if (!album) return
    mbAbortRef.current?.abort()
    const ctrl = new AbortController()
    mbAbortRef.current = ctrl
    setMbState({ status: 'loading' })
    fetchMusicBrainzCandidates(album.album_artist, album.album, ctrl.signal).then(
      (candidates) => {
        if (!ctrl.signal.aborted) setMbState({ status: 'ready', candidates })
      },
      (err: unknown) => {
        if (ctrl.signal.aborted) return
        const msg = err instanceof Error ? err.message : 'MusicBrainz lookup failed'
        setMbState({ status: 'error', message: msg })
        // Auto-reset error display after 4 s so the pill returns to idle.
        setTimeout(() => setMbState({ status: 'idle' }), 4_000)
      }
    )
  }

  const handleMBApply = async (payload: MBApplyPayload): Promise<void> => {
    if (!album) return
    setMbState({ status: 'idle' })

    const mbRelease = payload.release

    // 1. Album meta (year, label, mb_release_id)
    const metaOpts: { year?: string; label?: string; mb_release_id?: string } = {
      mb_release_id: mbRelease.mbid
    }
    if (payload.album.year === 'mb' && mbRelease.year) metaOpts.year = mbRelease.year
    if (payload.album.label === 'mb' && mbRelease.label) metaOpts.label = mbRelease.label
    await patchAlbumMeta(album.album_artist, album.album, metaOpts)

    // 2. Album tags (title, album_artist)
    const tagOpts: { album?: string; album_artist?: string } = {}
    if (payload.album.title === 'mb') tagOpts.album = mbRelease.title
    if (payload.album.album_artist === 'mb') tagOpts.album_artist = mbRelease.album_artist
    if (tagOpts.album || tagOpts.album_artist) {
      await patchAlbumTags(album.album_artist, album.album, tagOpts)
    }

    // 3. Track titles (sequential) + recording MBIDs (parallel)
    const mbTrackMap = new Map(
      mbRelease.tracks.map((t) => [`${t.disc_number}-${t.track_number}`, t])
    )
    const mbidPromises: Promise<unknown>[] = []
    for (const local of tracks) {
      const key = `${local.disc_number}-${local.track_number}`
      const mbTrack = mbTrackMap.get(key)
      if (!mbTrack) continue
      if (payload.tracks[key] === 'mb' && local.title !== mbTrack.title) {
        await patchTrackTitle(local.id, mbTrack.title)
      }
      if (mbTrack.recording_mbid && local.mb_recording_id !== mbTrack.recording_mbid) {
        mbidPromises.push(patchTrackMeta(local.id, mbTrack.recording_mbid))
      }
    }
    if (mbidPromises.length > 0) await Promise.allSettled(mbidPromises)

    await refreshOpenAlbum()
  }

  const [menu, setMenu] = useState<ContextMenu | null>(null)

  if (!album) return null

  const isCurrentAlbum =
    currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  return (
    <div className={`track-list-view${albumEditMode ? ' track-list-view--edit' : ''}`}>
      {/* Hero: full-width art — image intentionally taller than hero to bleed into track list */}
      <div className={`track-list-hero${album.has_art ? ' has-art' : ''}`}>
        {album.has_art && (
          <HeroImage
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
          />
        )}
      </div>
      {/* Overlay spans the full view so the gradient covers both hero and the top of the track list */}
      <div className="track-list-hero-overlay" />

      {/* Breadcrumb floats over the hero */}
      <nav className="breadcrumb" aria-label="Navigation">
        <button
          onClick={() => {
            selectAlbum(null)
            selectArtist(null)
          }}
        >
          Library
        </button>
        <span className="breadcrumb-sep" aria-hidden="true">
          ›
        </span>
        <button
          onClick={() => {
            selectAlbum(null)
            selectArtist(album.album_artist)
          }}
        >
          {album.album_artist}
        </button>
        <span className="breadcrumb-sep" aria-hidden="true">
          ›
        </span>
        <span>{album.album}</span>
      </nav>

      {/* Edit toggle — separate pill, right side of the hero row */}
      <button
        className={`breadcrumb-edit-btn${albumEditMode ? ' active' : ''}`}
        aria-pressed={albumEditMode}
        onClick={() => setAlbumEditMode(!albumEditMode)}
      >
        <PencilIcon size={11} />
        {albumEditMode ? 'Done' : 'Edit tags'}
      </button>

      {/* MusicBrainz fetch pill — only visible in edit mode, stacked below Edit/Done */}
      {albumEditMode && (
        <button
          className={`breadcrumb-edit-btn mb-pill${mbState.status === 'loading' ? ' mb-pill--loading' : mbState.status === 'error' ? ' mb-pill--error' : ''}`}
          disabled={mbState.status === 'loading'}
          {...tooltip(mbState.status === 'error' ? mbState.message : TOOLTIPS.LIBRARY_FETCH_MB)}
          onClick={handleFetchMB}
          type="button"
        >
          {mbState.status === 'loading' ? (
            <>
              Searching
              <span className="mb-pill__dots" aria-hidden="true">
                <span>.</span>
                <span>.</span>
                <span>.</span>
              </span>
            </>
          ) : mbState.status === 'error' ? (
            'No match'
          ) : (
            'MusicBrainz'
          )}
          {mbState.status === 'loading' && (
            <span
              className="mb-pill__progress"
              aria-hidden="true"
              style={{ animationDuration: `${tracks.length * 1.5}s` }}
            />
          )}
        </button>
      )}

      {/* iTunes album art fetch pill — only visible in edit mode */}
      {albumEditMode && (
        <button
          className="breadcrumb-edit-btn mb-pill mb-pill--second"
          {...tooltip(TOOLTIPS.LIBRARY_FETCH_ART)}
          onClick={() => setArtSearchOpen(true)}
          type="button"
        >
          Fetch Album Art
        </button>
      )}

      {/* Screen-reader announcement for edit-mode transitions */}
      <div aria-live="polite" className="sr-only">
        {albumEditMode ? 'Edit mode on. Album, artist, and track titles are editable.' : ''}
      </div>

      {/* Static identity block — does not scroll */}
      <div className="track-list-identity">
        <div className="track-list-identity-text">
          <button
            className={`track-list-album-fav-btn favorite-btn${album.favorite ? ' active' : ''}`}
            {...tooltip(
              album.favorite ? TOOLTIPS.ALBUM_FAVORITE_REMOVE : TOOLTIPS.ALBUM_FAVORITE_ADD
            )}
            aria-label={album.favorite ? 'Remove from favorites' : 'Add to favorites'}
            aria-pressed={album.favorite}
            onClick={() => setAlbumFavorite(album.album_artist, album.album, !album.favorite)}
          >
            <FavoriteIcon active={album.favorite} size={36} />
          </button>
          <EditableAlbumField
            value={album.album}
            editMode={albumEditMode}
            disabled={albumRenameProgress !== null}
            className="track-list-album-title"
            onSave={async (newAlbum) => {
              const oldAlbum = album.album
              const oldArtist = album.album_artist
              const count = album.track_count
              const result = await patchAlbumTags(oldArtist, oldAlbum, { album: newAlbum })
              if (result?.collision) {
                setAlbumCollision({ ...result, pendingOpts: { album: newAlbum } })
              } else {
                showRenameToast({
                  message: `${count} ${count === 1 ? 'file' : 'files'} reorganized`,
                  undo: () => patchAlbumTags(oldArtist, newAlbum, { album: oldAlbum })
                })
              }
            }}
            renderStatic={(val) => (
              <h1 ref={albumTitleRef} className="track-list-album-title" tabIndex={-1}>
                {val}
              </h1>
            )}
          />
          <EditableAlbumField
            value={album.album_artist}
            editMode={albumEditMode}
            disabled={albumRenameProgress !== null}
            className="track-list-album-artist-input"
            onSave={async (newArtist) => {
              const oldArtist = album.album_artist
              const oldAlbum = album.album
              const count = album.track_count
              const result = await patchAlbumTags(oldArtist, oldAlbum, { album_artist: newArtist })
              if (result?.collision) {
                setAlbumCollision({ ...result, pendingOpts: { album_artist: newArtist } })
              } else {
                showRenameToast({
                  message: `${count} ${count === 1 ? 'file' : 'files'} reorganized`,
                  undo: () => patchAlbumTags(newArtist, oldAlbum, { album_artist: oldArtist })
                })
              }
            }}
            renderStatic={(val) => (
              <h2 className="track-list-album-artist">
                <button
                  className="track-list-artist-link"
                  onClick={() => {
                    selectAlbum(null)
                    selectArtist(album.album_artist)
                  }}
                >
                  {val}
                </button>
              </h2>
            )}
          />
          {album.year && <div className="track-list-album-year">{album.year}</div>}
          {albumRenameProgress && (
            <div className="album-rename-progress" aria-live="polite">
              Renaming {albumRenameProgress.done} of {albumRenameProgress.total}…
            </div>
          )}
        </div>
        <div className="album-controls">
          <button
            className="album-secondary-btn"
            {...tooltip(TOOLTIPS.LIBRARY_ADD_TO_QUEUE)}
            aria-label="Add album to queue"
            onClick={() => void addAlbumToQueue(album.album_artist, album.album, album.file_path)}
          >
            <QueueAddIcon size={16} />
          </button>
          <button
            className="album-secondary-btn"
            {...tooltip(TOOLTIPS.LIBRARY_PLAY_NEXT)}
            aria-label="Play album next"
            onClick={() => void playAlbumNext(album.album_artist, album.album, album.file_path)}
          >
            <PlayNextIcon size={16} />
          </button>
          <button
            className="play-all-btn"
            aria-label={isCurrentAlbum && playing ? 'Pause' : 'Play all'}
            onClick={() =>
              isCurrentAlbum
                ? togglePlayPause()
                : playTrack(album.album_artist, album.album, 0, album.file_path)
            }
          >
            {isCurrentAlbum && playing ? <PauseIcon size={18} /> : <PlayIcon size={18} />}
          </button>
        </div>
      </div>

      <div className="track-list-divider" />

      <AlbumMetaPanel
        tracks={tracks}
        editMode={albumEditMode}
        expanded={albumMetaExpanded}
        onToggle={() => setAlbumMetaExpanded(!albumMetaExpanded)}
        onSave={(opts) => patchAlbumMeta(album.album_artist, album.album, opts)}
      />

      {/* Scrollable body */}
      <div className="track-list-body">
        <ol className="track-rows">
          {tracks.map((track, i) => {
            const isCurrent = currentTrack?.id === track.id
            return (
              <li
                key={track.id}
                className={`track-row${isCurrent ? ' current' : ''}`}
                tabIndex={0}
                onDoubleClick={() => {
                  if (isCurrent) {
                    togglePlayPause()
                  } else {
                    playTrack(album.album_artist, album.album, i, album.file_path)
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key !== 'Enter') return
                  if (isCurrent) togglePlayPause()
                  else playTrack(album.album_artist, album.album, i, album.file_path)
                }}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('text/kamp-track-path', track.file_path)
                  e.dataTransfer.effectAllowed = 'copy'
                }}
                onContextMenu={(e) => {
                  e.preventDefault()
                  setMenu({ x: e.clientX, y: e.clientY, track })
                }}
              >
                <span className="track-row-fav">
                  {track.favorite && <FavoriteIcon active size={10} />}
                </span>
                <span className="track-row-num">
                  {isCurrent ? (
                    playing ? (
                      <PlayIcon size={11} />
                    ) : (
                      <PauseIcon size={11} />
                    )
                  ) : (
                    track.track_number
                  )}
                </span>
                <EditableTrackTitle
                  trackId={track.id}
                  title={track.title}
                  editMode={albumEditMode}
                  deferred={track.id in deferredOps}
                  onSave={async (trackId, newTitle) => {
                    const result = await patchTrackTitle(trackId, newTitle)
                    if (result?.collision) {
                      setCollision({ ...result, pendingTrackId: trackId, pendingTitle: newTitle })
                    }
                  }}
                />
                <span className="track-row-artist">{track.artist}</span>
              </li>
            )
          })}
        </ol>
      </div>

      {menu && (
        <TrackContextMenu x={menu.x} y={menu.y} track={menu.track} onClose={() => setMenu(null)} />
      )}
      {mbState.status === 'ready' && (
        <MusicBrainzModal
          candidates={mbState.candidates}
          localTracks={tracks}
          onApply={(payload) => void handleMBApply(payload)}
          onClose={() => setMbState({ status: 'idle' })}
        />
      )}
      {artSearchOpen && (
        <AlbumArtModal
          albumArtist={album.album_artist}
          album={album.album}
          hasExistingArt={album.has_art}
          onClose={() => setArtSearchOpen(false)}
          onApplied={(updatedAlbum) => {
            setArtSearchOpen(false)
            patchOpenAlbum(updatedAlbum)
          }}
        />
      )}
      {collision && (
        <CollisionModal
          targetPath={collision.target_path}
          onOverwrite={() => {
            const { pendingTrackId, pendingTitle } = collision
            setCollision(null)
            void patchTrackTitle(pendingTrackId, pendingTitle, true)
          }}
          onSkip={() => setCollision(null)}
          onCancel={() => setCollision(null)}
        />
      )}
      {albumCollision && (
        <CollisionModal
          targetPath={albumCollision.first_path}
          onOverwrite={() => {
            const opts = albumCollision.pendingOpts
            setAlbumCollision(null)
            void patchAlbumTags(album.album_artist, album.album, { ...opts, overwrite: true })
          }}
          onSkip={() => {
            const opts = albumCollision.pendingOpts
            setAlbumCollision(null)
            void patchAlbumTags(album.album_artist, album.album, {
              ...opts,
              skip_conflicts: true
            })
          }}
          onCancel={() => setAlbumCollision(null)}
        />
      )}
      {albumRenameToast && (
        <div className="album-rename-toast" role="status">
          <span className="album-rename-toast-text">{albumRenameToast.message}</span>
          <button
            className="album-rename-toast-undo"
            onClick={() => {
              setAlbumRenameToast(null)
              if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
              void albumRenameToast.undo()
            }}
          >
            Undo
          </button>
          <div className="album-rename-toast-bar" style={{ animationDuration: `${TOAST_TTL}ms` }} />
        </div>
      )}
    </div>
  )
}
