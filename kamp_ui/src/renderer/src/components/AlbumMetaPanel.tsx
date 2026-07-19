import React, { useEffect, useRef } from 'react'
import { useStore } from '../store'
import { getGenres } from '../api/client'
import type { Track } from '../api/client'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { TagIcon, ChevronIcon } from './TransportIcons'
import { GenreChipsInput } from './GenreChipsInput'

interface AlbumMetaPanelProps {
  tracks: Track[]
  editMode: boolean
  expanded: boolean
  onToggle: () => void
  onSave: (opts: {
    genre?: string
    genres?: string[]
    label?: string
    release_date?: string
  }) => Promise<void>
  onHandleMouseDown?: (e: React.MouseEvent) => void
  onHandleDoubleClick?: () => void
}

/**
 * Derive the common value for a field across all tracks.
 * Returns the shared value, '(mixed)' if values differ, or '' if all are empty.
 */
function commonValue(tracks: Track[], key: keyof Track): string {
  const values = tracks.map((t) => String(t[key] ?? ''))
  const first = values[0] ?? ''
  if (values.every((v) => v === first)) return first
  return '(mixed)'
}

/**
 * The union of every track's genres (KAMP-586). Track.genre is a "; "-joined
 * display string; a "mixed" album rolls its tracks' genres up into one set that
 * is applied to every track on save. Case-insensitive dedup, sorted.
 */
function unionGenres(tracks: Track[]): string[] {
  const seen = new Map<string, string>()
  for (const t of tracks) {
    for (const g of (t.genre ?? '').split(';')) {
      const name = g.trim()
      if (name && !seen.has(name.toLowerCase())) seen.set(name.toLowerCase(), name)
    }
  }
  return [...seen.values()].sort((a, b) => a.localeCompare(b))
}

function hasAnyMeta(tracks: Track[], releaseDate: string): boolean {
  return !!(
    releaseDate ||
    tracks.some((t) => t.genre || t.label) ||
    tracks.some((t) => t.mb_release_id)
  )
}

interface MetaFieldProps {
  label: string
  value: string
  editMode: boolean
  readOnly?: boolean
  onChange?: (v: string) => void
  onBlur?: () => void
}

function MetaField({
  label,
  value,
  editMode,
  readOnly,
  onChange,
  onBlur
}: MetaFieldProps): React.JSX.Element {
  const tooltip = useTooltip()
  const isMixed = value === '(mixed)'
  const showInput = editMode && !readOnly && !isMixed

  return (
    <div className="album-meta-row">
      <dt className="album-meta-dt">{label}</dt>
      <dd className="album-meta-dd">
        {showInput ? (
          <input
            className="meta-field-input"
            value={value}
            onChange={(e) => onChange?.(e.target.value)}
            onBlur={onBlur}
          />
        ) : (
          <span className={readOnly || isMixed ? 'meta-field--readonly' : undefined}>
            {value || <span className="meta-field--empty">—</span>}
          </span>
        )}
        {readOnly && value && (
          <button
            className="meta-field-copy-btn"
            {...tooltip(TOOLTIPS.META_COPY)}
            aria-label={`Copy ${label}`}
            onClick={() => void navigator.clipboard.writeText(value)}
          >
            ⧉
          </button>
        )}
      </dd>
    </div>
  )
}

export function AlbumMetaPanel({
  tracks,
  editMode,
  expanded,
  onToggle,
  onSave,
  onHandleMouseDown,
  onHandleDoubleClick
}: AlbumMetaPanelProps): React.JSX.Element {
  const panelRef = useRef<HTMLDivElement>(null)
  const openGenreFilter = useStore((s) => s.openGenreFilter)

  const [label, setLabel] = React.useState(() => commonValue(tracks, 'label'))
  const [releaseDate, setReleaseDate] = React.useState(() => commonValue(tracks, 'release_date'))
  // Track the last-seen tracks reference so we can sync on external changes
  // (e.g. after a save) without using an effect.
  const [syncedTracks, setSyncedTracks] = React.useState(tracks)
  if (syncedTracks !== tracks) {
    setSyncedTracks(tracks)
    setLabel(commonValue(tracks, 'label'))
    setReleaseDate(commonValue(tracks, 'release_date'))
  }

  // Library genre vocabulary for the chips autocomplete — loaded lazily the
  // first time the panel enters edit mode (KAMP-586).
  const [genreSuggestions, setGenreSuggestions] = React.useState<string[]>([])
  useEffect(() => {
    if (!editMode) return
    let cancelled = false
    void getGenres().then(
      (g) => {
        if (!cancelled) setGenreSuggestions(g)
      },
      () => {
        /* autocomplete is best-effort */
      }
    )
    return () => {
      cancelled = true
    }
  }, [editMode])

  const albumGenres = unionGenres(tracks)

  // Instant show/hide — Electron's renderer produces jank with CSS/JS height
  // animations (see CLAUDE.md "CSS height animations in Electron").
  useEffect(() => {
    const el = panelRef.current
    if (!el) return
    el.style.display = expanded ? 'block' : 'none'
  }, [expanded])

  const mbId = tracks[0]?.mb_release_id ?? ''
  const hasContent = hasAnyMeta(tracks, commonValue(tracks, 'release_date'))

  const handleSaveGenres = (genres: string[]): void => {
    void onSave({ genres })
  }

  const handleSaveLabel = (): void => {
    const current = commonValue(tracks, 'label')
    if (label !== current) void onSave({ label })
  }

  const handleSaveReleaseDate = (): void => {
    const current = commonValue(tracks, 'release_date')
    if (releaseDate !== current) void onSave({ release_date: releaseDate })
  }

  return (
    <>
      <button
        className={`album-meta-toggle${expanded ? ' expanded' : ''}`}
        aria-expanded={expanded}
        aria-controls="album-meta-panel"
        onClick={onToggle}
        onMouseDown={onHandleMouseDown}
        onDoubleClick={onHandleDoubleClick}
      >
        <TagIcon size={11} />
        <span className="album-meta-toggle-label">
          {hasContent ? 'LINER NOTES' : 'LINER NOTES — no metadata yet'}
        </span>
        <ChevronIcon size={10} />
      </button>

      <div
        id="album-meta-panel"
        ref={panelRef}
        className="album-meta-panel"
        aria-hidden={!expanded}
      >
        <dl className="album-meta-rows">
          {(releaseDate || editMode) && (
            <MetaField
              label="RELEASE DATE"
              value={releaseDate}
              editMode={editMode}
              onChange={setReleaseDate}
              onBlur={handleSaveReleaseDate}
            />
          )}
          {(albumGenres.length > 0 || editMode) && (
            <div className="album-meta-row">
              <dt className="album-meta-dt">GENRE</dt>
              <dd className="album-meta-dd">
                <GenreChipsInput
                  chips={albumGenres}
                  suggestions={genreSuggestions}
                  editMode={editMode}
                  onCommit={handleSaveGenres}
                  onGenreClick={openGenreFilter}
                />
              </dd>
            </div>
          )}
          <MetaField
            label="LABEL"
            value={label}
            editMode={editMode}
            onChange={setLabel}
            onBlur={handleSaveLabel}
          />
          {mbId && <MetaField label="MUSICBRAINZ" value={mbId} editMode={editMode} readOnly />}
        </dl>
      </div>
    </>
  )
}
