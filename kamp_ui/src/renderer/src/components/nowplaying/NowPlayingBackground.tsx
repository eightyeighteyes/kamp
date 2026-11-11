// KAMP-766: mounts the selected Now Playing animation from the registry.

import React from 'react'
import { useStore } from '../../store'
import { NOW_PLAYING_ANIMATIONS, NOW_PLAYING_OVERRIDES } from './registry'
import type { NowPlayingEngineProps } from './registry'

export function NowPlayingBackground(props: NowPlayingEngineProps): React.JSX.Element | null {
  const id = useStore((s) => s.nowPlayingAnimation)
  // KAMP-780: a diagnostics override (window.__kampGL) wins over the user's choice.
  const override = useStore((s) => s.nowPlayingOverride)
  const Engine = override ? NOW_PLAYING_OVERRIDES[override] : NOW_PLAYING_ANIMATIONS[id].Engine
  if (!Engine) return null
  // key: a switch must fully unmount the old engine (stopping its loop) rather
  // than let React reuse the slot.
  return <Engine key={override ?? id} {...props} />
}
