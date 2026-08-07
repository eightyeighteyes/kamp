// The Crate (KAMP-650) — a crate of ten records to dig through.
//
// User-facing name is "The Crate"; `discovery` stays the code/API namespace.
// The whole crate arrives in one `discovery.crate` snapshot and is seeded from
// GET /api/v1/discovery/crate on every WS (re)connect, since _broadcast no-ops
// with no client attached.
//
// Plumbing only in this commit — the rail, the focus card and the keyboard model
// land in the commits that follow.
import React from 'react'
import { useStore } from '../store'

export function CrateView({ active = false }: { active?: boolean }): React.JSX.Element {
  const crate = useStore((s) => s.crate)
  void active

  return (
    <div className="crate-view">
      <p className="crate-empty-hint">
        {crate ? `Crate ${crate.crate_no ?? '—'}` : 'No crate yet.'}
      </p>
    </div>
  )
}
