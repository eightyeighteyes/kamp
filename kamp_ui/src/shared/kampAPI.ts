/**
 * Shared KampAPI types.
 *
 * Imported by the preload (builds the object) and by the renderer (consumes it).
 * Must contain only pure TypeScript — no Node.js or Electron imports.
 */

/**
 * A single field in an extension's declared settings schema.
 * Declared in package.json under `kamp.settings`.
 */
export type ExtensionSettingSchema = {
  key: string
  label: string
  type: 'text' | 'number' | 'boolean' | 'select'
  /** Valid values for `type: 'select'`. */
  options?: string[]
  default?: string | number | boolean
  hint?: string
}

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
  /** Version string from the extension's package.json. */
  version: string
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
  /**
   * Settings schema declared in package.json under `kamp.settings`.
   * The host renders a settings form from this schema in the Extensions preferences panel.
   */
  settings?: ExtensionSettingSchema[]
  /**
   * Where this extension was discovered:
   *   'bundled'  – shipped inside the app's `extensions/` directory; cannot be removed via the UI.
   *   'npm'      – user-installed into `node_modules/`; can be uninstalled via the UI.
   */
  installedFrom: 'bundled' | 'npm'
}

/** Result returned by extension install/uninstall operations. */
export type ExtensionInstallResult = { ok: true; id: string } | { ok: false; error: string }

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
    /** Install a community extension by npm package name or local directory path. */
    install: (source: 'npm' | 'local', nameOrPath: string) => Promise<ExtensionInstallResult>
    /** Uninstall a community extension by its package id. */
    uninstall: (id: string) => Promise<ExtensionInstallResult>
  }

  /**
   * Per-extension settings access. Only provided when the extension declares
   * the `"settings"` permission. Values are persisted by the host across sessions.
   */
  settings?: {
    /** Read the current value of a setting key, or undefined if not yet set. */
    get: (key: string) => unknown
    /** Persist a new value for a setting key. */
    set: (key: string, value: unknown) => void
  }
}
