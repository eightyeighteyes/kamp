import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isEndOfSide, matrixNumber, playbackOf, type SideTrack } from './runoutPlayback.ts'

const t = (id: number, album_id: number | null, disc_number = 1): SideTrack => ({
  id,
  album_id,
  disc_number
})

test('the last track in the queue ends the side', () => {
  assert.equal(isEndOfSide(t(1, 7), null, false), true)
})

test('a next track on the same album and disc does not end the side', () => {
  assert.equal(isEndOfSide(t(1, 7), t(2, 7), false), false)
})

test('a next track on a different album ends the side', () => {
  assert.equal(isEndOfSide(t(1, 7), t(2, 8), false), true)
})

test('a next track on the next disc of the same album ends the side', () => {
  assert.equal(isEndOfSide(t(1, 7, 1), t(2, 7, 2), false), true)
})

test('untagged tracks are each their own album, so the side ends after each', () => {
  assert.equal(isEndOfSide(t(1, null), t(2, null), false), true)
})

test('shuffle never ends a side: the next track says nothing about the album', () => {
  assert.equal(isEndOfSide(t(1, 7), t(2, 8), true), false)
  assert.equal(isEndOfSide(t(1, 7), null, true), false)
})

test('nothing playing is never the end of a side', () => {
  assert.equal(isEndOfSide(null, null, false), false)
})

test('playbackOf maps the player snapshot and the queue', () => {
  const p = playbackOf(
    { playing: true, position: 30, duration: 200, current_track: t(1, 7), next_track: null },
    { shuffle: false }
  )
  assert.deepEqual(p, { playing: true, position: 30, duration: 200, endOfSide: true, disc: 1 })
})

test('playbackOf treats an unknown queue as not shuffled', () => {
  const p = playbackOf(
    { playing: true, position: 0, duration: 200, current_track: t(1, 7), next_track: null },
    null
  )
  assert.equal(p.endOfSide, true)
})

test('playbackOf reports no end of side while the duration is unknown', () => {
  // A stream still loading reports duration 0; remaining time would read negative.
  const p = playbackOf(
    { playing: true, position: 3, duration: 0, current_track: t(1, 7), next_track: null },
    { shuffle: false }
  )
  assert.equal(p.endOfSide, false)
})

test('the matrix number names the side from the disc and is stable per album', () => {
  assert.match(matrixNumber('album:7', 1), /^A-[1-9]$/)
  assert.match(matrixNumber('album:7', 2), /^B-[1-9]$/)
  assert.equal(matrixNumber('album:7', 1), matrixNumber('album:7', 1))
})

test('a missing or zero disc number reads as side A', () => {
  assert.match(matrixNumber('album:7', 0), /^A-/)
})
