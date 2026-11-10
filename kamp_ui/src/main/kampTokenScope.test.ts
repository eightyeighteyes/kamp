// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { shouldInjectKampToken } from './kampTokenScope.ts'
import type { FrameIds, InjectableRequest } from './kampTokenScope.ts'

const MAIN_WC = 1
const MAIN_FRAME: FrameIds = { processId: 10, routingId: 1 }
const IFRAME: FrameIds = { processId: 10, routingId: 7 }
const API = 'http://127.0.0.1:47483'

const trusted = (id: number): FrameIds | null => (id === MAIN_WC ? MAIN_FRAME : null)

function req(overrides: Partial<InjectableRequest>): InjectableRequest {
  return {
    url: `${API}/api/v1/albums`,
    resourceType: 'xhr',
    webContentsId: MAIN_WC,
    frame: MAIN_FRAME,
    ...overrides
  }
}

test('main window main frame gets the token', () => {
  assert.equal(shouldInjectKampToken(req({}), trusted), true)
})

test('main window WebSocket upgrade gets the token', () => {
  const r = req({ url: 'ws://127.0.0.1:47483/api/v1/ws', resourceType: 'webSocket' })
  assert.equal(shouldInjectKampToken(r, trusted), true)
})

test('extension iframe (subframe of the main window) does not', () => {
  const r = req({ url: `${API}/api/v1/bandcamp/session-cookies`, frame: IFRAME })
  assert.equal(shouldInjectKampToken(r, trusted), false)
})

test('extension iframe WebSocket does not', () => {
  const r = req({ url: 'ws://127.0.0.1:47483/api/v1/ws', resourceType: 'webSocket', frame: IFRAME })
  assert.equal(shouldInjectKampToken(r, trusted), false)
})

test('null frame fails closed', () => {
  assert.equal(shouldInjectKampToken(req({ frame: null }), trusted), false)
})

test('missing webContentsId fails closed', () => {
  assert.equal(shouldInjectKampToken(req({ webContentsId: undefined }), trusted), false)
})

test('another webContents, even its top frame, does not', () => {
  const r = req({ webContentsId: 2, frame: MAIN_FRAME })
  assert.equal(shouldInjectKampToken(r, trusted), false)
})

test('album-art image from the extension iframe gets the token', () => {
  const r = req({
    url: `${API}/api/v1/album-art?album_artist=A&album=B`,
    resourceType: 'image',
    frame: IFRAME
  })
  assert.equal(shouldInjectKampToken(r, trusted), true)
})

test('album-art image with a null frame still gets the token', () => {
  const r = req({ url: `${API}/api/v1/album-art?a=1`, resourceType: 'image', frame: null })
  assert.equal(shouldInjectKampToken(r, trusted), true)
})

test('image to another endpoint from the iframe does not', () => {
  const r = req({ url: `${API}/api/v1/config`, resourceType: 'image', frame: IFRAME })
  assert.equal(shouldInjectKampToken(r, trusted), false)
})

test('album-art path only in the query string does not count', () => {
  const r = req({
    url: `${API}/api/v1/bandcamp/session-cookies?x=/api/v1/album-art`,
    resourceType: 'image',
    frame: IFRAME
  })
  assert.equal(shouldInjectKampToken(r, trusted), false)
})

test('album-art path prefix does not count', () => {
  const r = req({ url: `${API}/api/v1/album-art-evil`, resourceType: 'image', frame: IFRAME })
  assert.equal(shouldInjectKampToken(r, trusted), false)
})

test('non-image request to album-art from the iframe does not', () => {
  const r = req({ url: `${API}/api/v1/album-art?a=1`, resourceType: 'xhr', frame: IFRAME })
  assert.equal(shouldInjectKampToken(r, trusted), false)
})

test('unparseable URL does not throw', () => {
  const r = req({ url: 'not a url', resourceType: 'image', frame: IFRAME })
  assert.equal(shouldInjectKampToken(r, trusted), false)
})
