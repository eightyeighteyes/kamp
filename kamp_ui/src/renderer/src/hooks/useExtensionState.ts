/**
 * useExtensionState — localStorage-backed state for per-extension settings,
 * enable/disable toggles, and Phase 2 permission approvals.
 *
 * Storage keys:
 *   kamp:ext:disabled        JSON array of disabled extension ids
 *   kamp:ext:approved        JSON array of approved community extension ids
 *   kamp:ext:denied          JSON array of denied community extension ids
 *   kamp:ext:setting:<id>:<key>  per-extension setting value (JSON-encoded)
 */

import { useCallback, useState } from 'react'

// ---------------------------------------------------------------------------
// Storage helpers
// ---------------------------------------------------------------------------

function loadSet(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return new Set()
    return new Set(JSON.parse(raw) as string[])
  } catch {
    return new Set()
  }
}

function saveSet(key: string, s: Set<string>): void {
  localStorage.setItem(key, JSON.stringify(Array.from(s)))
}

function settingKey(extId: string, key: string): string {
  return `kamp:ext:setting:${extId}:${key}`
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export type ExtensionStateHook = {
  /** Ids of extensions the user has explicitly disabled. */
  disabledIds: Set<string>
  /** Ids of Phase 2 community extensions the user has approved. */
  approvedIds: Set<string>
  /** Ids of Phase 2 community extensions the user has denied. */
  deniedIds: Set<string>

  /** Toggle the enabled state of an extension. */
  toggleEnabled: (id: string) => void
  /** Mark a community extension as approved by the user. */
  approve: (id: string) => void
  /** Mark a community extension as denied by the user. */
  deny: (id: string) => void
  /**
   * Clear the denied state for a community extension so the permission prompt
   * will be shown again on next load.
   */
  resetDenied: (id: string) => void
  /** Remove all stored state (approved, denied, disabled, settings) for an extension. */
  clearExtensionState: (id: string) => void

  /**
   * Read the stored value for a setting key belonging to `extId`,
   * or `undefined` if not yet set.
   */
  getSettingValue: (extId: string, key: string) => unknown
  /** Persist a setting value and trigger a re-render. */
  setSettingValue: (extId: string, key: string, value: unknown) => void
}

export function useExtensionState(): ExtensionStateHook {
  const [disabledIds, setDisabledIds] = useState<Set<string>>(() => loadSet('kamp:ext:disabled'))
  const [approvedIds, setApprovedIds] = useState<Set<string>>(() => loadSet('kamp:ext:approved'))
  const [deniedIds, setDeniedIds] = useState<Set<string>>(() => loadSet('kamp:ext:denied'))
  // Incrementing counter so callers re-render when any setting changes.
  const [, setSettingVersion] = useState(0)

  const toggleEnabled = useCallback((id: string) => {
    setDisabledIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      saveSet('kamp:ext:disabled', next)
      return next
    })
  }, [])

  const approve = useCallback((id: string) => {
    setApprovedIds((prev) => {
      const next = new Set(prev)
      next.add(id)
      saveSet('kamp:ext:approved', next)
      return next
    })
    // Remove from denied if previously denied.
    setDeniedIds((prev) => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      saveSet('kamp:ext:denied', next)
      return next
    })
  }, [])

  const deny = useCallback((id: string) => {
    setDeniedIds((prev) => {
      const next = new Set(prev)
      next.add(id)
      saveSet('kamp:ext:denied', next)
      return next
    })
  }, [])

  const resetDenied = useCallback((id: string) => {
    setDeniedIds((prev) => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      saveSet('kamp:ext:denied', next)
      return next
    })
  }, [])

  const clearExtensionState = useCallback((id: string) => {
    setApprovedIds((prev) => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      saveSet('kamp:ext:approved', next)
      return next
    })
    setDeniedIds((prev) => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      saveSet('kamp:ext:denied', next)
      return next
    })
    setDisabledIds((prev) => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      saveSet('kamp:ext:disabled', next)
      return next
    })
    // Remove all per-setting keys for this extension.
    const prefix = `kamp:ext:setting:${id}:`
    Object.keys(localStorage)
      .filter((k) => k.startsWith(prefix))
      .forEach((k) => localStorage.removeItem(k))
  }, [])

  const getSettingValue = useCallback((extId: string, key: string): unknown => {
    try {
      const raw = localStorage.getItem(settingKey(extId, key))
      if (raw === null) return undefined
      return JSON.parse(raw) as unknown
    } catch {
      return undefined
    }
  }, [])

  const setSettingValue = useCallback((extId: string, key: string, value: unknown): void => {
    localStorage.setItem(settingKey(extId, key), JSON.stringify(value))
    setSettingVersion((v) => v + 1)
  }, [])

  return {
    disabledIds,
    approvedIds,
    deniedIds,
    toggleEnabled,
    approve,
    deny,
    resetDenied,
    clearExtensionState,
    getSettingValue,
    setSettingValue
  }
}
