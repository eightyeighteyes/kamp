import React, { useEffect, useState } from 'react'
import {
  getGenreAllowlist,
  addAllowlistEntry,
  revertAllowlist,
  getGenreMerges,
  deleteGenreMerge,
  mergeGenres,
  type GenreMerge
} from '../api/client'
import { RemoveFromQueueIcon, PencilIcon } from './TransportIcons'
import { MergeGenreModal } from './MergeGenreModal'

// Preferences → genre management (KAMP-610): add/revert allow-list entries and
// review / edit / delete the merges created in KAMP-607.
export function GenreManagementSection(): React.JSX.Element {
  const [extras, setExtras] = useState<string[]>([])
  const [defaults, setDefaults] = useState<string[]>([])
  const [merges, setMerges] = useState<GenreMerge[]>([])
  const [input, setInput] = useState('')
  const [showFull, setShowFull] = useState(false)
  const [editing, setEditing] = useState<GenreMerge | null>(null)

  const loadAllowlist = (): void => {
    void getGenreAllowlist().then((a) => {
      setExtras(a.extras)
      setDefaults(a.defaults)
    })
  }
  const loadMerges = (): void => {
    void getGenreMerges().then(setMerges)
  }
  useEffect(() => {
    loadAllowlist()
    loadMerges()
  }, [])

  // Only surface additions that aren't already shipped defaults (casefold).
  const defaultSet = new Set(defaults.map((d) => d.toLowerCase()))
  const additions = extras.filter((e) => !defaultSet.has(e.toLowerCase()))

  const addEntry = (): void => {
    const name = input.trim()
    if (!name) return
    setInput('')
    void addAllowlistEntry(name)
      .then(loadAllowlist)
      .catch(() => loadAllowlist())
  }

  return (
    <div className="prefs-section">
      <div className="prefs-section-label">Genre allow list</div>
      <p className="prefs-hint">
        Genres from Last.fm are filtered to a built-in list. Add your own entries here; they apply
        to future enrichment.
      </p>

      <div className="genre-admin-add">
        <input
          className="genre-admin-input"
          value={input}
          placeholder="Add a genre…"
          aria-label="Add allow-list genre"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              addEntry()
            }
          }}
        />
        <button className="prefs-choose-btn" onClick={addEntry} disabled={!input.trim()}>
          Add
        </button>
      </div>

      {additions.length > 0 ? (
        <div className="genre-admin-chips">
          {additions.map((g) => (
            <span key={g} className="genre-chip">
              {g}
            </span>
          ))}
        </div>
      ) : (
        <p className="genre-admin-empty">No additions — using the built-in list.</p>
      )}

      <div className="genre-admin-actions">
        <button
          className="prefs-choose-btn"
          onClick={() => {
            void revertAllowlist().then(loadAllowlist)
          }}
          disabled={additions.length === 0}
        >
          Revert to default
        </button>
        <button className="prefs-link-btn" onClick={() => setShowFull((v) => !v)}>
          {showFull ? 'Hide built-in list' : 'View built-in list'}
        </button>
      </div>
      {showFull && (
        <ul className="genre-admin-full">
          {defaults.map((g) => (
            <li key={g}>{g}</li>
          ))}
        </ul>
      )}

      <div className="prefs-section-label" style={{ marginTop: 20 }}>
        Genre merges
      </div>
      <p className="prefs-hint">
        Deleting or editing a merge only affects future tags — tracks already merged keep the target
        genre.
      </p>
      {merges.length === 0 ? (
        <p className="genre-admin-empty">No merges yet.</p>
      ) : (
        <ul className="genre-merge-rows">
          {merges.map((m) => (
            <li key={m.source} className="genre-merge-row">
              <span className="genre-merge-text">
                {m.source} <span className="genre-merge-arrow">→</span> {m.target}
              </span>
              <span className="genre-merge-controls">
                <button
                  className="genre-merge-btn"
                  aria-label={`Edit merge of ${m.source}`}
                  onClick={() => setEditing(m)}
                >
                  <PencilIcon size={13} />
                </button>
                <button
                  className="genre-merge-btn"
                  aria-label={`Delete merge of ${m.source}`}
                  onClick={() => {
                    void deleteGenreMerge(m.source).then(loadMerges)
                  }}
                >
                  <RemoveFromQueueIcon size={13} />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <MergeGenreModal
          source={editing.source}
          currentTarget={editing.target}
          onConfirm={(target) => {
            void mergeGenres(editing.source, target).then(loadMerges)
            setEditing(null)
          }}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  )
}
