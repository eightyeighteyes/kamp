import React from 'react'
import '../assets/style-rail.css'
import { useStore } from '../store'
import { ThemePicker } from './ThemePicker'
import { NOW_PLAYING_ANIMATIONS } from './nowplaying/registry'
import { pickerOrder } from '../../../shared/nowPlayingAnimation'
import type { NowPlayingAnimationId } from '../../../shared/nowPlayingAnimation'
import { HIGHLIGHT_PICKER_ORDER, highlightChoice } from '../../../shared/newArrivalHighlight'
import type { HighlightChoice } from '../../../shared/newArrivalHighlight'

// Labels are static, so the order is computed once.
const ANIMATION_ORDER = pickerOrder(
  Object.fromEntries(
    Object.entries(NOW_PLAYING_ANIMATIONS).map(([id, a]) => [id, a.label])
  ) as Record<NowPlayingAnimationId, string>
)

export function StyleRail(): React.JSX.Element | null {
  const styleRailVisible = useStore((s) => s.styleRailVisible)
  const highlightEnabled = useStore((s) => s.highlightEnabled)
  const highlightStyle = useStore((s) => s.highlightStyle)
  const setNewArrivalHighlight = useStore((s) => s.setNewArrivalHighlight)
  const nowPlayingAnimation = useStore((s) => s.nowPlayingAnimation)
  const setNowPlayingAnimation = useStore((s) => s.setNowPlayingAnimation)
  const nowPlayingArtVisible = useStore((s) => s.nowPlayingArtVisible)
  const setNowPlayingArtVisible = useStore((s) => s.setNowPlayingArtVisible)

  if (!styleRailVisible) return null

  return (
    <div className="style-rail">
      <ThemePicker />
      <div className="style-rail-spacer" />
      <label className="style-rail-control">
        New Arrival Highlight
        <select
          value={highlightChoice(highlightEnabled, highlightStyle)}
          // Options come only from HIGHLIGHT_PICKER_ORDER, so the cast is sound.
          onChange={(e) => setNewArrivalHighlight(e.target.value as HighlightChoice)}
        >
          {HIGHLIGHT_PICKER_ORDER.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <label className="style-rail-control">
        Now Playing Animation
        <select
          value={nowPlayingAnimation}
          // Options come only from ANIMATION_ORDER (animation ids), so the cast is sound.
          onChange={(e) => setNowPlayingAnimation(e.target.value as NowPlayingAnimationId)}
        >
          {ANIMATION_ORDER.map((id) => (
            <option key={id} value={id}>
              {NOW_PLAYING_ANIMATIONS[id].label}
            </option>
          ))}
        </select>
      </label>
      <label className="style-rail-control">
        <input
          type="checkbox"
          checked={nowPlayingArtVisible}
          onChange={(e) => setNowPlayingArtVisible(e.target.checked)}
        />
        Show album art
      </label>
    </div>
  )
}
