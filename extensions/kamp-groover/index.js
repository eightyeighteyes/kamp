/**
 * Groover — community extension
 *
 * Displays the current track's album art with a continuously rotating
 * color palette using CSS hue-rotate, animated via requestAnimationFrame.
 *
 * This is a Phase 2 (community) extension: it is NOT on the first-party
 * allow-list and loads inside a sandboxed iframe with no contextBridge access.
 * All server communication goes through the SDK methods passed to register().
 */

export function register(api) {
  api.panels.register({
    id: 'kamp-groover.visualizer',
    title: 'Groover',
    defaultSlot: 'main',
    compatibleSlots: ['main'],

    render(container) {
      // Clear any leftover DOM from a previous mount cycle (React StrictMode
      // fires useEffect twice: mount → cleanup → remount, sending panel-mount
      // twice; without this, two sets of elements accumulate in document.body).
      container.innerHTML = ''

      // Fill the container exactly as Now Playing does — column, centered,
      // with the art growing to fill available space while staying square.
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
      // Player state polling
      // -----------------------------------------------------------------------
      let currentArtKey = null

      async function poll() {
        try {
          const state = await api.player.getState()
          const track = state.current_track

          if (!track) {
            img.style.opacity = '0'
            currentArtKey = null
            return
          }

          // Reload art only when the album changes.
          const artKey = `${track.album_artist}||${track.album}`
          if (artKey !== currentArtKey) {
            currentArtKey = artKey
            const url = api.library.getAlbumArtUrl(track.album_artist, track.album)
            img.style.opacity = '0'
            img.onload = () => { img.style.opacity = '1' }
            img.onerror = () => { img.style.opacity = '0' }
            img.src = url
          }
        } catch {
          // Server unreachable — leave last state visible.
        }
      }

      void poll()
      const pollInterval = setInterval(() => void poll(), 2000)

      // -----------------------------------------------------------------------
      // Cleanup
      // -----------------------------------------------------------------------
      return () => {
        cancelAnimationFrame(rafId)
        clearInterval(pollInterval)
      }
    }
  })
}
