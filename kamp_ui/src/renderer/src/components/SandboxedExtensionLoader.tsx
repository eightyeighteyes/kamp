import { useEffect, useRef } from 'react'
import type { ExtensionInfo, SlotId, PlayerState } from '../../../shared/kampAPI'

/**
 * Static shim injected into every community extension iframe via srcdoc.
 *
 * The shim is static (no per-extension data) so its SHA-256 hash can be
 * listed in index.html's script-src CSP, satisfying Chromium's rule that
 * srcdoc iframes inherit the parent document's CSP.
 *
 * Extension code and permissions arrive via postMessage (kamp:init) after
 * the iframe's onLoad fires. The shim builds a permission-scoped SDK object
 * and passes it to the extension's register() function.
 *
 * SDK methods available to the shim:
 *   player.getState()          — async, proxied via kamp:sdk-call / kamp:sdk-response
 *   player.onTrackChange(cb)   — subscription, proxied via kamp:sdk-subscribe / kamp:sdk-event
 *   player.onPlayStateChange(cb) — subscription, same pattern
 *   library.getAlbumArtUrl()   — sync, constructs URL against the hardcoded server origin
 *
 * Hash (index.html script-src): sha256-pZ2P3MM/ZsmuxQcGlYScwK38Sz5eE+wn9o/rNUV96gA=
 * If you change SANDBOX_SHIM, recompute the hash and update index.html:
 *   node -e "const s='<paste shim>';console.log('sha256-'+require('crypto').createHash('sha256').update(s,'utf8').digest('base64'))"
 *
 * NOTE: sandboxed iframes without allow-same-origin reload when moved in the
 * DOM, so we do NOT use a holding-area/move strategy. Each panel mount creates
 * a fresh iframe; state is lost on tab switch.
 */
// prettier-ignore
// If you change this string, recompute the hash and update index.html script-src (see comment above).
const SANDBOX_SHIM = `(function(){var SERVER="http://127.0.0.1:8000",r={},c={},pending=null,reqs={},rid=0,subs={},sid=0;function rpc(method,args){return new Promise(function(resolve,reject){var id=++rid;reqs[id]={resolve:resolve,reject:reject};window.parent.postMessage({type:"kamp:sdk-call",id:id,method:method,args:args},"*")})}function subscribe(event,cb){var id=++sid;subs[id]={event:event,cb:cb};window.parent.postMessage({type:"kamp:sdk-subscribe",subId:id,event:event},"*");return function(){delete subs[id];window.parent.postMessage({type:"kamp:sdk-unsubscribe",subId:id},"*")}}window.addEventListener("message",function(e){if(e.source!==window.parent)return;var m=e.data;if(!m||typeof m.type!=="string")return;if(m.type==="kamp:init"){var id=m.id,perms=m.permissions||[];var K={panels:{register:function(p){if(!p||typeof p.id!=="string")return;r[p.id]=p.render;window.parent.postMessage({type:"kamp:register-panel",extensionId:id,manifest:{id:p.id,title:p.title,defaultSlot:p.defaultSlot,compatibleSlots:p.compatibleSlots}},"*");if(pending===p.id&&typeof r[p.id]==="function"){c[p.id]=r[p.id](document.body);pending=null}}}};if(perms.indexOf("player.read")!==-1){K.player={getState:function(){return rpc("player.getState",[])},onTrackChange:function(cb){return subscribe("track.changed",cb)},onPlayStateChange:function(cb){return subscribe("play_state.changed",cb)}}}if(perms.indexOf("library.read")!==-1){K.library={getAlbumArtUrl:function(aa,a){return SERVER+"/api/v1/album-art?album_artist="+encodeURIComponent(aa)+"&album="+encodeURIComponent(a)}}}var b=new Blob([m.code],{type:"text/javascript"});var u=URL.createObjectURL(b);import(u).then(function(mod){if(typeof mod.register==="function")mod.register(K)}).catch(function(err){console.error("[kamp sandbox "+id+"]",err)}).finally(function(){URL.revokeObjectURL(u)})}else if(m.type==="kamp:sdk-response"){var req=reqs[m.id];if(!req)return;delete reqs[m.id];if(m.error)req.reject(new Error(m.error));else req.resolve(m.result)}else if(m.type==="kamp:sdk-event"){Object.keys(subs).forEach(function(k){if(subs[k].event===m.event)subs[k].cb(m.payload)})}else if(m.type==="kamp:panel-mount"&&m.panelId){if(typeof r[m.panelId]==="function"){c[m.panelId]=r[m.panelId](document.body)}else{pending=m.panelId}}else if(m.type==="kamp:panel-unmount"&&m.panelId){var fn=c[m.panelId];if(typeof fn==="function")fn();delete c[m.panelId];if(pending===m.panelId)pending=null}})})();`

const SANDBOX_SRCDOC =
  `<!doctype html>
<html>
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; script-src blob: 'unsafe-inline'; connect-src http://127.0.0.1:8000; img-src http://127.0.0.1:8000; style-src 'unsafe-inline'">
<style>html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; }</style>
</head>
<body>
<script>${SANDBOX_SHIM}<` +
  `/script>
</body>
</html>`

// Tracks active subscriptions from sandboxed iframes.
// Outer key: the iframe's WindowProxy (browser-controlled, not forgeable by message content).
// Inner key: the subId chosen by that iframe.
// An extension can only reach its own inner Map, making cross-extension unsubscription
// structurally impossible without needing composite string keys or suppression annotations.
const _activeSubscriptions = new WeakMap<WindowProxy, Map<number, () => void>>()

/**
 * Handles kamp:sdk-call messages from sandboxed extension iframes.
 *
 * Extensions call SDK methods (e.g. api.player.getState()) which post a
 * kamp:sdk-call message. This handler dispatches to the real KampAPI
 * implementation and posts a kamp:sdk-response back to the source iframe.
 */
function handleSdkCall(
  event: MessageEvent<{ type: string; id: number; method: string; args: unknown[] }>
): void {
  const msg = event.data
  if (!msg || msg.type !== 'kamp:sdk-call') return
  const source = event.source as WindowProxy | null
  if (!source) return

  const respond = (result?: unknown, error?: string): void => {
    source.postMessage({ type: 'kamp:sdk-response', id: msg.id, result, error }, '*')
  }

  switch (msg.method) {
    case 'player.getState':
      window.KampAPI.player!.getState()
        .then((result: PlayerState) => respond(result))
        .catch((err: unknown) => respond(undefined, String(err)))
      break
    default:
      respond(undefined, `Unknown SDK method: ${msg.method}`)
  }
}

/**
 * Handles kamp:sdk-subscribe and kamp:sdk-unsubscribe messages from sandboxed iframes.
 *
 * When an extension calls api.player.onTrackChange(cb) or api.player.onPlayStateChange(cb),
 * the shim posts kamp:sdk-subscribe. This handler registers the real subscription on the
 * host KampAPI and posts kamp:sdk-event back to the iframe whenever the event fires.
 *
 * kamp:sdk-unsubscribe cancels the subscription.
 */
function handleSdkSubscribe(
  event: MessageEvent<{ type: string; subId: number; event: string }>
): void {
  const msg = event.data
  if (!msg || (msg.type !== 'kamp:sdk-subscribe' && msg.type !== 'kamp:sdk-unsubscribe')) return
  const source = event.source as WindowProxy | null
  if (!source) return

  if (msg.type === 'kamp:sdk-unsubscribe') {
    const sourceSubs = _activeSubscriptions.get(source)
    const unsub = sourceSubs?.get(msg.subId)
    if (unsub) {
      unsub()
      sourceSubs!.delete(msg.subId)
    }
    return
  }

  // kamp:sdk-subscribe
  const dispatch = (state: PlayerState): void => {
    source.postMessage({ type: 'kamp:sdk-event', event: msg.event, payload: state }, '*')
  }

  let unsub: (() => void) | undefined
  if (msg.event === 'track.changed') {
    unsub = window.KampAPI.player!.onTrackChange(dispatch)
  } else if (msg.event === 'play_state.changed') {
    unsub = window.KampAPI.player!.onPlayStateChange(dispatch)
  }
  if (unsub) {
    let sourceSubs = _activeSubscriptions.get(source)
    if (!sourceSubs) {
      sourceSubs = new Map()
      _activeSubscriptions.set(source, sourceSubs)
    }
    sourceSubs.set(msg.subId, unsub)
  }
}

/**
 * Creates a discovery iframe for an extension: loads the shim, sends kamp:init,
 * and waits for the extension to call panels.register() (which sends kamp:register-panel
 * back to the host). The iframe is removed once registration is complete.
 *
 * Returns a cleanup function that removes the iframe if registration hasn't
 * completed yet (e.g. on component unmount).
 */
function createDiscoveryIframe(
  ext: ExtensionInfo,
  onRegister: (manifest: {
    id: string
    title: string
    defaultSlot: string
    compatibleSlots?: string[]
  }) => void
): () => void {
  const iframe = document.createElement('iframe')
  iframe.sandbox.add('allow-scripts')
  iframe.srcdoc = SANDBOX_SRCDOC
  iframe.style.cssText = 'position:absolute;width:0;height:0;border:none;'
  iframe.title = `Discovery: ${ext.id}`

  let done = false

  function onMessage(event: MessageEvent): void {
    const msg = event.data as Record<string, unknown> | null
    if (!msg || msg.type !== 'kamp:register-panel') return
    if (msg.extensionId !== ext.id) return
    const manifest = msg.manifest as
      | { id: string; title: string; defaultSlot: string; compatibleSlots?: string[] }
      | undefined
    if (!manifest?.id) return
    done = true
    window.removeEventListener('message', onMessage)
    iframe.remove()
    onRegister(manifest)
  }

  window.addEventListener('message', onMessage)

  iframe.addEventListener(
    'load',
    () => {
      iframe.contentWindow?.postMessage(
        { type: 'kamp:init', id: ext.id, permissions: ext.permissions, code: ext.code },
        '*'
      )
    },
    { once: true }
  )

  document.body.appendChild(iframe)

  return () => {
    if (!done) {
      window.removeEventListener('message', onMessage)
      iframe.remove()
    }
  }
}

interface Props {
  extensions: ExtensionInfo[]
}

/**
 * Registers each community (Phase 2) extension's panels with the host.
 *
 * For each extension, spins up a short-lived discovery iframe to run the
 * extension code and collect its panels.register() calls. Once registration
 * is complete the discovery iframe is discarded.
 *
 * When a panel tab is activated, render() creates a fresh sandboxed iframe,
 * sends kamp:init + kamp:panel-mount, and renders the extension content.
 * On deactivation, cleanup() sends kamp:panel-unmount and removes the iframe.
 *
 * State is lost on tab switch — the holding-area/move strategy was abandoned
 * because Chromium reloads sandboxed cross-origin iframes on DOM move.
 *
 * SDK calls (kamp:sdk-call) from any active iframe are handled by a single
 * global listener registered for the lifetime of this component.
 */
export function SandboxedExtensionLoader({ extensions }: Props): null {
  // Track cleanup functions for in-flight discovery iframes.
  const cleanupRefs = useRef<Map<string, () => void>>(new Map())

  // Global handlers for SDK method proxying and subscriptions from all active extension iframes.
  useEffect(() => {
    window.addEventListener('message', handleSdkCall)
    window.addEventListener('message', handleSdkSubscribe)
    return () => {
      window.removeEventListener('message', handleSdkCall)
      window.removeEventListener('message', handleSdkSubscribe)
    }
  }, [])

  useEffect(() => {
    for (const ext of extensions) {
      if (cleanupRefs.current.has(ext.id)) continue // already discovering

      const cleanup = createDiscoveryIframe(ext, (manifest) => {
        cleanupRefs.current.delete(ext.id)

        window.KampAPI.panels.register({
          id: manifest.id,
          title: manifest.title,
          defaultSlot: manifest.defaultSlot as SlotId,
          compatibleSlots: manifest.compatibleSlots as SlotId[] | undefined,
          render(container: HTMLElement): () => void {
            const panelId = manifest.id
            const iframe = document.createElement('iframe')
            iframe.sandbox.add('allow-scripts')
            iframe.srcdoc = SANDBOX_SRCDOC
            iframe.style.cssText = 'width:100%;height:100%;border:none;display:block;'
            iframe.title = `Extension: ${ext.id}`

            iframe.addEventListener(
              'load',
              () => {
                iframe.contentWindow?.postMessage(
                  {
                    type: 'kamp:init',
                    id: ext.id,
                    permissions: ext.permissions,
                    code: ext.code
                  },
                  '*'
                )
                iframe.contentWindow?.postMessage({ type: 'kamp:panel-mount', panelId }, '*')
              },
              { once: true }
            )

            container.appendChild(iframe)

            return () => {
              iframe.contentWindow?.postMessage({ type: 'kamp:panel-unmount', panelId }, '*')
              iframe.remove()
            }
          }
        })
      })

      cleanupRefs.current.set(ext.id, cleanup)
    }

    const refs = cleanupRefs.current
    return () => {
      refs.forEach((fn) => fn())
      refs.clear()
    }
  }, [extensions])

  return null
}
