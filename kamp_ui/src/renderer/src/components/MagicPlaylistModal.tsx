import React, { useEffect, useReducer, useState } from 'react'
import { useStore } from '../store'
import { previewCriteria } from '../api/client'
import type { CriteriaDoc, CriteriaField, CriteriaOperator, Playlist } from '../api/client'
import '../assets/magic-playlist-modal.css'

// ---------------------------------------------------------------------------
// Field metadata
// ---------------------------------------------------------------------------

type FieldType = 'bool' | 'int' | 'date' | 'text' | 'source' | 'playlist'

const FIELD_META: Record<CriteriaField, { label: string; type: FieldType }> = {
  'track.artist': { label: 'Artist', type: 'text' },
  'track.album': { label: 'Album', type: 'text' },
  'track.genre': { label: 'Genre', type: 'text' },
  'track.year': { label: 'Year', type: 'int' },
  'track.play_count': { label: 'Play Count', type: 'int' },
  'track.favorite': { label: 'Favorited Track', type: 'bool' },
  'album.favorite': { label: 'Favorited Album', type: 'bool' },
  'track.last_played': { label: 'Last Played', type: 'date' },
  'track.date_added': { label: 'Date Added', type: 'date' },
  'track.source': { label: 'Source', type: 'source' },
  in_playlist: { label: 'In Playlist', type: 'playlist' }
}

const FIELD_ORDER: CriteriaField[] = [
  'track.artist',
  'track.album',
  'track.genre',
  'track.year',
  'track.play_count',
  'track.favorite',
  'album.favorite',
  'track.last_played',
  'track.date_added',
  'track.source',
  'in_playlist'
]

// Operators available per field type, with display labels
const OPS_FOR_TYPE: Record<FieldType, { op: CriteriaOperator; label: string }[]> = {
  bool: [{ op: 'is', label: 'is' }],
  int: [
    { op: 'is', label: 'is' },
    { op: 'is_not', label: 'is not' },
    { op: 'gt', label: '>' },
    { op: 'lt', label: '<' },
    { op: 'gte', label: '≥' },
    { op: 'lte', label: '≤' }
  ],
  date: [
    { op: 'gt', label: 'after' },
    { op: 'lt', label: 'before' },
    { op: 'gte', label: 'on or after' },
    { op: 'lte', label: 'on or before' }
  ],
  text: [
    { op: 'contains', label: 'contains' },
    { op: 'not_contains', label: 'does not contain' },
    { op: 'is', label: 'is' },
    { op: 'is_not', label: 'is not' }
  ],
  source: [
    { op: 'is', label: 'is' },
    { op: 'is_not', label: 'is not' }
  ],
  playlist: [
    { op: 'is', label: 'is in' },
    { op: 'is_not', label: 'is not in' }
  ]
}

function defaultOpForType(type: FieldType): CriteriaOperator {
  return OPS_FOR_TYPE[type][0].op
}

// ---------------------------------------------------------------------------
// State types
// ---------------------------------------------------------------------------

type DateMeta =
  | { mode: 'relative'; amount: number; unit: 'days' | 'weeks' | 'months' }
  | { mode: 'absolute'; date: string }

type ConditionState = {
  id: string
  field: CriteriaField
  op: CriteriaOperator
  value: string
  dateMeta?: DateMeta
}

type GroupState = {
  id: string
  match: 'all' | 'any'
  negate: boolean
  conditions: ConditionState[]
}

type ModalState = {
  title: string
  match: 'all' | 'any'
  groups: GroupState[]
}

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

type Action =
  | { type: 'SET_TITLE'; title: string }
  | { type: 'SET_TOP_MATCH'; match: 'all' | 'any' }
  | { type: 'ADD_GROUP' }
  | { type: 'REMOVE_GROUP'; groupId: string }
  | { type: 'SET_GROUP_MATCH'; groupId: string; match: 'all' | 'any' }
  | { type: 'TOGGLE_GROUP_NEGATE'; groupId: string }
  | { type: 'ADD_CONDITION'; groupId: string }
  | { type: 'REMOVE_CONDITION'; groupId: string; conditionId: string }
  | {
      type: 'UPDATE_CONDITION'
      groupId: string
      conditionId: string
      patch: Partial<ConditionState>
    }

let _nextId = 0
function uid(): string {
  return String(++_nextId)
}

function defaultCondition(): ConditionState {
  return { id: uid(), field: 'track.artist', op: 'contains', value: '' }
}

function defaultGroup(): GroupState {
  return { id: uid(), match: 'all', negate: false, conditions: [defaultCondition()] }
}

function reducer(state: ModalState, action: Action): ModalState {
  switch (action.type) {
    case 'SET_TITLE':
      return { ...state, title: action.title }

    case 'SET_TOP_MATCH':
      return { ...state, match: action.match }

    case 'ADD_GROUP':
      return { ...state, groups: [...state.groups, defaultGroup()] }

    case 'REMOVE_GROUP':
      return { ...state, groups: state.groups.filter((g) => g.id !== action.groupId) }

    case 'SET_GROUP_MATCH':
      return {
        ...state,
        groups: state.groups.map((g) =>
          g.id === action.groupId ? { ...g, match: action.match } : g
        )
      }

    case 'TOGGLE_GROUP_NEGATE':
      return {
        ...state,
        groups: state.groups.map((g) => (g.id === action.groupId ? { ...g, negate: !g.negate } : g))
      }

    case 'ADD_CONDITION':
      return {
        ...state,
        groups: state.groups.map((g) =>
          g.id === action.groupId ? { ...g, conditions: [...g.conditions, defaultCondition()] } : g
        )
      }

    case 'REMOVE_CONDITION':
      return {
        ...state,
        groups: state.groups.map((g) =>
          g.id === action.groupId
            ? { ...g, conditions: g.conditions.filter((c) => c.id !== action.conditionId) }
            : g
        )
      }

    case 'UPDATE_CONDITION': {
      return {
        ...state,
        groups: state.groups.map((g) =>
          g.id === action.groupId
            ? {
                ...g,
                conditions: g.conditions.map((c) =>
                  c.id === action.conditionId ? { ...c, ...action.patch } : c
                )
              }
            : g
        )
      }
    }

    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// CriteriaDoc builder
// ---------------------------------------------------------------------------

const UNIT_SECONDS: Record<'days' | 'weeks' | 'months', number> = {
  days: 86400,
  weeks: 604800,
  months: 2592000
}

function buildCriteriaDoc(state: ModalState): CriteriaDoc {
  return {
    match: state.match,
    groups: state.groups.map((g) => ({
      match: g.match,
      negate: g.negate,
      conditions: g.conditions.map((c) => {
        const fieldType = FIELD_META[c.field].type
        let op: CriteriaOperator = c.op
        let value = c.value

        if (fieldType === 'date' && c.dateMeta) {
          if (c.dateMeta.mode === 'relative') {
            op = 'gt'
            value = String(Date.now() / 1000 - c.dateMeta.amount * UNIT_SECONDS[c.dateMeta.unit])
          } else if (c.dateMeta.date) {
            value = String(new Date(c.dateMeta.date).getTime() / 1000)
          }
        } else if (fieldType === 'bool') {
          op = 'is'
        }

        return { field: c.field, op, value }
      })
    }))
  }
}

function hasAnyCriteria(state: ModalState): boolean {
  return state.groups.some((g) => g.conditions.length > 0)
}

function hasAllConditionsComplete(state: ModalState): boolean {
  return state.groups.every((g) =>
    g.conditions.every((c) => {
      const { type } = FIELD_META[c.field]
      if (type === 'bool' || type === 'source') return true
      if (type === 'date') return !!c.dateMeta
      return c.value.trim() !== ''
    })
  )
}

// ---------------------------------------------------------------------------
// Sub-components for condition value inputs
// ---------------------------------------------------------------------------

function BoolInput({
  value,
  onChange
}: {
  value: string
  onChange: (v: string) => void
}): React.JSX.Element {
  return (
    <select
      className="magic-select"
      value={value || 'true'}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="true">Yes</option>
      <option value="false">No</option>
    </select>
  )
}

function SourceInput({
  value,
  onChange
}: {
  value: string
  onChange: (v: string) => void
}): React.JSX.Element {
  return (
    <select
      className="magic-select"
      value={value || 'local'}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="local">Local</option>
      <option value="bandcamp">Bandcamp</option>
    </select>
  )
}

function PlaylistInput({
  value,
  onChange
}: {
  value: string
  onChange: (v: string) => void
}): React.JSX.Element {
  const playlists = useStore((s) => s.library.playlists).filter((p) => !p.criteria)
  return (
    <select
      className="magic-select magic-select--wide"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {playlists.length === 0 && <option value="">No simple playlists</option>}
      {playlists.map((p) => (
        <option key={p.id} value={String(p.id)}>
          {p.title}
        </option>
      ))}
    </select>
  )
}

function DateInput({
  dateMeta,
  op,
  onChangeDateMeta,
  onChangeOp
}: {
  dateMeta: DateMeta | undefined
  op: CriteriaOperator
  onChangeDateMeta: (m: DateMeta) => void
  onChangeOp: (op: CriteriaOperator) => void
}): React.JSX.Element {
  const meta: DateMeta = dateMeta ?? { mode: 'relative', amount: 7, unit: 'days' }
  const isRelative = meta.mode === 'relative'

  return (
    <div className="magic-date-input">
      <select
        className="magic-select"
        value={meta.mode}
        onChange={(e) => {
          const mode = e.target.value as 'relative' | 'absolute'
          if (mode === 'relative') {
            onChangeDateMeta({ mode: 'relative', amount: 7, unit: 'days' })
          } else {
            onChangeOp('gt')
            onChangeDateMeta({ mode: 'absolute', date: '' })
          }
        }}
      >
        <option value="relative">in the last</option>
        <option value="absolute">absolute date</option>
      </select>

      {isRelative && meta.mode === 'relative' ? (
        <>
          <input
            className="magic-input magic-input--number"
            type="number"
            min={1}
            value={meta.amount}
            onChange={(e) =>
              onChangeDateMeta({ ...meta, amount: Math.max(1, Number(e.target.value)) })
            }
          />
          <select
            className="magic-select"
            value={meta.unit}
            onChange={(e) =>
              onChangeDateMeta({ ...meta, unit: e.target.value as 'days' | 'weeks' | 'months' })
            }
          >
            <option value="days">days</option>
            <option value="weeks">weeks</option>
            <option value="months">months</option>
          </select>
        </>
      ) : (
        <>
          <select
            className="magic-select"
            value={op}
            onChange={(e) => onChangeOp(e.target.value as CriteriaOperator)}
          >
            {OPS_FOR_TYPE.date.map(({ op: o, label }) => (
              <option key={o} value={o}>
                {label}
              </option>
            ))}
          </select>
          <input
            className="magic-input"
            type="date"
            value={meta.mode === 'absolute' ? meta.date : ''}
            onChange={(e) => onChangeDateMeta({ mode: 'absolute', date: e.target.value })}
          />
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Condition row
// ---------------------------------------------------------------------------

function ConditionRow({
  condition,
  onUpdate,
  onRemove
}: {
  condition: ConditionState
  onUpdate: (patch: Partial<ConditionState>) => void
  onRemove: () => void
}): React.JSX.Element {
  const fieldType = FIELD_META[condition.field].type
  const ops = OPS_FOR_TYPE[fieldType]
  const isBool = fieldType === 'bool'
  const isDate = fieldType === 'date'

  const handleFieldChange = (newField: CriteriaField): void => {
    const newType = FIELD_META[newField].type
    const newOp = defaultOpForType(newType)
    const patch: Partial<ConditionState> = {
      field: newField,
      op: newOp,
      value: '',
      dateMeta: undefined
    }
    if (newType === 'date') {
      patch.dateMeta = { mode: 'relative', amount: 7, unit: 'days' }
    } else if (newType === 'bool') {
      patch.value = 'true'
    }
    onUpdate(patch)
  }

  return (
    <div className="magic-condition">
      <select
        className="magic-select magic-select--field"
        value={condition.field}
        onChange={(e) => handleFieldChange(e.target.value as CriteriaField)}
      >
        {FIELD_ORDER.map((f) => (
          <option key={f} value={f}>
            {FIELD_META[f].label}
          </option>
        ))}
      </select>

      {!isBool && !isDate && (
        <select
          className="magic-select"
          value={condition.op}
          onChange={(e) => onUpdate({ op: e.target.value as CriteriaOperator })}
        >
          {ops.map(({ op, label }) => (
            <option key={op} value={op}>
              {label}
            </option>
          ))}
        </select>
      )}

      {isBool && <BoolInput value={condition.value} onChange={(v) => onUpdate({ value: v })} />}

      {isDate && (
        <DateInput
          dateMeta={condition.dateMeta}
          op={condition.op}
          onChangeDateMeta={(m) => onUpdate({ dateMeta: m })}
          onChangeOp={(op) => onUpdate({ op })}
        />
      )}

      {fieldType === 'text' && (
        <input
          className="magic-input magic-input--text"
          type="text"
          value={condition.value}
          placeholder="value"
          onChange={(e) => onUpdate({ value: e.target.value })}
        />
      )}

      {fieldType === 'int' && (
        <input
          className="magic-input magic-input--number"
          type="number"
          value={condition.value}
          onChange={(e) => onUpdate({ value: e.target.value })}
        />
      )}

      {fieldType === 'source' && (
        <SourceInput value={condition.value} onChange={(v) => onUpdate({ value: v })} />
      )}

      {fieldType === 'playlist' && (
        <PlaylistInput value={condition.value} onChange={(v) => onUpdate({ value: v })} />
      )}

      <button className="magic-remove-btn" title="Remove rule" onClick={onRemove}>
        ×
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Group
// ---------------------------------------------------------------------------

function GroupBlock({
  group,
  groupIndex,
  totalGroups,
  dispatch
}: {
  group: GroupState
  groupIndex: number
  totalGroups: number
  dispatch: React.Dispatch<Action>
}): React.JSX.Element {
  return (
    <div className="magic-group">
      <div className="magic-group-header">
        <button
          className={`match-pill${group.match === 'all' ? ' match-pill--active' : ''}`}
          title="Toggle All / Any"
          onClick={() =>
            dispatch({
              type: 'SET_GROUP_MATCH',
              groupId: group.id,
              match: group.match === 'all' ? 'any' : 'all'
            })
          }
        >
          {group.match === 'all' ? 'All' : 'Any'}
        </button>
        <span className="magic-group-label">of the following rules match</span>
        <button
          className={`magic-negate-btn${group.negate ? ' magic-negate-btn--active' : ''}`}
          title="Toggle NOT"
          onClick={() => dispatch({ type: 'TOGGLE_GROUP_NEGATE', groupId: group.id })}
        >
          NOT
        </button>
        {totalGroups > 1 && (
          <button
            className="magic-remove-btn magic-remove-btn--group"
            title="Remove group"
            onClick={() => dispatch({ type: 'REMOVE_GROUP', groupId: group.id })}
          >
            ×
          </button>
        )}
      </div>

      <div className="magic-conditions">
        {group.conditions.map((c) => (
          <ConditionRow
            key={c.id}
            condition={c}
            onUpdate={(patch) =>
              dispatch({ type: 'UPDATE_CONDITION', groupId: group.id, conditionId: c.id, patch })
            }
            onRemove={() =>
              dispatch({ type: 'REMOVE_CONDITION', groupId: group.id, conditionId: c.id })
            }
          />
        ))}
      </div>

      <button
        className="magic-add-rule-btn"
        onClick={() => dispatch({ type: 'ADD_CONDITION', groupId: group.id })}
      >
        + Add a rule
      </button>

      {groupIndex < totalGroups - 1 && <div className="magic-group-separator">AND</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

function initialState(playlist?: Playlist): ModalState {
  if (playlist?.criteria) {
    // Edit mode: load existing criteria into local state
    const doc = playlist.criteria
    return {
      title: playlist.title,
      match: doc.match,
      groups: doc.groups.map((g) => ({
        id: uid(),
        match: g.match,
        negate: g.negate,
        conditions: g.conditions.map((c) => ({
          id: uid(),
          field: c.field,
          op: c.op,
          value: c.value,
          // Loaded as absolute mode; relative intent is not round-tripped
          dateMeta:
            FIELD_META[c.field]?.type === 'date'
              ? {
                  mode: 'absolute',
                  date: new Date(Number(c.value) * 1000).toISOString().slice(0, 10)
                }
              : undefined
        }))
      }))
    }
  }
  return { title: '', match: 'all', groups: [defaultGroup()] }
}

export function MagicPlaylistModal({
  open,
  onClose,
  playlist
}: {
  open: boolean
  onClose: () => void
  playlist?: Playlist
}): React.JSX.Element | null {
  const createMagicPlaylist = useStore((s) => s.createMagicPlaylist)
  const updateMagicPlaylistCriteria = useStore((s) => s.updateMagicPlaylistCriteria)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)

  const isEditMode = !!playlist

  const [state, dispatch] = useReducer(reducer, playlist, initialState)
  const [previewText, setPreviewText] = useState<string>('')
  const [saving, setSaving] = useState(false)

  // Reset state when modal opens with a new playlist prop
  useEffect(() => {
    if (open) {
      // Re-initialize reducer by dispatching to a fresh state isn't ergonomic with useReducer.
      // We rely on the key prop in PlaylistGrid to remount on each open (see wiring in PlaylistGrid).
    }
  }, [open])

  // Debounced preview — only fires when all conditions are present and filled in
  useEffect(() => {
    if (!open || !hasAnyCriteria(state) || !hasAllConditionsComplete(state)) return
    const criteria = buildCriteriaDoc(state)
    const timeout = setTimeout(async () => {
      try {
        const result = await previewCriteria(criteria)
        setPreviewText(`Matches ${result.count} track${result.count === 1 ? '' : 's'}`)
      } catch {
        setPreviewText('Preview unavailable')
      }
    }, 300)
    return () => clearTimeout(timeout)
  }, [open, state])

  // Escape key
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  const handleBackdropClick = (e: React.MouseEvent): void => {
    if (e.target === e.currentTarget) onClose()
  }

  const handleSave = (): void => {
    if (saving) return
    setSaving(true)
    const criteria = buildCriteriaDoc(state)
    void (async () => {
      try {
        if (isEditMode && playlist) {
          await updateMagicPlaylistCriteria(playlist.id, criteria)
          onClose()
        } else {
          const title = state.title.trim() || 'Magic Playlist'
          const pl = await createMagicPlaylist(title, criteria)
          setCollectionType('playlists')
          await selectPlaylist(pl)
          onClose()
        }
      } finally {
        setSaving(false)
      }
    })()
  }

  return (
    <div className="prefs-overlay" onClick={handleBackdropClick}>
      <div className="magic-playlist-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="magic-playlist-header">
          <span className="magic-playlist-title">
            {isEditMode ? `Edit: ${playlist.title}` : 'New Magic Playlist'}
          </span>
          <button className="magic-close-btn" onClick={onClose} title="Close">
            ×
          </button>
        </div>

        <div className="magic-playlist-body">
          {!isEditMode && (
            <div className="magic-title-row">
              <label className="magic-label" htmlFor="magic-title">
                Name
              </label>
              <input
                id="magic-title"
                className="magic-input magic-input--title"
                type="text"
                placeholder="Magic Playlist"
                value={state.title}
                onChange={(e) => dispatch({ type: 'SET_TITLE', title: e.target.value })}
              />
            </div>
          )}

          {state.groups.length > 1 && (
            <div className="magic-top-match-row">
              <span>Match</span>
              <button
                className="match-pill match-pill--active"
                onClick={() =>
                  dispatch({
                    type: 'SET_TOP_MATCH',
                    match: state.match === 'all' ? 'any' : 'all'
                  })
                }
              >
                {state.match === 'all' ? 'All' : 'Any'}
              </button>
              <span>of the following groups:</span>
            </div>
          )}

          <div className="magic-groups">
            {state.groups.map((g, i) => (
              <GroupBlock
                key={g.id}
                group={g}
                groupIndex={i}
                totalGroups={state.groups.length}
                dispatch={dispatch}
              />
            ))}
          </div>

          <button className="magic-add-group-btn" onClick={() => dispatch({ type: 'ADD_GROUP' })}>
            + Add group
          </button>
        </div>

        <div className="magic-playlist-footer">
          <span className="magic-preview-text">
            {!hasAnyCriteria(state)
              ? 'No conditions set — will match no tracks.'
              : !hasAllConditionsComplete(state)
                ? 'Fill in all condition values to see a preview.'
                : previewText || 'Matches…'}
          </span>
          <div className="magic-footer-actions">
            <button className="magic-btn magic-btn--cancel" onClick={onClose}>
              Cancel
            </button>
            <button
              className="magic-btn magic-btn--save"
              onClick={handleSave}
              disabled={saving || !hasAllConditionsComplete(state)}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
