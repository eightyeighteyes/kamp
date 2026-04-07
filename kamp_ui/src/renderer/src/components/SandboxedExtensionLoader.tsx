import React, { useEffect, useRef } from 'react'
import type { ExtensionInfo, SlotId } from '../../../shared/kampAPI'

/**
 * Builds the srcdoc HTML for a sandboxed community extension iframe.
 *
 * The iframe carries no allow-same-origin — it cannot access the host DOM,
 * localStorage, or contextBridge. All host interaction goes through postMessage.
 *
 * CSP connect-src is pinned to the exact kamp server origin so extensions
 * cannot make arbitrary outbound requests (AC#5).
 */
function buildSrcdoc(extensionId: string, code: string, serverUrl: string): string {
  // Embed strings as JSON literals, escaping < > & so the HTML parser never
  // sees a </script> tag or entity sequence inside the script block.
  function safeJson(s: string): string {
    return JSON.stringify(s)
      .replace(/</g, '\\u003c')
      .replace(/>/g, '\\u003e')
      .replace(/&/g, '\\u0026')
  }

  const escapedCode = safeJson(code)
  const escapedId = safeJson(extensionId)
  const escapedServerUrl = safeJson(serverUrl)

  return `<!doctype html>
<html>
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; script-src 'unsafe-inline' blob:; connect-src ${serverUrl}; style-src 'unsafe-inline'">
</head>
<body>
<script>(async function () {
  var __id = ${escapedId}
  var __serverUrl = ${escapedServerUrl}

  // Panel render functions keyed by panel id.
  var __renders = {}
  // Active cleanup functions keyed by panel id.
  var __cleanups = {}

  // Minimal KampAPI shim. Extensions call panels.register(); the host moves
  // the iframe into the active container on mount, back to a hidden holding
  // area on unmount, so the iframe is never reloaded between tab switches.
  var KampAPI = {
    serverUrl: __serverUrl,
    panels: {
      register: function (manifest) {
        if (!manifest || typeof manifest.id !== 'string') return
        __renders[manifest.id] = manifest.render
        // Notify the host so it can register this panel in the panel layout.
        window.parent.postMessage({
          type: 'kamp:register-panel',
          extensionId: __id,
          manifest: {
            id: manifest.id,
            title: manifest.title,
            defaultSlot: manifest.defaultSlot,
            compatibleSlots: manifest.compatibleSlots
          }
        }, '*')
      }
    }
  }

  // Host → iframe: mount/unmount lifecycle messages.
  window.addEventListener('message', function (e) {
    // Only accept messages from the direct parent (the host renderer).
    if (e.source !== window.parent) return
    var msg = e.data
    if (!msg || typeof msg.type !== 'string') return

    if (msg.type === 'kamp:panel-mount' && msg.panelId) {
      var render = __renders[msg.panelId]
      if (typeof render === 'function') {
        __cleanups[msg.panelId] = render(document.body)
      }
    } else if (msg.type === 'kamp:panel-unmount' && msg.panelId) {
      var cleanup = __cleanups[msg.panelId]
      if (typeof cleanup === 'function') cleanup()
      delete __cleanups[msg.panelId]
    }
  })

  // Load extension as an ES module via a blob URL. The main process already
  // read the file contents; we never need a file:// import from the renderer.
  var blob = new Blob([${escapedCode}], { type: 'text/javascript' })
  var url = URL.createObjectURL(blob)
  try {
    var mod = await import(url)
    if (typeof mod.register === 'function') mod.register(KampAPI)
  } catch (err) {
    console.error('[kamp sandbox ' + __id + ']', err)
  } finally {
    URL.revokeObjectURL(url)
  }
})()
<` + `/script>
</body>
</html>`
}

interface Props {
  extensions: ExtensionInfo[]
}

/**
 * Renders each community (Phase 2) extension in its own sandboxed iframe.
 *
 * All iframes live in a hidden holding div. When the user activates an
 * extension panel tab, ExtensionPanel's render() moves the iframe into the
 * visible container div. On deactivation, cleanup() moves it back here.
 * Moving an iframe within the same document does not reload it, so extension
 * state (timers, network sockets, DOM) persists across tab switches.
 */
export function SandboxedExtensionLoader({ extensions }: Props): React.JSX.Element {
  const holdingRef = useRef<HTMLDivElement>(null)
  const iframeRefs = useRef<Map<string, HTMLIFrameElement>>(new Map())

  useEffect(() => {
    function onMessage(event: MessageEvent): void {
      // Reject messages from anything other than our known sandbox iframes.
      const knownWindows = Array.from(iframeRefs.current.values())
        .map((el) => el.contentWindow)
        .filter(Boolean)
      if (!knownWindows.includes(event.source as Window | null)) return

      const msg = event.data as Record<string, unknown> | null
      if (!msg || typeof msg !== 'object') return

      if (msg.type === 'kamp:register-panel') {
        const extensionId = msg.extensionId as string | undefined
        const manifest = msg.manifest as
          | { id: string; title: string; defaultSlot: string; compatibleSlots?: string[] }
          | undefined

        if (!extensionId || !manifest?.id || !manifest?.title || !manifest?.defaultSlot) return
        const iframeEl = iframeRefs.current.get(extensionId)
        if (!iframeEl) return

        const panelId = manifest.id

        window.KampAPI.panels.register({
          id: panelId,
          title: manifest.title,
          defaultSlot: manifest.defaultSlot as SlotId,
          compatibleSlots: manifest.compatibleSlots as SlotId[] | undefined,
          render(container: HTMLElement): () => void {
            // Move the live iframe into the active panel container.
            // Does not reload the iframe — extension state is preserved.
            container.appendChild(iframeEl)
            iframeEl.style.display = 'block'
            iframeEl.contentWindow?.postMessage({ type: 'kamp:panel-mount', panelId }, '*')

            return () => {
              // Send unmount signal before moving iframe back to the holding area.
              iframeEl.contentWindow?.postMessage({ type: 'kamp:panel-unmount', panelId }, '*')
              iframeEl.style.display = 'none'
              holdingRef.current?.appendChild(iframeEl)
            }
          }
        })
      }
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [extensions])

  return (
    // Hidden holding area. Iframes here stay loaded but invisible.
    <div ref={holdingRef} style={{ display: 'none' }} aria-hidden="true">
      {extensions.map((ext) => (
        <iframe
          key={ext.id}
          ref={(el): void => {
            if (el) iframeRefs.current.set(ext.id, el)
            else iframeRefs.current.delete(ext.id)
          }}
          sandbox="allow-scripts"
          srcDoc={buildSrcdoc(ext.id, ext.code, window.KampAPI.serverUrl)}
          style={{ width: '100%', height: '100%', border: 'none', display: 'none' }}
          title={`Extension: ${ext.id}`}
        />
      ))}
    </div>
  )
}
