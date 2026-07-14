import React, { useEffect, useState } from 'react'
import { useTooltip } from '../hooks/useTooltip'
import { useStore } from '../store'

// The stage label the daemon emits for the metadata-tagging step. Every other
// non-empty stage (Extracting / Updating artwork / Moving) is treated as the
// "copying to library" phase. Kept as a named constant so a backend rewording
// is a one-line change here (KAMP-558).
const TAGGING_STAGE = 'Tagging'

type IndicatorMode = 'idle' | 'copying' | 'tagging'

/**
 * Pure mapping from the raw pipeline stage + album label to the indicator's
 * visual mode and tooltip. Extracted so the state machine is reviewable without
 * a renderer (kamp_ui has no unit-test runner).
 *
 * - ''            → idle folder (tooltip is the folder action, set by the caller)
 * - 'Tagging'     → pulsing tag, "Tagging {album}…"
 * - anything else → spinning arrows, "Copying {album} to Library…"
 *
 * album is "" before extraction (and for pre-558 daemons); the tooltip drops the
 * trailing " {album}" in that case rather than showing a double space.
 */
function deriveState(stage: string, album: string): { mode: IndicatorMode; tooltip: string } {
  if (stage === '') {
    return { mode: 'idle', tooltip: 'Open Music Library in Finder/Explorer' }
  }
  const name = album.trim()
  if (stage === TAGGING_STAGE) {
    return { mode: 'tagging', tooltip: name ? `Tagging ${name}…` : 'Tagging…' }
  }
  return {
    mode: 'copying',
    tooltip: name ? `Copying ${name} to Library…` : 'Copying to Library…'
  }
}

export function PipelineIndicator(): React.JSX.Element {
  const [stage, setStage] = useState('')
  const [album, setAlbum] = useState('')
  const libraryPath = useStore((s) => s.configuredLibraryPath)
  const tooltip = useTooltip()

  useEffect(() => {
    return window.api.pipeline.onStage((s, a) => {
      setStage(s)
      // Reset the album on the terminal empty stage so a stale label can't leak
      // into the next run's early "Extracting" tooltip.
      setAlbum(s === '' ? '' : a)
    })
  }, [])

  const { mode, tooltip: tip } = deriveState(stage, album)

  if (mode === 'idle') {
    // Idle is an actionable button: click opens the Music Library folder. Disabled
    // (non-interactive) until a library path is configured (fresh onboarding).
    return (
      <button
        type="button"
        className="pipeline-indicator pipeline-indicator--idle"
        disabled={!libraryPath}
        onClick={() => {
          if (libraryPath) window.api.openPath(libraryPath)
        }}
        aria-label={tip}
        {...tooltip(tip)}
      >
        {/* Folder */}
        <svg viewBox="0 0 20 20" width="20" height="20" fill="currentColor" aria-hidden="true">
          <path d="M2 5.5A1.5 1.5 0 0 1 3.5 4h3.3a1.5 1.5 0 0 1 1.06.44l1 1a1.5 1.5 0 0 0 1.06.44H16.5A1.5 1.5 0 0 1 18 7.5v7A1.5 1.5 0 0 1 16.5 16h-13A1.5 1.5 0 0 1 2 14.5v-9Z" />
        </svg>
      </button>
    )
  }

  const className =
    mode === 'tagging'
      ? 'pipeline-indicator pipeline-indicator--active pipeline-indicator--tagging'
      : 'pipeline-indicator pipeline-indicator--active pipeline-indicator--copying'

  return (
    <div className={className} aria-label={tip} {...tooltip(tip)}>
      {mode === 'tagging' ? (
        // Tag
        <svg viewBox="0 0 20 20" width="20" height="20" fill="currentColor" aria-hidden="true">
          <path d="M3 3.8A.8.8 0 0 1 3.8 3h5.13a1.5 1.5 0 0 1 1.06.44l6.07 6.07a1.5 1.5 0 0 1 0 2.12l-4.5 4.5a1.5 1.5 0 0 1-2.12 0L3.44 10.06A1.5 1.5 0 0 1 3 9V3.8Zm3.25 3.45a1.25 1.25 0 1 0 0-2.5 1.25 1.25 0 0 0 0 2.5Z" />
        </svg>
      ) : (
        // Two arrows chasing each other (copy / in-progress)
        <svg viewBox="0 0 20 20" width="20" height="20" fill="none" aria-hidden="true">
          <path
            d="M16 10a6 6 0 0 1-10.6 3.8"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
          <path
            d="M4 10a6 6 0 0 1 10.6-3.8"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
          <path
            d="M14.8 3.4l.2 3-3-.4"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M5.2 16.6l-.2-3 3 .4"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </div>
  )
}
