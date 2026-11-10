// KAMP-744: which requests to the local daemon get X-Kamp-Token injected.
//
// Kept free of runtime imports (types only) so `node --test` can load it
// directly with Node's built-in TypeScript type stripping — kamp_ui has no
// other unit-test runner, and this predicate is a security boundary.
//
// Why it exists: the injector used to add the token to EVERY request to
// 127.0.0.1:47483 from ANY frame in the default session.  The community-
// extension iframe lives in that session, so it was silently authenticated
// against the whole API — including the Bandcamp cookie jar.
//
// Rules (do not widen without re-reading KAMP-744):
//   1. The main window's own main frame gets the token.  This is a positive
//      allowlist of webContents the app created, NOT "any top-level frame":
//      a popup opened by a future `allow-popups` sandbox flag would be a top
//      frame too.  Origin/Referer cannot discriminate — the packaged renderer
//      loads from file:// and sends `Origin: null`, exactly like the srcdoc
//      iframe, which also inherits the parent's URL.
//   2. Any frame may load the album-art IMAGE endpoint, matched on the exact
//      pathname.  This keeps the extension SDK's getAlbumArtUrl() <img> working.
//      Never use startsWith/includes: `?x=/api/v1/album-art` on another path
//      would otherwise borrow the token.
//   3. Everything else — subframes, unknown webContents, requests whose frame
//      is gone (null) — reaches the daemon without a token and gets 401.  This
//      fails closed safely: every fetch in the app (main-process net.fetch,
//      preload, renderer client) attaches its own header; only <img> and the
//      WebSockets rely on injection.

export interface FrameIds {
  processId: number
  routingId: number
}

export interface InjectableRequest {
  url: string
  resourceType: string
  webContentsId?: number
  frame?: FrameIds | null
}

const ALBUM_ART_PATH = '/api/v1/album-art'

/**
 * @param trustedMainFrame returns the main-frame ids of a webContents the app
 *   trusts (the main window), or null for any other webContents.
 */
export function shouldInjectKampToken(
  req: InjectableRequest,
  trustedMainFrame: (webContentsId: number) => FrameIds | null
): boolean {
  if (req.resourceType === 'image' && pathnameOf(req.url) === ALBUM_ART_PATH) {
    return true
  }
  if (req.webContentsId === undefined || !req.frame) return false
  const main = trustedMainFrame(req.webContentsId)
  if (!main) return false
  return req.frame.processId === main.processId && req.frame.routingId === main.routingId
}

function pathnameOf(url: string): string | null {
  try {
    return new URL(url).pathname
  } catch {
    return null
  }
}
