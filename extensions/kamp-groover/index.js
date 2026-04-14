/**
 * Groover — kamp community extension
 *
 * Displays the current track's album art with a continuously rotating
 * color palette using CSS hue-rotate, animated via requestAnimationFrame.
 *
 * This file is also the canonical developer reference for the kamp extension
 * SDK. Read it alongside kamp-example-panel (a minimal boilerplate) to
 * understand the full lifecycle. Every SDK call is annotated with:
 *   - which permission it requires
 *   - what it returns / when it fires
 *   - cleanup responsibilities
 *
 * --- EXTENSION BASICS ---
 *
 * A kamp extension is an ES module that exports a `register(api)` function.
 * The host discovers it by looking for packages with "kamp-extension" in their
 * npm keywords. On first install the user is shown a permission prompt listing
 * the permissions declared in package.json#kamp.permissions. If approved, the
 * extension runs inside a sandboxed iframe (Phase 2 / community security level)
 * with no access to Node.js, Electron, or the filesystem — only the `api`
 * object passed to register().
 *
 * package.json declares:
 *   "kamp": { "permissions": ["player.read", "library.read"] }
 *
 * This is a Phase 2 (community) extension: it is NOT on the first-party
 * allow-list and loads inside a sandboxed iframe with no contextBridge access.
 * All server communication goes through the SDK methods passed to register().
 */

/**
 * register() — extension entry point
 *
 * Called once by the host after the user approves permissions. The `api`
 * argument is a permission-scoped SDK object: only methods matching the
 * permissions listed in package.json are present. Calling an unpermitted
 * method throws at runtime.
 *
 * You must call api.panels.register() before register() returns. The host
 * uses this call to learn the extension's panel metadata (id, title, slot).
 * If register() returns without registering a panel, the extension is silently
 * ignored.
 *
 * @param {object} api                          - Permission-scoped SDK
 * @param {object} api.panels                   - Panel management (always available)
 * @param {object} api.player                   - Playback state (requires player.read)
 * @param {object} api.library                  - Library metadata (requires library.read)
 */
export function register(api) {
  /**
   * api.panels.register() — declare your panel
   *
   * `id`              Stable, globally unique identifier. Convention: "<pkg-name>.<panel>".
   *                   The host uses this to persist panel state across sessions.
   * `title`           Label shown in the panel tab bar.
   * `defaultSlot`     Where to place the panel on first install. Currently "main"
   *                   is the only supported slot.
   * `compatibleSlots` Slots the user can drag the panel to. Omit to restrict to
   *                   defaultSlot only.
   * `render(container)` Called each time the panel is mounted (user clicks the tab
   *                   or app restores layout). `container` is a real DOM node inside
   *                   the sandboxed iframe — write to it directly. Must return a
   *                   cleanup function that the host calls on unmount.
   */
  api.panels.register({
    id: 'kamp-groover.visualizer',
    title: 'Groover',
    defaultSlot: 'main',
    compatibleSlots: ['main'],

    render(container) {
      // Clear any leftover DOM from a previous mount cycle. React StrictMode
      // fires useEffect twice (mount → cleanup → remount), which sends
      // panel-mount twice; without this, elements accumulate in the container.
      container.innerHTML = ''

      // Fill the container as a centered column so the art square grows to
      // fill available vertical space without breaking the layout.
      container.style.cssText = `
        margin: 0; padding: 16px;
        width: 100%; height: 100%;
        background: #0a0a0a;
        display: flex;
        flex-direction: column;
        align-items: center;
        overflow: hidden;
      `

      const artWrap = document.createElement('div')
      artWrap.style.cssText = `
        position: relative;
        flex: 1;
        min-height: 0;
        aspect-ratio: 1;
        max-width: 100%;
        border-radius: 8px;
        background: #111;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
      `

      const placeholder = document.createElement('div')
      placeholder.style.cssText = `
        font-size: 72px; opacity: 0.15; color: #fff;
      `
      placeholder.textContent = '♪'

      const img = document.createElement('img')
      img.style.cssText = `
        position: absolute; inset: 0;
        width: 100%; height: 100%;
        object-fit: cover;
        opacity: 0;
        transition: opacity 0.2s;
      `

      artWrap.appendChild(placeholder)
      artWrap.appendChild(img)
      container.appendChild(artWrap)

      // -----------------------------------------------------------------------
      // Hue rotation animation
      // requestAnimationFrame is available inside the sandboxed iframe.
      // Advance hue by ~0.022° per millisecond ≈ one full cycle every ~45 s.
      // -----------------------------------------------------------------------
      let hue = 0
      let rafId = null
      let lastTs = null

      function animate(ts) {
        if (lastTs !== null) {
          hue = (hue + (ts - lastTs) * 0.022) % 360
        }
        lastTs = ts
        img.style.filter = `hue-rotate(${hue.toFixed(1)}deg) saturate(1.4) brightness(0.95)`
        rafId = requestAnimationFrame(animate)
      }

      rafId = requestAnimationFrame(animate)

      // -----------------------------------------------------------------------
      // api.library.getAlbumArtUrl() — requires library.read
      //
      // Returns a URL string pointing to the kamp server's album art endpoint
      // for the given artist + album pair. The URL is valid as long as the
      // server is running; set it as img.src to load the image.
      //
      // Signature: getAlbumArtUrl(albumArtist: string, album: string): string
      // -----------------------------------------------------------------------

      // -----------------------------------------------------------------------
      // api.player.getState() — requires player.read
      //
      // Returns a Promise that resolves to the current PlayerState:
      //   {
      //     current_track: Track | null,  // null when nothing is playing
      //     position: number,             // seconds elapsed
      //     duration: number,             // track length in seconds
      //     volume: number,               // 0–100
      //     playing: boolean,
      //   }
      //
      // Track fields: title, artist, album, album_artist, year, play_count, …
      //
      // Use getState() to seed the UI on mount, then subscribe via
      // onTrackChange() for live updates instead of polling.
      // -----------------------------------------------------------------------

      // -----------------------------------------------------------------------
      // api.player.onTrackChange() — requires player.read
      //
      // Subscribes to track-change events pushed from the server over
      // WebSocket. The callback receives a full PlayerState (same shape as
      // getState()) every time the track changes.
      //
      // Returns an unsubscribe function — call it in your cleanup to avoid
      // memory leaks and stale callbacks after the panel unmounts.
      //
      // Signature: onTrackChange(callback: (state: PlayerState) => void): () => void
      // -----------------------------------------------------------------------
      let currentArtKey = null

      function updateArt(state) {
        const track = state.current_track
        if (!track) {
          img.style.opacity = '0'
          currentArtKey = null
          return
        }
        // Reload art only when the album changes — album_artist + album is the
        // stable key; artist alone can differ across tracks on the same album.
        const artKey = `${track.album_artist}||${track.album}`
        if (artKey !== currentArtKey) {
          currentArtKey = artKey
          const url = api.library.getAlbumArtUrl(track.album_artist, track.album)
          img.style.opacity = '0'
          img.onload = () => { img.style.opacity = '1' }
          img.onerror = () => { img.style.opacity = '0' }
          img.src = url
        }
      }

      // Seed with current state, then subscribe for push updates.
      api.player.getState().then(updateArt).catch(() => {})
      const unsubTrack = api.player.onTrackChange(updateArt)

      // -----------------------------------------------------------------------
      // Cleanup — render() must return a function
      //
      // The host calls this when the panel unmounts (user switches away, app
      // closes, or the extension is disabled). Cancel all async work here:
      // timers, animation frames, and subscriptions. Failing to unsubscribe
      // from onTrackChange() will cause callbacks to fire against a detached
      // DOM node and leak memory.
      // -----------------------------------------------------------------------
      return () => {
        cancelAnimationFrame(rafId)
        unsubTrack()
      }
    }
  })
}
