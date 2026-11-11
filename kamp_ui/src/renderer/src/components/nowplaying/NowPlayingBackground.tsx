// KAMP-766: mounts the selected Now Playing animation from the registry.

import React from 'react'
import { useStore } from '../../store'
import { NOW_PLAYING_ANIMATIONS } from './registry'
import type { NowPlayingEngineProps } from './registry'

export function NowPlayingBackground(props: NowPlayingEngineProps): React.JSX.Element | null {
  const id = useStore((s) => s.nowPlayingAnimation)
  const Engine = NOW_PLAYING_ANIMATIONS[id].Engine
  if (!Engine) return null
  // key={id}: a switch must fully unmount the old engine (stopping its loop)
  // rather than let React reuse the slot.
  return <Engine key={id} {...props} />
}
