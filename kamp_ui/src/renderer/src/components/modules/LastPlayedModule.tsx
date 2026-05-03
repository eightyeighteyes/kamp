import React from 'react'

export function LastPlayedModule(): React.JSX.Element {
  return (
    <div className="module-skeleton-row">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="module-skeleton-card" />
      ))}
    </div>
  )
}
