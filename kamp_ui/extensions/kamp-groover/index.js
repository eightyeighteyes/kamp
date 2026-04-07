/**
 * Groover — community extension
 *
 * Displays the current track's album art with a continuously rotating
 * color palette using CSS hue-rotate, animated via requestAnimationFrame.
 *
 * This is a Phase 2 (community) extension: it is NOT on the first-party
 * allow-list and loads inside a sandboxed iframe with no contextBridge access.
 * All server communication goes through fetch() to the kamp server origin.
 */

export function register(api) {
  api.panels.register({
    id: 'kamp-groover.visualizer',
    title: 'Groover',
    defaultSlot: 'main',

    render(container) {
      // -----------------------------------------------------------------------
      // DOM
      // -----------------------------------------------------------------------
      container.style.cssText = `
        margin: 0; padding: 0;
        width: 100%; height: 100%;
        background: #0a0a0a;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        overflow: hidden;
        font-family: 'DM Sans', system-ui, sans-serif;
      `

      const artWrap = document.createElement('div')
      artWrap.style.cssText = `
        position: relative;
        width: min(60vw, 60vh, 400px);
        height: min(60vw, 60vh, 400px);
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 0 80px 20px rgba(120, 80, 255, 0.25);
      `

      const img = document.createElement('img')
      img.style.cssText = `
        width: 100%; height: 100%;
        object-fit: cover;
        display: block;
        transition: opacity 0.4s;
      `
      img.setAttribute('crossorigin', 'anonymous')

      const placeholder = document.createElement('div')
      placeholder.style.cssText = `
        position: absolute; inset: 0;
        display: flex; align-items: center; justify-content: center;
        font-size: 72px; color: #333;
        background: #111;
      `
      placeholder.textContent = '♪'

      artWrap.appendChild(placeholder)
      artWrap.appendChild(img)

      const meta = document.createElement('div')
      meta.style.cssText = `
        margin-top: 28px;
        text-align: center;
        max-width: min(60vw, 400px);
      `

      const titleEl = document.createElement('div')
      titleEl.style.cssText = `
        font-size: 17px; font-weight: 600;
        color: #fff;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        margin-bottom: 6px;
      `

      const artistEl = document.createElement('div')
      artistEl.style.cssText = `
        font-size: 13px; color: #888;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      `

      const speedWrap = document.createElement('div')
      speedWrap.style.cssText = `
        margin-top: 20px;
        display: flex; align-items: center; gap: 10px;
      `
      const speedLabel = document.createElement('label')
      speedLabel.style.cssText = `font-size: 11px; color: #555;`
      speedLabel.textContent = 'Speed'
      const speedSlider = document.createElement('input')
      speedSlider.type = 'range'
      speedSlider.min = '0.1'
      speedSlider.max = '3'
      speedSlider.step = '0.1'
      speedSlider.value = '0.6'
      speedSlider.style.cssText = `
        width: 100px; accent-color: #7c5cbf; cursor: pointer;
      `
      speedWrap.appendChild(speedLabel)
      speedWrap.appendChild(speedSlider)

      meta.appendChild(titleEl)
      meta.appendChild(artistEl)
      meta.appendChild(speedWrap)

      container.appendChild(artWrap)
      container.appendChild(meta)

      // -----------------------------------------------------------------------
      // Hue rotation animation
      // -----------------------------------------------------------------------
      let hue = 0
      let rafId = null
      let lastTs = null

      function animate(ts) {
        if (lastTs !== null) {
          const delta = ts - lastTs
          // degrees per ms, controlled by speed slider
          hue = (hue + delta * 0.036 * parseFloat(speedSlider.value)) % 360
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
          const res = await fetch(`${api.serverUrl}/api/v1/player/state`)
          if (!res.ok) throw new Error(res.status)
          const state = await res.json()
          const track = state.current_track

          if (!track) {
            titleEl.textContent = 'Nothing playing'
            artistEl.textContent = ''
            img.style.opacity = '0'
            currentArtKey = null
            return
          }

          titleEl.textContent = track.title ?? ''
          artistEl.textContent = [track.artist, track.album].filter(Boolean).join(' · ')

          // Reload art only when the track changes.
          const artKey = `${track.album_artist}||${track.album}`
          if (artKey !== currentArtKey) {
            currentArtKey = artKey
            const url =
              `${api.serverUrl}/api/v1/album-art` +
              `?album_artist=${encodeURIComponent(track.album_artist)}` +
              `&album=${encodeURIComponent(track.album)}`
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
