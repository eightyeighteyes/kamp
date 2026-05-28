import React, { useEffect, useRef } from 'react'
import type { Track } from '../api/client'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { TagIcon, ChevronIcon } from './TransportIcons'

interface AlbumMetaPanelProps {
  tracks: Track[]
  editMode: boolean
  expanded: boolean
  onToggle: () => void
  onSave: (opts: { genre?: string; label?: string; year?: string }) => Promise<void>
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

function hasAnyMeta(tracks: Track[], year: string): boolean {
  return !!(year || tracks.some((t) => t.genre || t.label) || tracks.some((t) => t.mb_release_id))
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

  const [genre, setGenre] = React.useState(() => commonValue(tracks, 'genre'))
  const [label, setLabel] = React.useState(() => commonValue(tracks, 'label'))
  const [year, setYear] = React.useState(() => commonValue(tracks, 'year'))
  // Track the last-seen tracks reference so we can sync on external changes
  // (e.g. after a save) without using an effect.
  const [syncedTracks, setSyncedTracks] = React.useState(tracks)
  if (syncedTracks !== tracks) {
    setSyncedTracks(tracks)
    setGenre(commonValue(tracks, 'genre'))
    setLabel(commonValue(tracks, 'label'))
    setYear(commonValue(tracks, 'year'))
  }

  // Instant show/hide — Electron's renderer produces jank with CSS/JS height
  // animations (see CLAUDE.md "CSS height animations in Electron").
  useEffect(() => {
    const el = panelRef.current
    if (!el) return
    el.style.display = expanded ? 'block' : 'none'
  }, [expanded])

  const mbId = tracks[0]?.mb_release_id ?? ''
  const hasContent = hasAnyMeta(tracks, year)

  const handleSaveGenre = (): void => {
    const current = commonValue(tracks, 'genre')
    if (genre !== current) void onSave({ genre })
  }

  const handleSaveLabel = (): void => {
    const current = commonValue(tracks, 'label')
    if (label !== current) void onSave({ label })
  }

  const handleSaveYear = (): void => {
    const current = commonValue(tracks, 'year')
    if (year !== current) void onSave({ year })
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
          {year && (
            <MetaField
              label="YEAR"
              value={year}
              editMode={editMode}
              onChange={setYear}
              onBlur={handleSaveYear}
            />
          )}
          <MetaField
            label="GENRE"
            value={genre}
            editMode={editMode}
            onChange={setGenre}
            onBlur={handleSaveGenre}
          />
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
