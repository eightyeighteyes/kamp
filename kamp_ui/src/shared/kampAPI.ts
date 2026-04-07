/**
 * Shared KampAPI types.
 *
 * Imported by the preload (builds the object) and by the renderer (consumes it).
 * Must contain only pure TypeScript — no Node.js or Electron imports.
 */

/**
 * Layout slots available in the host application.
 *
 * - `main`   – tabbed content area (Library, Now Playing, extension views)
 * - `left`   – left sidebar column
 * - `right`  – right sidebar column
 * - `bottom` – bottom bar (transport controls)
 */
export type SlotId = 'main' | 'left' | 'right' | 'bottom'

/** A panel contributed by a frontend extension. */
export type PanelManifest = {
  /** Unique identifier for the panel (e.g. "kamp-example-panel.stats"). */
  id: string
  /** Human-readable title shown in the nav tab. */
  title: string
  /**
   * Where this panel should appear by default.
   * Extensions that render full page views should use `'main'`.
   */
  defaultSlot: SlotId
  /**
   * Slots this panel can occupy. Omit to allow all slots.
   * The host uses this to disable incompatible slot buttons in the panel picker.
   */
  compatibleSlots?: SlotId[]
  /**
   * Mount the panel into `container` and return a cleanup function.
   * Called once when the panel tab is first shown; cleanup is called on unmount.
   */
  render: (container: HTMLElement) => () => void
}

/** Descriptor returned by the main process for each discovered extension. */
export type ExtensionInfo = {
  id: string
  name: string
  /** Source code of the extension's entry point, read by the main process. */
  code: string
  /**
   * Security phase that governs how this extension is loaded:
   *   1 – First-party: on the kamp allow-list; receives full contextBridge (KampAPI) access.
   *   2 – Community: keyword present but not on the allow-list; rendered in a sandboxed iframe.
   */
  phase: 1 | 2
  /**
   * Capabilities declared by this extension in its package.json `kamp.permissions` array.
   * The host scopes the KampAPI it passes to the extension's `register()` function based
   * on this list.  Extensions that access an undeclared capability receive an error.
   *
   * Known values: "library.read", "player.read", "player.control", "network.fetch", "settings"
   */
  permissions: string[]
}

/** The full shape of window.KampAPI. */
export type KampAPI = {
  /**
   * Base URL of the kamp HTTP server.
   * Extensions should use this rather than hard-coding localhost:8000.
   */
  serverUrl: string

  panels: {
    /** Register a panel contributed by an extension. */
    register: (manifest: PanelManifest) => void
    /** Return a snapshot of all currently registered panels. */
    getAll: () => PanelManifest[]
    /**
     * Subscribe to panel registrations. The callback is invoked each time a
     * panel is registered. Returns an unsubscribe function.
     */
    onRegister: (callback: (manifest: PanelManifest) => void) => () => void
  }

  extensions: {
    /** Ask the main process to discover installed kamp-extension packages. */
    getAll: () => Promise<ExtensionInfo[]>
  }
}
