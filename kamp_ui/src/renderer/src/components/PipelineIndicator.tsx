import React, { useEffect, useState } from 'react'

export function PipelineIndicator(): React.JSX.Element | null {
  const [stage, setStage] = useState('')

  useEffect(() => {
    return window.api.pipeline.onStage(setStage)
  }, [])

  return (
    <div
      className={`pipeline-indicator${stage ? ' pipeline-indicator--active' : ''}`}
      title={stage || 'Idle'}
    >
      {/* Approximates SF Symbol "music.note.list": a note head + stem + three horizontal lines */}
      <svg viewBox="0 0 20 20" width="20" height="20" fill="currentColor" aria-hidden="true">
        <ellipse cx="5.5" cy="14" rx="2.5" ry="2" />
        <rect x="7.5" y="5" width="1.5" height="9" />
        <rect x="7.5" y="5" width="5" height="1.5" />
        {/* <rect x="11" y="5" width="1.5" height="5" /> */}
        <line
          x1="13"
          y1="5"
          x2="18"
          y2="5"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <line
          x1="13"
          y1="9"
          x2="18"
          y2="9"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <line
          x1="13"
          y1="13"
          x2="18"
          y2="13"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
    </div>
  )
}
