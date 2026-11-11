import React from 'react'
import '../assets/style-rail.css'
import { useStore } from '../store'
import { ThemePicker } from './ThemePicker'
import { NOW_PLAYING_ANIMATIONS } from './nowplaying/registry'
import { NOW_PLAYING_ANIMATION_IDS } from '../../../shared/nowPlayingAnimation'
import type { NowPlayingAnimationId } from '../../../shared/nowPlayingAnimation'

const HIGHLIGHT_STYLES = ['shiny', 'newmoji', 'vaporwave', 'proud', 'pressed', 'boring', 'static']

export function StyleRail(): React.JSX.Element | null {
  const styleRailVisible = useStore((s) => s.styleRailVisible)
  const highlightEnabled = useStore((s) => s.highlightEnabled)
  const highlightStyle = useStore((s) => s.highlightStyle)
  const setHighlightEnabled = useStore((s) => s.setHighlightEnabled)
  const setHighlightStyle = useStore((s) => s.setHighlightStyle)
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
      <label className="style-rail-control">
        Now Playing animation
        <select
          value={nowPlayingAnimation}
          // Options come only from NOW_PLAYING_ANIMATION_IDS, so the cast is sound.
          onChange={(e) => setNowPlayingAnimation(e.target.value as NowPlayingAnimationId)}
        >
          {NOW_PLAYING_ANIMATION_IDS.map((id) => (
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
