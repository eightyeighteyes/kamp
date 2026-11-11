// KAMP-780: the fallback for every shader background when WebGL is unavailable or
// its context is lost for good. A static CSS gradient, deliberately NOT Bokeh:
// with hardware acceleration off, Bokeh's blur(28px) is software-composited and is
// likely the most expensive option there is (KAMP-776).

import React, { useState } from 'react'
import { accentPalette, type Palette } from '../palette'
import { accentHex, useAlbumPalette } from './themeColors'

// Two dominant swatches over the themed bg; .now-playing-backdrop supplies
// var(--bg) and the KAMP-631 opaque backing.
export function StillBackdrop({ artUrl }: { artUrl: string }): React.JSX.Element {
  const [pal, setPal] = useState<Palette>(() => accentPalette(accentHex()))
  useAlbumPalette(artUrl, setPal)
  const a = pal.colors[0] ?? { r: 40, g: 40, b: 48 }
  const b = pal.colors[1] ?? a
  const style: React.CSSProperties = {
    backgroundImage: `radial-gradient(ellipse at center, transparent 45%, color-mix(in srgb, var(--bg) 35%, transparent) 100%), linear-gradient(135deg, rgba(${a.r},${a.g},${a.b},0.22), rgba(${b.r},${b.g},${b.b},0.22))`
  }
  return <div className="now-playing-backdrop" style={style} aria-hidden="true" />
}
