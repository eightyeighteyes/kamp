// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
// KAMP-776 spike: packing the album palette into a vec3[5] uniform.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { packPalette } from './paletteUniforms.ts'

const RED = { r: 255, g: 0, b: 0 }
const BLUE = { r: 0, g: 0, b: 255 }

test('an empty palette falls back to the accent palette instead of uploading NaN', () => {
  // padPalette (palette.ts) returns [] when no swatches are found.
  const out = packPalette([], [BLUE])
  assert.equal(out.length, 15)
  assert.ok(out.every((v) => Number.isFinite(v)))
  assert.deepEqual(Array.from(out.slice(0, 3)), [0, 0, 1])
})

test('a short palette is padded to five slots by repeating its colors', () => {
  const out = packPalette([RED, BLUE], [])
  assert.deepEqual(Array.from(out), [1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0])
})

test('with nothing at all to draw from, slots are a neutral grey, never NaN', () => {
  const out = packPalette([], [])
  assert.equal(out.length, 15)
  assert.ok(out.every((v) => Number.isFinite(v) && v > 0))
})
