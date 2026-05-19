import React, { useEffect, useMemo, useState } from 'react'
import type { MusicBrainzRelease, Track } from '../api/client'

// Fields that can be toggled between local and MB values.
type FieldId = 'title' | 'album_artist' | 'year' | 'label'

// Per-track selection key: "disc-track"
type TrackKey = string

type SelectionState = {
  album: Record<FieldId, 'local' | 'mb'>
  tracks: Record<TrackKey, 'local' | 'mb'>
}

export type MBApplyPayload = {
  album: Record<FieldId, 'local' | 'mb'>
  tracks: Record<TrackKey, 'local' | 'mb'>
  release: MusicBrainzRelease
}

type Props = {
  candidates: MusicBrainzRelease[]
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

function defaultSelection(release: MusicBrainzRelease, localTracks: Track[]): SelectionState {
  const album: Record<FieldId, 'local' | 'mb'> = {
    title: release.title !== localTracks[0]?.album ? 'mb' : 'local',
    album_artist: release.album_artist !== localTracks[0]?.album_artist ? 'mb' : 'local',
    year: release.year !== localTracks[0]?.year ? 'mb' : 'local',
    label: release.label !== localTracks[0]?.label ? 'mb' : 'local'
  }

  const tracks: Record<TrackKey, 'local' | 'mb'> = {}
  for (const local of localTracks) {
    const mb = findMBTrack(release, local)
    if (!mb) continue
    const key = trackKey(local.disc_number, local.track_number)
    tracks[key] = mb.title !== local.title ? 'mb' : 'local'
  }

  return { album, tracks }
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
  year: 'Year',
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
  const release = candidates[candidateIndex]

  const [sel, setSel] = useState<SelectionState>(() => defaultSelection(release, localTracks))

  // Reset selection when the active candidate changes (render-time derived state pattern).
  const [prevRelease, setPrevRelease] = useState(release)
  if (release !== prevRelease) {
    setPrevRelease(release)
    setSel(defaultSelection(release, localTracks))
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

  const albumFieldRows: Array<{ field: FieldId; localVal: string; mbVal: string }> = useMemo(
    () => [
      {
        field: 'title',
        localVal: localAlbum?.album ?? '',
        mbVal: release.title
      },
      {
        field: 'album_artist',
        localVal: localAlbum?.album_artist ?? '',
        mbVal: release.album_artist
      },
      {
        field: 'year',
        localVal: localAlbum?.year ?? '',
        mbVal: release.year
      },
      {
        field: 'label',
        localVal: localAlbum?.label ?? '',
        mbVal: release.label
      }
    ],
    [release, localAlbum]
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
            MusicBrainz — {release.title}
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
            {release.release_type && (
              <span className="mb-modal__release-type">{release.release_type}</span>
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

          {/* Track-level titles */}
          <div className="mb-modal__section-label">Tracks</div>
          {localTracks.map((local) => {
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
            return (
              <div key={local.id} className="mb-cmp-row">
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
            onClick={() => onApply({ album: sel.album, tracks: sel.tracks, release })}
          >
            Apply selected
          </button>
        </div>
      </div>
    </div>
  )
}
