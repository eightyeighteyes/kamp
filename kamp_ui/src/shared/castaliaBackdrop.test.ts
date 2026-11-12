import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  MAX_BANDS,
  PULSE_PERIOD_S,
  STRENGTH_MAX,
  STRENGTH_MIN,
  backdropStops,
  bandColors,
  pulseStrength
} from './castaliaBackdrop.ts'

const BG = { r: 20, g: 20, b: 20 }
const RED = { r: 200, g: 40, b: 40 }
const GREEN = { r: 40, g: 200, b: 40 }
const BLUE = { r: 40, g: 40, b: 200 }
const GOLD = { r: 220, g: 180, b: 60 }

test('at most three bands, from the dominant end of the palette', () => {
  assert.deepEqual(bandColors([RED, GREEN, BLUE, GOLD], BG), [RED, GREEN, BLUE])
})

test('a short palette repeats its colours so there are always three bands', () => {
  assert.deepEqual(bandColors([RED], BG), [RED, RED, RED])
  assert.deepEqual(bandColors([RED, GREEN], BG), [RED, GREEN, GREEN])
})

test('an empty palette falls back to the given colour, never NaN', () => {
  const bands = bandColors([], GOLD)
  assert.equal(bands.length, MAX_BANDS)
  for (const c of bands) assert.deepEqual(c, GOLD)
})

test('the stops run from the corner out and end exactly on --bg', () => {
  const stops = backdropStops(BG, [RED, GREEN, BLUE], 0.2)
  assert.equal(stops.length, MAX_BANDS + 1)
  for (let i = 1; i < stops.length; i++) assert.ok(stops[i].offset > stops[i - 1].offset)
  assert.equal(stops[0].offset, 0)
  assert.deepEqual(stops.at(-1), { offset: 1, color: BG })
})

test('every band stays subtle: close to --bg, and fainter further from the corner', () => {
  const dist = (c: { r: number; g: number; b: number }): number =>
    Math.hypot(c.r - BG.r, c.g - BG.g, c.b - BG.b)
  const stops = backdropStops(BG, [GOLD, GOLD, GOLD], STRENGTH_MAX)
  const full = dist(GOLD)
  for (const s of stops) assert.ok(dist(s.color) <= full * STRENGTH_MAX + 1e-9)
  for (let i = 1; i < stops.length; i++) {
    assert.ok(dist(stops[i].color) < dist(stops[i - 1].color), `band ${i} is not fainter`)
  }
})

test('the breath stays within its range and returns to where it started each period', () => {
  for (let t = 0; t < PULSE_PERIOD_S * 3; t += 0.05) {
    const s = pulseStrength(t)
    assert.ok(s >= STRENGTH_MIN - 1e-12 && s <= STRENGTH_MAX + 1e-12)
  }
  assert.ok(Math.abs(pulseStrength(0) - pulseStrength(PULSE_PERIOD_S)) < 1e-12)
  // Positive control: it actually moves across the range.
  assert.ok(Math.abs(pulseStrength(PULSE_PERIOD_S / 2) - STRENGTH_MAX) < 1e-12)
  assert.ok(Math.abs(pulseStrength(0) - STRENGTH_MIN) < 1e-12)
})

test('the breath is slow and smooth: no visible step between 60 Hz frames', () => {
  const dt = 1 / 60
  let worst = 0
  for (let t = 0; t < PULSE_PERIOD_S; t += dt) {
    worst = Math.max(worst, Math.abs(pulseStrength(t + dt) - pulseStrength(t)))
  }
  // Peak slope of a raised cosine is π·range/period per second.
  const bound = (Math.PI * (STRENGTH_MAX - STRENGTH_MIN) * dt) / PULSE_PERIOD_S
  assert.ok(worst <= bound + 1e-12, `a frame stepped ${worst}`)
})

test('reduced motion gets a fixed strength in the middle of the range', () => {
  assert.equal(pulseStrength(null), (STRENGTH_MIN + STRENGTH_MAX) / 2)
})
