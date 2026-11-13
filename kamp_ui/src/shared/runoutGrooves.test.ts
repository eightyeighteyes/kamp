import { test } from 'node:test'
import assert from 'node:assert/strict'
import { GROOVES } from './runoutModel.ts'
import { LEAD_AT, cutAlpha, grooveLevel, lockS, spiralS } from './runoutGrooves.ts'

test('without the runout the grooves are evenly spaced', () => {
  for (let x = 0; x <= GROOVES; x += 0.5) assert.equal(spiralS(x, 0), x)
})

test('the runout widens only the lead-out, past LEAD_AT', () => {
  const lead = LEAD_AT * GROOVES
  assert.equal(spiralS(lead - 1, 1), lead - 1)
  assert.ok(spiralS(lead + 2, 1) - spiralS(lead + 1, 1) > 2, 'wider than one groove')
})

test('the widening is continuous in the envelope (no jump as it eases)', () => {
  const x = LEAD_AT * GROOVES + 5
  for (let e = 0; e < 1; e += 0.01) {
    assert.ok(Math.abs(spiralS(x, e + 0.01) - spiralS(x, e)) < 0.5)
  }
})

test('the spiral is continuous and increasing along the record', () => {
  for (const e of [0, 0.5, 1]) {
    for (let x = 0; x < GROOVES; x += 0.1) {
      const d = spiralS(x + 0.1, e) - spiralS(x, e)
      assert.ok(d > 0 && d < 0.5, `e ${e} x ${x}`)
    }
  }
})

test('nothing is cut without the runout', () => {
  for (let s = 0; s <= GROOVES; s++) assert.equal(cutAlpha(s, 0), 1)
})

test('locked, the grooves end at the lock groove and the runout past it is blank', () => {
  assert.equal(cutAlpha(lockS(1) - 2, 1), 1)
  assert.equal(cutAlpha(lockS(1) + 2, 1), 0)
})

test('the cut fades, never steps (KAMP-774 stairs)', () => {
  for (let s = 0; s < GROOVES; s += 0.05) {
    assert.ok(Math.abs(cutAlpha(s + 0.05, 1) - cutAlpha(s, 1)) < 0.2)
  }
})

test('the groove level stays in 0..1', () => {
  for (let x = -2; x < GROOVES + 2; x += 0.013) {
    const v = grooveLevel(12345, x)
    assert.ok(v >= 0 && v <= 1, `x ${x}: ${v}`)
  }
})

test('the groove level is continuous along the spiral, across turns too', () => {
  for (let x = 0; x < GROOVES; x += 0.001) {
    assert.ok(Math.abs(grooveLevel(7, x + 0.001) - grooveLevel(7, x)) < 0.2)
  }
})

test('adjacent turns differ: the pattern does not line up into radial stripes', () => {
  let diff = 0
  for (let x = 0; x < 1; x += 0.05) diff += Math.abs(grooveLevel(7, x) - grooveLevel(7, x + 1))
  assert.ok(diff / 20 > 0.05)
})

test('each album has its own pattern; the same album always the same', () => {
  assert.equal(grooveLevel(7, 3.3), grooveLevel(7, 3.3))
  let diff = 0
  for (let x = 0; x < 2; x += 0.05) diff += Math.abs(grooveLevel(7, x) - grooveLevel(8, x))
  assert.ok(diff / 40 > 0.05)
})
