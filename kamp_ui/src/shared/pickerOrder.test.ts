// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { offFirstAlphabetical } from './pickerOrder.ts'

const IDS = ['b', 'off', 'a', 'c'] as const
type Id = (typeof IDS)[number]
const identity = Object.fromEntries(IDS.map((id) => [id, id])) as Record<Id, string>

test('the off entry is pinned first even when its label sorts last (KAMP-770)', () => {
  const order = offFirstAlphabetical(IDS, 'off', { ...identity, off: 'zzz' })
  assert.equal(order[0], 'off')
})

test('the rest are sorted by label, not by id or input order', () => {
  const order = offFirstAlphabetical(IDS, 'off', { ...identity, c: 'aaa', a: 'zzz' })
  assert.deepEqual(order, ['off', 'c', 'b', 'a'])
})

test('accents collate as their base letter (ä sorts as a)', () => {
  // Codepoint order puts 'ä' after 'z'; collation places 'äb' between 'aa' and 'az'.
  const order = offFirstAlphabetical(IDS, 'off', { ...identity, a: 'az', b: 'äb', c: 'aa' })
  assert.deepEqual(order, ['off', 'c', 'b', 'a'])
})

test('every id is listed exactly once', () => {
  const order = offFirstAlphabetical(IDS, 'off', identity)
  assert.deepEqual([...order].sort(), [...IDS].sort())
})

test('the input list is not mutated', () => {
  const ids: Id[] = ['b', 'off', 'a', 'c']
  offFirstAlphabetical(ids, 'off', identity)
  assert.deepEqual(ids, ['b', 'off', 'a', 'c'])
})
