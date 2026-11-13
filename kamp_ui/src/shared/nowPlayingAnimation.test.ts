// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  NOW_PLAYING_ANIMATION_IDS,
  legacyGlowValue,
  pickerOrder,
  resolveNowPlayingAnimation
} from './nowPlayingAnimation.ts'
import type { NowPlayingAnimationId } from './nowPlayingAnimation.ts'

// Labels keyed by id; each test overrides only what it needs.
function labels(
  overrides: Partial<Record<NowPlayingAnimationId, string>>
): Record<NowPlayingAnimationId, string> {
  const base = Object.fromEntries(NOW_PLAYING_ANIMATION_IDS.map((id) => [id, id])) as Record<
    NowPlayingAnimationId,
    string
  >
  return { ...base, ...overrides }
}

test('the picker pins none first even when its label sorts last (KAMP-783)', () => {
  assert.equal(pickerOrder(labels({ none: 'zzz' }))[0], 'none')
})

test('the picker sorts the rest by label, not by id or registration order', () => {
  const order = pickerOrder(labels({ castalia: 'aaa', bokeh: 'zzz' }))
  assert.equal(order[1], 'castalia')
  assert.equal(order[order.length - 1], 'bokeh')
})

test('the picker collates accents as their base letter (ä sorts as a)', () => {
  // Codepoint order puts 'ä' (U+00E4) after 'z', so a plain `<` sort would
  // place 'äb' last; collation places it between 'aa' and 'az'.
  const order = pickerOrder(labels({ bokeh: 'az', chapel: 'äb', halation: 'aa' }))
  assert.deepEqual(order.slice(1, 4), ['halation', 'chapel', 'bokeh'])
})

test('the picker lists every animation exactly once', () => {
  const order = pickerOrder(labels({}))
  assert.equal(order.length, NOW_PLAYING_ANIMATION_IDS.length)
  assert.deepEqual([...order].sort(), [...NOW_PLAYING_ANIMATION_IDS].sort())
})

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

test('halation is a known id that survives a reload (KAMP-774)', () => {
  assert.equal(resolveNowPlayingAnimation('halation', 'false'), 'halation')
  assert.equal(legacyGlowValue('halation'), 'true')
})

test('borealis is a known id that survives a reload (KAMP-765)', () => {
  assert.equal(resolveNowPlayingAnimation('borealis', 'false'), 'borealis')
  assert.equal(legacyGlowValue('borealis'), 'true')
})

test('tannhauser is a known id that survives a reload (KAMP-772)', () => {
  assert.equal(resolveNowPlayingAnimation('tannhauser', 'false'), 'tannhauser')
  assert.equal(legacyGlowValue('tannhauser'), 'true')
})

test('sparks is a known id that survives a reload (KAMP-767)', () => {
  assert.equal(resolveNowPlayingAnimation('sparks', 'false'), 'sparks')
  assert.equal(legacyGlowValue('sparks'), 'true')
})

test('wintermute is a known id that survives a reload (KAMP-768)', () => {
  assert.equal(resolveNowPlayingAnimation('wintermute', 'false'), 'wintermute')
  assert.equal(legacyGlowValue('wintermute'), 'true')
})

test('castalia is a known id that survives a reload (KAMP-773)', () => {
  assert.equal(resolveNowPlayingAnimation('castalia', 'false'), 'castalia')
  assert.equal(legacyGlowValue('castalia'), 'true')
})
