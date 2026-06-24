import React from 'react'
import '../assets/style-rail.css'
import { useStore } from '../store'

const HIGHLIGHT_STYLES = ['shiny', 'newmoji', 'vaporwave', 'proud', 'pressed', 'boring', 'static']

export function StyleRail(): React.JSX.Element | null {
  const styleRailVisible = useStore((s) => s.styleRailVisible)
  const highlightEnabled = useStore((s) => s.highlightEnabled)
  const highlightStyle = useStore((s) => s.highlightStyle)
  const setHighlightEnabled = useStore((s) => s.setHighlightEnabled)
  const setHighlightStyle = useStore((s) => s.setHighlightStyle)

  if (!styleRailVisible) return null

  return (
    <div className="style-rail">
      <label className="style-rail-control">
        <input
          type="checkbox"
          checked={highlightEnabled}
          onChange={(e) => setHighlightEnabled(e.target.checked)}
        />
        Highlight new arrivals
      </label>
      {highlightEnabled && (
        <label className="style-rail-control">
          Style
          <select value={highlightStyle} onChange={(e) => setHighlightStyle(e.target.value)}>
            {HIGHLIGHT_STYLES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  )
}
