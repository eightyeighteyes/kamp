// KAMP-561: album-art color extraction for the Now Playing ambient bokeh.
//
// extractPalette is a PURE function (no DOM/canvas) so the tricky quantize + clamp
// logic is reason-about-able in isolation. loadPalette does the taint-safe I/O:
// fetch -> blob -> createImageBitmap -> a tiny offscreen canvas -> getImageData.
// The blob bytes are local, so the derived canvas is never tainted (unlike drawing
// the cross-origin <img> element directly, which would throw on getImageData).

export interface RGB {
  r: number
  g: number
  b: number
}

export interface Palette {
  // Fixed-length, clamped to a pleasant band so dark/monochrome covers still work.
  colors: RGB[]
  source: 'art' | 'accent'
}

// Tunables (see plan "5 knobs"). Conservative midpoints; adjust by eye.
export const PALETTE_SIZE = 5
const SAT_FLOOR = 0.45
const LIGHT_MIN = 0.45
const LIGHT_MAX = 0.68
const HUE_BINS = 16
// A cover whose dominant colorful hue is below this saturation is "monochrome".
const MONO_SAT_THRESHOLD = 0.12
const EXTRACT_SIZE = 32

// --- color-space helpers ----------------------------------------------------

interface HSL {
  h: number // [0,360)
  s: number // [0,1]
  l: number // [0,1]
}

function rgbToHsl({ r, g, b }: RGB): HSL {
  const rn = r / 255
  const gn = g / 255
  const bn = b / 255
  const max = Math.max(rn, gn, bn)
  const min = Math.min(rn, gn, bn)
  const l = (max + min) / 2
  const d = max - min
  if (d === 0) return { h: 0, s: 0, l }
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h: number
  if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) * 60
  else if (max === gn) h = ((bn - rn) / d + 2) * 60
  else h = ((rn - gn) / d + 4) * 60
  return { h, s, l }
}

function hslToRgb({ h, s, l }: HSL): RGB {
  const c = (1 - Math.abs(2 * l - 1)) * s
  const hp = (((h % 360) + 360) % 360) / 60
  const x = c * (1 - Math.abs((hp % 2) - 1))
  let r = 0
  let g = 0
  let b = 0
  if (hp < 1) [r, g, b] = [c, x, 0]
  else if (hp < 2) [r, g, b] = [x, c, 0]
  else if (hp < 3) [r, g, b] = [0, c, x]
  else if (hp < 4) [r, g, b] = [0, x, c]
  else if (hp < 5) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]
  const m = l - c / 2
  return {
    r: Math.round((r + m) * 255),
    g: Math.round((g + m) * 255),
    b: Math.round((b + m) * 255)
  }
}

export function hexToRgb(hex: string): RGB | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  const n = parseInt(m[1], 16)
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
}

// Clamp a swatch into the pleasant band (keep the album's hue; fix its tone).
function clampSwatch(rgb: RGB): RGB {
  const hsl = rgbToHsl(rgb)
  return hslToRgb({
    h: hsl.h,
    s: Math.max(SAT_FLOOR, hsl.s),
    l: Math.min(LIGHT_MAX, Math.max(LIGHT_MIN, hsl.l))
  })
}

// Pad/truncate to a fixed length by hue-rotating the existing swatches, so the
// render loop never branches on palette length.
function padPalette(colors: RGB[]): RGB[] {
  if (colors.length === 0) return []
  const out = colors.slice(0, PALETTE_SIZE)
  const rotations = [30, -30, 60, -60, 90]
  let i = 0
  while (out.length < PALETTE_SIZE) {
    const base = rgbToHsl(colors[i % colors.length])
    out.push(hslToRgb({ ...base, h: base.h + rotations[i % rotations.length] }))
    i++
  }
  return out
}

// A palette built from the theme accent — the fallback for monochrome covers,
// missing art, or a failed decode. Always a valid, on-theme result.
export function accentPalette(accentHex: string): Palette {
  const base = hexToRgb(accentHex) ?? { r: 124, g: 134, b: 225 }
  const hsl = rgbToHsl(base)
  const rotations = [0, 28, -28, 56, -56]
  const colors = rotations.map((dh) => clampSwatch(hslToRgb({ h: hsl.h + dh, s: hsl.s, l: hsl.l })))
  return { colors, source: 'accent' }
}

/**
 * Extract a small, clamped palette from decoded image pixels (PURE — KAMP-561).
 *
 * Weighted-histogram quantize: each pixel is binned by hue and weighted by
 * `saturation × mid-lightness`, so a mostly-black cover surfaces its one neon
 * accent rather than the dominant black. Near-monochrome covers fall back to the
 * theme accent. Always returns a fixed-length, band-clamped palette.
 */
export function extractPalette(data: Uint8ClampedArray, accentHex: string): Palette {
  const binWeight = new Float64Array(HUE_BINS)
  const binR = new Float64Array(HUE_BINS)
  const binG = new Float64Array(HUE_BINS)
  const binB = new Float64Array(HUE_BINS)

  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3]
    if (a < 128) continue
    const rgb = { r: data[i], g: data[i + 1], b: data[i + 2] }
    const { h, s, l } = rgbToHsl(rgb)
    // Prefer colorful, mid-lightness pixels; grey/very dark/blown pixels ~= 0 weight.
    const w = s * (1 - Math.abs(l - 0.5) * 2)
    if (w <= 0) continue
    const bin = Math.min(HUE_BINS - 1, Math.floor((h / 360) * HUE_BINS))
    binWeight[bin] += w
    binR[bin] += rgb.r * w
    binG[bin] += rgb.g * w
    binB[bin] += rgb.b * w
  }

  // Rank bins by weight.
  const ranked = Array.from({ length: HUE_BINS }, (_, i) => i)
    .filter((i) => binWeight[i] > 0)
    .sort((a, b) => binWeight[b] - binWeight[a])

  if (ranked.length === 0) return accentPalette(accentHex)

  // Average color of each surviving bin, most-dominant first.
  const avgs: RGB[] = ranked.map((i) => ({
    r: Math.round(binR[i] / binWeight[i]),
    g: Math.round(binG[i] / binWeight[i]),
    b: Math.round(binB[i] / binWeight[i])
  }))

  // Monochrome guard: if even the dominant hue is nearly desaturated, the cover is
  // effectively grey — a band-clamp would invent random colors, so use the accent.
  if (rgbToHsl(avgs[0]).s < MONO_SAT_THRESHOLD) return accentPalette(accentHex)

  const colors = padPalette(avgs.slice(0, PALETTE_SIZE).map(clampSwatch))
  return { colors, source: 'art' }
}

// --- taint-safe loader ------------------------------------------------------

// One reused offscreen canvas across track changes (no per-load allocation).
let _extractCanvas: HTMLCanvasElement | null = null
function extractContext(): CanvasRenderingContext2D | null {
  if (!_extractCanvas) {
    _extractCanvas = document.createElement('canvas')
    _extractCanvas.width = EXTRACT_SIZE
    _extractCanvas.height = EXTRACT_SIZE
  }
  return _extractCanvas.getContext('2d', { willReadFrequently: true })
}

/**
 * Load an album-art URL and derive its palette (KAMP-561). Taint-safe via
 * fetch->blob->createImageBitmap. Any failure (missing art, non-image body, abort)
 * degrades to the accent palette rather than throwing.
 */
export async function loadPalette(
  url: string,
  accentHex: string,
  signal?: AbortSignal
): Promise<Palette> {
  try {
    const res = await fetch(url, { signal })
    if (!res.ok) return accentPalette(accentHex)
    const blob = await res.blob()
    const bitmap = await createImageBitmap(blob)
    const ctx = extractContext()
    if (!ctx) {
      bitmap.close()
      return accentPalette(accentHex)
    }
    ctx.clearRect(0, 0, EXTRACT_SIZE, EXTRACT_SIZE)
    ctx.drawImage(bitmap, 0, 0, EXTRACT_SIZE, EXTRACT_SIZE)
    bitmap.close()
    const { data } = ctx.getImageData(0, 0, EXTRACT_SIZE, EXTRACT_SIZE)
    return extractPalette(data, accentHex)
  } catch {
    // AbortError / decode failure / missing art — all degrade to the accent.
    return accentPalette(accentHex)
  }
}
