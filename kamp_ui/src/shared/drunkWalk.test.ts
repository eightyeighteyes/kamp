import { test } from 'node:test'
import assert from 'node:assert/strict'
import { DrunkWalk } from './drunkWalk.ts'
import { seededRng } from './particleUtil.ts'

const DT = 1 / 15 // Chapel's frame cap

function trace(seed: number, seconds: number, max = 6): number[] {
  const w = new DrunkWalk(seededRng(seed), max)
  const out: number[] = []
  for (let t = 0; t < seconds; t += DT) {
    w.tick(DT)
    out.push(w.value)
  }
  return out
}

test('it starts at the centre', () => {
  assert.equal(new DrunkWalk(seededRng(1), 6).value, 0)
})

test('it never leaves its bound', () => {
  for (let s = 1; s <= 20; s++) {
    for (const v of trace(s, 600)) assert.ok(Math.abs(v) <= 6, `seed ${s}: ${v}`)
  }
})

test('it actually wanders: over ten minutes it reaches a third of its bound on both sides', () => {
  for (let s = 1; s <= 10; s++) {
    const v = trace(s, 600)
    assert.ok(
      Math.max(...v) > 2 && Math.min(...v) < -2,
      `seed ${s}: ${Math.min(...v)}..${Math.max(...v)}`
    )
  }
})

test('it moves slowly and smoothly: no frame jumps more than a sliver of its range', () => {
  const v = trace(3, 600)
  for (let i = 1; i < v.length; i++) {
    assert.ok(Math.abs(v[i] - v[i - 1]) < 6 * 0.02, `jump ${v[i] - v[i - 1]} at ${i}`)
  }
})

test('it is a walk, not a cycle: it changes direction irregularly', () => {
  const v = trace(5, 600)
  let turns = 0
  for (let i = 2; i < v.length; i++) {
    if (Math.sign(v[i] - v[i - 1]) !== Math.sign(v[i - 1] - v[i - 2])) turns++
  }
  assert.ok(turns > 10, `only ${turns} turns`)
})

test('it is drawn back to the centre: more than half the time it is within 60% of its bound', () => {
  // Without the pull, the reflection alone would keep it in bounds but it would
  // spend long stretches pinned at an edge, a tint rather than a wander.
  let near = 0
  let total = 0
  for (let s = 1; s <= 10; s++) {
    for (const v of trace(s, 600)) {
      total++
      if (Math.abs(v) < 6 * 0.6) near++
    }
  }
  assert.ok(near / total > 0.61, `near the centre ${(near / total).toFixed(2)} of the time`)
})

test('the same seed walks the same path', () => {
  assert.deepEqual(trace(9, 30), trace(9, 30))
})

test('a long stall (a stopped loop) is clamped, never one giant step', () => {
  const w = new DrunkWalk(seededRng(2), 6)
  w.tick(30)
  assert.ok(Math.abs(w.value) < 6 * 0.02)
})
