// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { legacyGlowValue, resolveNowPlayingAnimation } from './nowPlayingAnimation.ts'

test('a stored known id wins over the legacy toggle', () => {
  assert.equal(resolveNowPlayingAnimation('bokeh', 'false'), 'bokeh')
  assert.equal(resolveNowPlayingAnimation('none', 'true'), 'none')
})

test('legacy glow turned off migrates to none', () => {
  assert.equal(resolveNowPlayingAnimation(null, 'false'), 'none')
})

test('legacy glow turned on migrates to bokeh', () => {
  assert.equal(resolveNowPlayingAnimation(null, 'true'), 'bokeh')
})

test('a fresh profile defaults to bokeh, matching the old default-on toggle', () => {
  assert.equal(resolveNowPlayingAnimation(null, null), 'bokeh')
})

test('an unknown stored id (from a newer build) falls back to the legacy toggle', () => {
  assert.equal(resolveNowPlayingAnimation('aurora', 'false'), 'none')
  assert.equal(resolveNowPlayingAnimation('aurora', null), 'bokeh')
})

test('the legacy key is written so an older build still honours None', () => {
  assert.equal(legacyGlowValue('none'), 'false')
  assert.equal(legacyGlowValue('bokeh'), 'true')
})

test('chapel is a known id that survives a reload (KAMP-775)', () => {
  assert.equal(resolveNowPlayingAnimation('chapel', 'false'), 'chapel')
})

test('an older build sees chapel as glow on, not None', () => {
  assert.equal(legacyGlowValue('chapel'), 'true')
})
