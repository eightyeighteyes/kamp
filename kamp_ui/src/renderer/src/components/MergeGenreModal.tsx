import React, { useEffect, useMemo, useState } from 'react'
import { useStore } from '../store'
import { getGenreMerges } from '../api/client'

type Props = {
  source: string
  // Pre-selected target when editing an existing merge (KAMP-610).
  currentTarget?: string
  onConfirm: (target: string) => void
  onCancel: () => void
}

// Pick a target genre to merge the source into (KAMP-607). The target list
// excludes the source itself and any genre that is already a merge source
// (a merge target can't be a source — no chains).
export function MergeGenreModal({
  source,
  currentTarget,
  onConfirm,
  onCancel
}: Props): React.JSX.Element {
  const genres = useStore((s) => s.library.genres)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState<string | null>(currentTarget ?? null)
  const [excluded, setExcluded] = useState<Set<string>>(new Set())

  // Esc = implicit Cancel.
  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onCancel])

  // Existing merge sources can't be chosen as targets (no chains).
  useEffect(() => {
    let cancelled = false
    void getGenreMerges().then((merges) => {
      if (!cancelled) setExcluded(new Set(merges.map((m) => m.source.toLowerCase())))
    })
    return () => {
      cancelled = true
    }
  }, [])

  const candidates = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const src = source.toLowerCase()
    return genres.filter(
      (g) =>
        g.toLowerCase() !== src &&
        !excluded.has(g.toLowerCase()) &&
        (!q || g.toLowerCase().includes(q))
    )
  }, [genres, filter, excluded, source])

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal collision-modal merge-genre-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="merge-genre-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="merge-genre-title" className="modal-title">
          Merge genres
        </h2>
        <p className="modal-body">
          Choose another genre to merge <strong>{source}</strong> into. Merging will update all
          albums with this genre, now and in the future, to the merged genre.
        </p>
        <input
          className="merge-genre-filter"
          value={filter}
          placeholder="Filter genres…"
          aria-label="Filter genres"
          onChange={(e) => setFilter(e.target.value)}
          autoFocus
        />
        <ul className="merge-genre-list" role="listbox" aria-label="Merge target">
          {candidates.length === 0 ? (
            <li className="merge-genre-empty" aria-disabled="true">
              No genres to merge into
            </li>
          ) : (
            candidates.map((g) => (
              <li key={g} role="option" aria-selected={selected === g}>
                <button
                  type="button"
                  className={`merge-genre-option${selected === g ? ' active' : ''}`}
                  onClick={() => setSelected(g)}
                >
                  {g}
                </button>
              </li>
            ))
          )}
        </ul>
        <div className="modal-actions">
          <button
            className="modal-btn modal-btn--destructive"
            disabled={!selected}
            onClick={() => selected && onConfirm(selected)}
          >
            Merge
          </button>
          <button className="modal-btn modal-btn--primary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
