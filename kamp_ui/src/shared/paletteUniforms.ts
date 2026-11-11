// KAMP-776 spike: pack the album palette into the flat Float32Array a GLSL
// `uniform vec3 uPalette[5]` expects (0..1 channels). The crossfade happens on
// the CPU before packing (as bokehEngine does), so the shader holds one array.
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
 * Pack `colors` into PALETTE_SLOTS vec3s. An empty palette (padPalette returns
 * [] when a cover yields no swatches) falls back to `fallback`, then to a
 * neutral grey, so the uniform never receives undefined/NaN. Short palettes
 * repeat cyclically.
 */
export function packPalette(colors: Rgb[], fallback: Rgb[]): Float32Array {
  const src = colors.length > 0 ? colors : fallback.length > 0 ? fallback : [NEUTRAL]
  const out = new Float32Array(PALETTE_SLOTS * 3)
  for (let i = 0; i < PALETTE_SLOTS; i++) {
    const c = src[i % src.length]
    out[i * 3] = c.r / 255
    out[i * 3 + 1] = c.g / 255
    out[i * 3 + 2] = c.b / 255
  }
  return out
}
