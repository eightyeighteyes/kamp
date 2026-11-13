// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  HIGHLIGHT_PICKER_ORDER,
  HIGHLIGHT_STYLES,
  highlightChoice,
  resolveHighlightStyle
} from './newArrivalHighlight.ts'

test('a stored known style survives a reload', () => {
  for (const style of HIGHLIGHT_STYLES) assert.equal(resolveHighlightStyle(style), style)
})

test('a fresh profile defaults to shiny', () => {
  assert.equal(resolveHighlightStyle(null), 'shiny')
})

test('an unknown stored style (removed, or from a newer build) falls back to shiny', () => {
  assert.equal(resolveHighlightStyle('foo'), 'shiny')
})

test("'off' is never a style: stored there it falls back to shiny (KAMP-770)", () => {
  // Off lives only in the enabled key; an older build re-ticking the box with a
  // style of 'off' would emit a class with no CSS and silently show nothing.
  assert.equal(resolveHighlightStyle('off'), 'shiny')
})

test('disabled reads as off whatever the remembered style', () => {
  for (const style of HIGHLIGHT_STYLES) assert.equal(highlightChoice(false, style), 'off')
})

test('enabled reads as the stored style', () => {
  assert.equal(highlightChoice(true, 'vaporwave'), 'vaporwave')
})

test('the picker lists off first, then every style alphabetically (KAMP-770)', () => {
  assert.deepEqual(HIGHLIGHT_PICKER_ORDER, [
    'off',
    'boring',
    'newmoji',
    'pressed',
    'proud',
    'shiny',
    'static',
    'vaporwave'
  ])
})
