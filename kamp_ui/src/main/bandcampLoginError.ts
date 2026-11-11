// Turns a failed POST /api/v1/bandcamp/login-complete into the text the login
// UI shows.  Kept free of Electron imports so `npm test` can run it directly.

/** Long enough for any message the daemon composes, short enough for a card. */
const MAX_DETAIL_LENGTH = 500

/**
 * Prefer the daemon's `detail`, which since KAMP-750 is a fixed, actionable
 * sentence ("Kamp couldn't store your Bandcamp login securely ... Make sure
 * your keychain is unlocked, then try again.") written to be shown verbatim
 * and guaranteed to carry no credential material.  Anything else -- a
 * FastAPI validation array, an HTML error page, an empty body -- falls back to
 * the bare status, because rendering an unknown body risks showing the user
 * something meaningless or unintended.
 */
export function describeLoginCompleteFailure(status: number, bodyText: string): string {
  const fallback = `login-complete returned ${status}`
  let parsed: unknown
  try {
    parsed = JSON.parse(bodyText)
  } catch {
    return fallback
  }
  if (typeof parsed !== 'object' || parsed === null) return fallback
  const detail = (parsed as { detail?: unknown }).detail
  if (typeof detail !== 'string' || detail.trim() === '') return fallback
  return detail.length > MAX_DETAIL_LENGTH ? `${detail.slice(0, MAX_DETAIL_LENGTH - 1)}…` : detail
}
