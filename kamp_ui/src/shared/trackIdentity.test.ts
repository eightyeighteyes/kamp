import { test } from 'node:test'
import assert from 'node:assert/strict'
import { albumKeyOf, trackKeyOf } from './trackIdentity.ts'

test('tracks of one album share its key', () => {
  assert.equal(albumKeyOf({ id: 1, album_id: 7 }), albumKeyOf({ id: 2, album_id: 7 }))
})

test('different albums have different keys', () => {
  assert.notEqual(albumKeyOf({ id: 1, album_id: 7 }), albumKeyOf({ id: 1, album_id: 8 }))
})

test('a missing-album track is its own album, never shared with another', () => {
  const a = albumKeyOf({ id: 1, album_id: null })
  assert.notEqual(a, albumKeyOf({ id: 2, album_id: null }))
  // ...and never collides with a real album row of the same number.
  assert.notEqual(a, albumKeyOf({ id: 99, album_id: 1 }))
})

test('the track key is stable for a track', () => {
  assert.equal(trackKeyOf({ id: 5, album_id: 1 }), trackKeyOf({ id: 5, album_id: null }))
})
