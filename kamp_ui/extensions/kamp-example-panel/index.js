/**
 * kamp-example-panel
 *
 * A first-party extension demonstrating the KampAPI panel registration system.
 * This module is a plain ES module — it has no access to Node.js, Electron, or
 * ipcRenderer. All interaction with the app happens through the `api` argument.
 */

/**
 * Called by the host once KampAPI is ready.
 * @param {import('../../src/shared/kampAPI').KampAPI} api
 */
export function register(api) {
  api.panels.register({
    id: 'kamp-example-panel.stats',
    title: 'Stats',
    defaultSlot: 'main',

    render(container) {
      container.style.cssText = 'padding: 20px; font-family: monospace; font-size: 13px;'

      async function refresh() {
        try {
          const res = await fetch(`${api.serverUrl}/api/v1/player/state`)
          /** @type {{ playing: boolean, position: number, duration: number, volume: number, current_track: object | null }} */
          const state = await res.json()

          const track = state.current_track
          container.innerHTML = track
            ? `<div style="color:#fff;font-size:14px;font-weight:bold;margin-bottom:12px">Now Playing</div>
               <table style="color:#ccc;border-collapse:collapse;width:100%">
                 <tr><td style="padding:3px 12px 3px 0;color:#888">Title</td><td>${esc(track.title)}</td></tr>
                 <tr><td style="padding:3px 12px 3px 0;color:#888">Artist</td><td>${esc(track.artist)}</td></tr>
                 <tr><td style="padding:3px 12px 3px 0;color:#888">Album</td><td>${esc(track.album)}</td></tr>
                 <tr><td style="padding:3px 12px 3px 0;color:#888">Year</td><td>${esc(track.year)}</td></tr>
                 <tr><td style="padding:3px 12px 3px 0;color:#888">Plays</td><td>${track.play_count}</td></tr>
                 <tr><td style="padding:3px 12px 3px 0;color:#888">Position</td><td>${fmt(state.position)} / ${fmt(state.duration)}</td></tr>
                 <tr><td style="padding:3px 12px 3px 0;color:#888">Volume</td><td>${Math.round(state.volume * 100)}%</td></tr>
               </table>`
            : `<div style="color:#888">Nothing playing</div>`
        } catch {
          container.innerHTML = `<div style="color:#666">Server unavailable</div>`
        }
      }

      void refresh()
      const interval = setInterval(() => void refresh(), 2000)

      // Return cleanup so the host can stop polling when the panel unmounts.
      return () => clearInterval(interval)
    }
  })
}

/** Escape HTML special characters to prevent XSS from track metadata. */
function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** Format seconds as m:ss. */
function fmt(secs) {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}
