// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { describeLoginCompleteFailure } from './bandcampLoginError.ts'

const STORAGE_MESSAGE =
  "Kamp couldn't store your Bandcamp login securely (the system credential store was unavailable), so it was not saved."

test('uses the daemon detail verbatim (KAMP-750 storage failure)', () => {
  const body = JSON.stringify({ detail: STORAGE_MESSAGE })
  assert.equal(describeLoginCompleteFailure(422, body), STORAGE_MESSAGE)
})

test('falls back to the status when the body has no string detail', () => {
  assert.equal(describeLoginCompleteFailure(503, '{}'), 'login-complete returned 503')
  assert.equal(
    describeLoginCompleteFailure(422, JSON.stringify({ detail: [{ msg: 'x' }] })),
    'login-complete returned 422'
  )
})

test('falls back to the status when the body is not JSON', () => {
  assert.equal(describeLoginCompleteFailure(500, '<html>oops'), 'login-complete returned 500')
})

test('ignores an empty or whitespace-only detail', () => {
  assert.equal(
    describeLoginCompleteFailure(422, JSON.stringify({ detail: '   ' })),
    'login-complete returned 422'
  )
})

test('caps a pathologically long detail', () => {
  const detail = 'x'.repeat(5000)
  const out = describeLoginCompleteFailure(422, JSON.stringify({ detail }))
  assert.ok(out.length <= 500, `length ${out.length}`)
})
