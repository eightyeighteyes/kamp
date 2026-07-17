import React, { useEffect, useMemo, useRef, useState } from 'react'
import { fetchMusicBrainzRelease } from '../api/client'
import type {
  MusicBrainzCandidate,
  MusicBrainzRelease,
  MusicBrainzTrack,
  Track
} from '../api/client'

// Fields that can be toggled between local and MB values.
type FieldId = 'title' | 'album_artist' | 'release_date' | 'label'

// Per-track selection key: "disc-track"
type TrackKey = string

type SelectionState = {
  album: Record<FieldId, 'local' | 'mb'>
  tracks: Record<TrackKey, 'local' | 'mb'>
  // Per-track artist choice, keyed like `tracks` (KAMP-583).
  trackArtists: Record<TrackKey, 'local' | 'mb'>
}

// One entry per local track the modal could resolve to an MB track. Resolution
// happens HERE, once, and the resolved MB track travels with the choice —
// previously the modal resolved with findMBTrack's disc normalisation but the
// apply path re-derived its own key from raw MB coords, so on a disc-shifted
// album the modal showed a toggle and Apply silently skipped the track.
export type MBTrackApply = {
  localId: number
  mb: MusicBrainzTrack
  title: 'local' | 'mb'
  artist: 'local' | 'mb'
}

export type MBApplyPayload = {
  album: Record<FieldId, 'local' | 'mb'>
  tracks: MBTrackApply[]
  // Full release only: applying a shallow candidate would stamp a new
  // mb_release_id while leaving every track's recording id stale (KAMP-584).
  release: MusicBrainzRelease
}

type Props = {
  candidates: MusicBrainzCandidate[]
  localTracks: Track[]
  onApply: (payload: MBApplyPayload) => void
  onClose: () => void
}

function trackKey(disc: number, num: number): TrackKey {
  return `${disc}-${num}`
}

// Look up the MB track that matches a local track, with disc-normalisation
// fallback (disc=0 vs disc=1 mismatch is common).
function findMBTrack(
  release: MusicBrainzRelease,
  local: Track
): (typeof release.tracks)[number] | undefined {
  const byKey = (d: number, n: number): (typeof release.tracks)[number] | undefined =>
    release.tracks.find((t) => t.disc_number === d && t.track_number === n)
  return (
    byKey(local.disc_number, local.track_number) ??
    byKey(local.disc_number + 1, local.track_number) ??
    byKey(local.disc_number - 1, local.track_number)
  )
}

// Album-level defaults come from the shallow candidate, so they're available
// before hydration and survive the shallow → hydrated swap untouched.
function defaultAlbumSelection(
  candidate: MusicBrainzCandidate,
  localTracks: Track[]
): Record<FieldId, 'local' | 'mb'> {
  return {
    title: candidate.title !== localTracks[0]?.album ? 'mb' : 'local',
    album_artist: candidate.album_artist !== localTracks[0]?.album_artist ? 'mb' : 'local',
    release_date: candidate.release_date !== localTracks[0]?.release_date ? 'mb' : 'local',
    label: candidate.label !== localTracks[0]?.label ? 'mb' : 'local'
  }
}

function defaultTrackSelection(
  release: MusicBrainzRelease,
  localTracks: Track[]
): Record<TrackKey, 'local' | 'mb'> {
  const tracks: Record<TrackKey, 'local' | 'mb'> = {}
  for (const local of localTracks) {
    const mb = findMBTrack(release, local)
    if (!mb) continue
    const key = trackKey(local.disc_number, local.track_number)
    tracks[key] = mb.title !== local.title ? 'mb' : 'local'
  }
  return tracks
}

// An artist row only exists where MB and local disagree, so its default is
// always 'mb' — there is nothing to choose on rows that already agree.
function defaultTrackArtistSelection(
  release: MusicBrainzRelease,
  localTracks: Track[]
): Record<TrackKey, 'local' | 'mb'> {
  const artists: Record<TrackKey, 'local' | 'mb'> = {}
  for (const local of localTracks) {
    const mb = findMBTrack(release, local)
    if (!mb || !artistDiffers(mb, local)) continue
    artists[trackKey(local.disc_number, local.track_number)] = 'mb'
  }
  return artists
}

// MB must actually have a credit, and it must differ from what we hold.
function artistDiffers(mb: MusicBrainzTrack, local: Track): boolean {
  return !!mb.artist && mb.artist !== local.artist
}

function Toggle({
  side,
  onChange
}: {
  side: 'local' | 'mb'
  onChange: (v: 'local' | 'mb') => void
}): React.JSX.Element {
  return (
    <div className="mb-toggle" role="group" aria-label="Choose value">
      <button
        className={`mb-toggle__btn${side === 'local' ? ' mb-toggle__btn--active' : ''}`}
        onClick={() => onChange('local')}
        type="button"
      >
        Local
      </button>
      <button
        className={`mb-toggle__btn${side === 'mb' ? ' mb-toggle__btn--active' : ''}`}
        onClick={() => onChange('mb')}
        type="button"
      >
        MB
      </button>
    </div>
  )
}

const FIELD_LABELS: Record<FieldId, string> = {
  title: 'Album',
  album_artist: 'Artist',
  release_date: 'Release Date',
  label: 'Label'
}

export function MusicBrainzModal({
  candidates,
  localTracks,
  onApply,
  onClose
}: Props): React.JSX.Element {
  const localAlbum = localTracks[0]

  const [candidateIndex, setCandidateIndex] = useState(0)
  const candidate = candidates[candidateIndex]

  // Lazy hydration cache, keyed by the candidate's mbid (not index): rapid
  // navigation can resolve out of order, and a cached entry must land on the
  // candidate that requested it. A merged mbid hydrates to a release whose
  // own id differs — Apply uses the hydrated (canonical) release.
  const [hydrated, setHydrated] = useState<Record<string, MusicBrainzRelease>>({})
  const [hydrationError, setHydrationError] = useState<Record<string, string>>({})
  const hydrateAbortRef = useRef<AbortController | null>(null)

  const release: MusicBrainzRelease | undefined = hydrated[candidate.mbid]

  useEffect(() => {
    const mbid = candidate.mbid
    if (hydrated[mbid] || hydrationError[mbid]) return
    // Supersede any in-flight hydration — one request at a time.
    hydrateAbortRef.current?.abort()
    const ctrl = new AbortController()
    hydrateAbortRef.current = ctrl
    fetchMusicBrainzRelease(mbid, ctrl.signal).then(
      (r) => {
        if (!ctrl.signal.aborted) setHydrated((prev) => ({ ...prev, [mbid]: r }))
      },
      (err: unknown) => {
        if (ctrl.signal.aborted) return
        const msg = err instanceof Error ? err.message : 'Failed to load release'
        setHydrationError((prev) => ({ ...prev, [mbid]: msg }))
      }
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidate.mbid])

  // Abort in-flight hydration when the modal unmounts (close).
  useEffect(() => {
    return () => {
      hydrateAbortRef.current?.abort()
      hydrateAbortRef.current = null
    }
  }, [])

  const [sel, setSel] = useState<SelectionState>(() => ({
    album: defaultAlbumSelection(candidate, localTracks),
    tracks: release ? defaultTrackSelection(release, localTracks) : {},
    trackArtists: release ? defaultTrackArtistSelection(release, localTracks) : {}
  }))

  // Reset selection when the active candidate changes (render-time derived
  // state pattern), keyed by mbid so the shallow → hydrated swap of the same
  // candidate does NOT wipe the user's album-field toggles.
  const [prevMbid, setPrevMbid] = useState(candidate.mbid)
  const [trackDefaultsFor, setTrackDefaultsFor] = useState<string | null>(
    release ? candidate.mbid : null
  )
  if (candidate.mbid !== prevMbid) {
    setPrevMbid(candidate.mbid)
    setSel({
      album: defaultAlbumSelection(candidate, localTracks),
      tracks: release ? defaultTrackSelection(release, localTracks) : {},
      trackArtists: release ? defaultTrackArtistSelection(release, localTracks) : {}
    })
    setTrackDefaultsFor(release ? candidate.mbid : null)
  } else if (release && trackDefaultsFor !== candidate.mbid) {
    // Hydration just resolved for the viewed candidate: merge in the
    // per-track defaults without touching album-field selections.
    setTrackDefaultsFor(candidate.mbid)
    setSel((s) => ({
      ...s,
      tracks: defaultTrackSelection(release, localTracks),
      trackArtists: defaultTrackArtistSelection(release, localTracks)
    }))
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const setAlbumField = (field: FieldId, v: 'local' | 'mb'): void =>
    setSel((s) => ({ ...s, album: { ...s.album, [field]: v } }))

  const setTrackField = (key: TrackKey, v: 'local' | 'mb'): void =>
    setSel((s) => ({ ...s, tracks: { ...s.tracks, [key]: v } }))

  const setTrackArtistField = (key: TrackKey, v: 'local' | 'mb'): void =>
    setSel((s) => ({ ...s, trackArtists: { ...s.trackArtists, [key]: v } }))

  // Resolve local → MB once, at Apply, so the caller never re-derives keys.
  const buildApplyTracks = (r: MusicBrainzRelease): MBTrackApply[] => {
    const out: MBTrackApply[] = []
    for (const local of localTracks) {
      const mb = findMBTrack(r, local)
      if (!mb) continue
      const key = trackKey(local.disc_number, local.track_number)
      out.push({
        localId: local.id,
        mb,
        title: sel.tracks[key] ?? 'local',
        artist: sel.trackArtists[key] ?? 'local'
      })
    }
    return out
  }

  const albumFieldRows: Array<{ field: FieldId; localVal: string; mbVal: string }> = useMemo(
    () => [
      {
        field: 'title',
        localVal: localAlbum?.album ?? '',
        mbVal: candidate.title
      },
      {
        field: 'album_artist',
        localVal: localAlbum?.album_artist ?? '',
        mbVal: candidate.album_artist
      },
      {
        field: 'release_date',
        localVal: localAlbum?.release_date ?? '',
        mbVal: candidate.release_date
      },
      {
        field: 'label',
        localVal: localAlbum?.label ?? '',
        mbVal: candidate.label
      }
    ],
    [candidate, localAlbum]
  )

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal mb-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mb-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="mb-modal__header">
          <h2 id="mb-modal-title" className="mb-modal__title">
            MusicBrainz — {candidate.title}
          </h2>
          <div className="mb-modal__header-right">
            {candidates.length > 1 && (
              <div className="mb-modal__nav" role="group" aria-label="Switch candidate">
                <button
                  className="mb-modal__nav-btn"
                  type="button"
                  aria-label="Previous candidate"
                  disabled={candidateIndex === 0}
                  onClick={() => setCandidateIndex((i) => i - 1)}
                >
                  ‹
                </button>
                <span className="mb-modal__nav-count">
                  {candidateIndex + 1} / {candidates.length}
                </span>
                <button
                  className="mb-modal__nav-btn"
                  type="button"
                  aria-label="Next candidate"
                  disabled={candidateIndex === candidates.length - 1}
                  onClick={() => setCandidateIndex((i) => i + 1)}
                >
                  ›
                </button>
              </div>
            )}
            {candidate.is_current && <span className="mb-modal__release-type">Current</span>}
            {candidate.release_type && (
              <span className="mb-modal__release-type">{candidate.release_type}</span>
            )}
          </div>
        </div>

        {/* Body */}
        <div className="mb-modal__body">
          {/* Album-level fields */}
          <div className="mb-modal__section-label">Album</div>
          {albumFieldRows.map(({ field, localVal, mbVal }) => {
            const isDiff = localVal !== mbVal
            const chosen = sel.album[field]
            return (
              <div key={field} className="mb-cmp-row">
                <span className="mb-cmp-row__label">{FIELD_LABELS[field]}</span>
                <Toggle side={sel.album[field]} onChange={(v) => setAlbumField(field, v)} />
                <div className="mb-cmp-row__values">
                  <span
                    className={`mb-cmp-row__local${chosen === 'mb' && isDiff ? ' mb-cmp-row__local--overridden' : ''}`}
                  >
                    {localVal || <em style={{ opacity: 0.4 }}>empty</em>}
                  </span>
                  <span
                    className={`mb-cmp-row__mb${isDiff ? ' mb-cmp-row__mb--diff' : ' mb-cmp-row__mb--same'}`}
                  >
                    {mbVal || <em style={{ opacity: 0.4 }}>empty</em>}
                  </span>
                </div>
              </div>
            )
          })}

          {/* Track-level titles (available once the candidate is hydrated) */}
          <div className="mb-modal__section-label">Tracks</div>
          {!release && hydrationError[candidate.mbid] && (
            <div className="mb-cmp-row mb-cmp-row--unmatched">
              <div className="mb-cmp-row__values" style={{ gridColumn: '1 / -1' }}>
                <span className="mb-cmp-row__mb--no-match">
                  Couldn&apos;t load this release: {hydrationError[candidate.mbid]}
                </span>
              </div>
            </div>
          )}
          {!release && !hydrationError[candidate.mbid] && (
            <div className="mb-cmp-row mb-cmp-row--unmatched">
              <div className="mb-cmp-row__values" style={{ gridColumn: '1 / -1' }}>
                <span className="mb-cmp-row__local">Loading track list…</span>
              </div>
            </div>
          )}
          {release &&
            localTracks.map((local) => {
              const mb = findMBTrack(release, local)
              const key = trackKey(local.disc_number, local.track_number)

              if (!mb) {
                return (
                  <div key={local.id} className="mb-cmp-row mb-cmp-row--unmatched">
                    <span className="mb-cmp-row__label">
                      {local.disc_number > 1 ? `${local.disc_number}-` : ''}
                      {local.track_number}
                    </span>
                    <div className="mb-cmp-row__values" style={{ gridColumn: '2 / -1' }}>
                      <span className="mb-cmp-row__local">{local.title}</span>
                      <span className="mb-cmp-row__mb--no-match">no MB match</span>
                    </div>
                  </div>
                )
              }

              const isDiff = local.title !== mb.title
              const chosen = sel.tracks[key] ?? 'local'
              // Artist gets its own row, but only where MB actually disagrees:
              // on a single-artist album every track would otherwise carry a
              // redundant row with nothing to choose (KAMP-583).
              const showArtist = artistDiffers(mb, local)
              const artistChosen = sel.trackArtists[key] ?? 'local'
              return (
                <React.Fragment key={local.id}>
                  <div className="mb-cmp-row">
                    <span className="mb-cmp-row__label">
                      {local.disc_number > 1 ? `${local.disc_number}-` : ''}
                      {local.track_number}
                    </span>
                    <Toggle side={chosen} onChange={(v) => setTrackField(key, v)} />
                    <div className="mb-cmp-row__values">
                      <span
                        className={`mb-cmp-row__local${chosen === 'mb' && isDiff ? ' mb-cmp-row__local--overridden' : ''}`}
                      >
                        {local.title}
                      </span>
                      <span
                        className={`mb-cmp-row__mb${isDiff ? ' mb-cmp-row__mb--diff' : ' mb-cmp-row__mb--same'}`}
                      >
                        {mb.title}
                      </span>
                    </div>
                  </div>
                  {showArtist && (
                    <div className="mb-cmp-row mb-cmp-row--sub">
                      <span className="mb-cmp-row__label mb-cmp-row__label--sub">artist</span>
                      <Toggle side={artistChosen} onChange={(v) => setTrackArtistField(key, v)} />
                      <div className="mb-cmp-row__values">
                        <span
                          className={`mb-cmp-row__local${artistChosen === 'mb' ? ' mb-cmp-row__local--overridden' : ''}`}
                        >
                          {local.artist || <em style={{ opacity: 0.4 }}>empty</em>}
                        </span>
                        <span className="mb-cmp-row__mb mb-cmp-row__mb--diff">{mb.artist}</span>
                      </div>
                    </div>
                  )}
                </React.Fragment>
              )
            })}
        </div>

        {/* Footer */}
        <div className="mb-modal__footer">
          <button className="mb-modal__btn mb-modal__btn--ghost" type="button" onClick={onClose}>
            Cancel
          </button>
          <button
            className="mb-modal__btn mb-modal__btn--accent"
            type="button"
            disabled={!release}
            onClick={() =>
              release && onApply({ album: sel.album, tracks: buildApplyTracks(release), release })
            }
          >
            Apply selected
          </button>
        </div>
      </div>
    </div>
  )
}
