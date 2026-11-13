import { test } from 'node:test'
import assert from 'node:assert/strict'

import { alphaScale, bakeKey, drawHalf, orbSprite } from './bokehSprites.ts'
import { blurPad } from './particleUtil.ts'

test('blurPad covers three sigma, and nothing when there is no blur', () => {
  assert.equal(blurPad(0), 0)
  assert.equal(blurPad(-4), 0)
  assert.ok(blurPad(7) >= 21)
  assert.ok(blurPad(2.5) >= 7.5)
})

test('orbSprite works in backing px: radius and sigma both follow the backing scale', () => {
  // The CSS blur is a sigma in CSS px. A sprite baked at backing resolution must
  // carry sigma x scale, or the blur shrinks relative to the orb (KAMP-767).
  const a = orbSprite(200, 28, 1)
  const b = orbSprite(200, 28, 0.25)
  assert.equal(a.radius, 200)
  assert.equal(a.sigma, 28)
  assert.equal(b.radius, 50)
  assert.equal(b.sigma, 7)
})

test('orbSprite pads the orb by three sigma so the blurred tail is never clipped', () => {
  for (const [r, blur, scale] of [
    [200, 28, 1],
    [30, 28, 0.25],
    [1, 28, 0.125],
    [120, 0, 0.5]
  ]) {
    const sp = orbSprite(r, blur, scale)
    assert.ok(sp.half >= sp.radius + 3 * sp.sigma, `half ${sp.half} for r=${r} blur=${blur}`)
    assert.ok(Number.isInteger(sp.half) && sp.half >= 1)
  }
})

test('drawHalf grows and shrinks the sprite with the orb radius (breath, bloom)', () => {
  const sp = orbSprite(100, 28, 0.5) // baked at 100 CSS px
  const base = drawHalf(sp, 100, 0.5)
  assert.equal(base, sp.half / 0.5) // CSS px: the engine draws in CSS space
  assert.ok(Math.abs(drawHalf(sp, 116, 0.5) - base * 1.16) < 1e-9)
  assert.ok(Math.abs(drawHalf(sp, 92, 0.5) - base * 0.92) < 1e-9)
})

test('alphaScale maps the frame alpha onto a sprite baked at its peak, never above 1', () => {
  assert.equal(alphaScale(0.4, 0.4), 1)
  assert.ok(Math.abs(alphaScale(0.2, 0.4) - 0.5) < 1e-12)
  assert.equal(alphaScale(0.5, 0.4), 1) // overshoot clamps; globalAlpha can't exceed 1
  assert.equal(alphaScale(-0.1, 0.4), 0)
  assert.equal(alphaScale(0.3, 0), 0)
})

test('bakeKey changes with pane size, backing scale and blur, and ignores sub-pixel jitter', () => {
  const k = bakeKey(800, 0.5, 28)
  assert.notEqual(bakeKey(900, 0.5, 28), k)
  assert.notEqual(bakeKey(800, 0.25, 28), k)
  assert.notEqual(bakeKey(800, 0.5, 0), k)
  // A fractional layout size that rounds to the same backing px must not re-bake.
  assert.equal(bakeKey(800.4, 0.5, 28), k)
})
