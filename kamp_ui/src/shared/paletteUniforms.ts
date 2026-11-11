// KAMP-780: pack the album palette into the flat Float32Array a GLSL
// `uniform vec3 uPalette[5]` expects (0..1 channels). The crossfade happens on
// the CPU before packing (paletteCrossfade.ts), so the shader holds one array.
//
// Structurally typed (not imported from the renderer's palette.ts) so this
// stays runnable under `node --test` with no renderer deps.

export interface Rgb {
  r: number
  g: number
  b: number
}

export const PALETTE_SLOTS = 5
const NEUTRAL: Rgb = { r: 64, g: 64, b: 72 }

/**
 * Pack `colors` into PALETTE_SLOTS vec3s. An empty palette falls back to
 * `fallback` (the theme accent palette), then to a neutral grey, so the uniform
 * never receives undefined/NaN. Short palettes repeat cyclically.
 *
 * Pass `out` to fill a reused buffer: the engine packs every frame, and a fresh
 * allocation per frame is needless GC churn in a loop meant to idle cheaply.
 */
export function packPalette(
  colors: Rgb[],
  fallback: Rgb[],
  out: Float32Array = new Float32Array(PALETTE_SLOTS * 3)
): Float32Array {
  const src = colors.length > 0 ? colors : fallback.length > 0 ? fallback : [NEUTRAL]
  for (let i = 0; i < PALETTE_SLOTS; i++) {
    const c = src[i % src.length]
    out[i * 3] = c.r / 255
    out[i * 3 + 1] = c.g / 255
    out[i * 3 + 2] = c.b / 255
  }
  return out
}
