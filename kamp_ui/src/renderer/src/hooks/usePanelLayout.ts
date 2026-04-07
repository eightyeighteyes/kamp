/**
 * Panel layout system: slot assignments, persistence, and built-in registration.
 *
 * Built-in panels (React components) register at module level before React
 * mounts. Extension panels arrive async via KampAPI.panels and are merged in
 * as they register. Layout is persisted to localStorage so user repositioning
 * survives app restarts.
 */

import React, { useEffect, useMemo, useRef, useState } from 'react'
import type { PanelManifest, SlotId } from '../../../shared/kampAPI'
import { useRegisteredPanels } from './useRegisteredPanels'

// ---------------------------------------------------------------------------
// Built-in panel type (React components, not subject to contextBridge)
// ---------------------------------------------------------------------------

/** A panel backed by a React component (used for built-in panels). */
export type BuiltInPanelManifest = {
  id: string
  title: string
  defaultSlot: SlotId
  /** Slots this panel can occupy. Omit to allow all slots. */
  compatibleSlots?: SlotId[]
  component: React.ComponentType
}

/** Discriminated union used throughout the rendering layer. */
export type UnifiedPanel =
  | (BuiltInPanelManifest & { kind: 'builtin' })
  | (PanelManifest & { kind: 'extension' })

// Module-level registry — populated synchronously before React mounts so the
// initial layout state can reference all built-ins on first render.
const builtInRegistry: BuiltInPanelManifest[] = []

/** Register a built-in panel. Idempotent: duplicate IDs are ignored. */
export function registerBuiltInPanel(manifest: BuiltInPanelManifest): void {
  if (!builtInRegistry.some((p) => p.id === manifest.id)) {
    builtInRegistry.push(manifest)
  }
}

// ---------------------------------------------------------------------------
// Persisted layout state
// ---------------------------------------------------------------------------

type LayoutState = {
  version: 1
  /** Panel IDs assigned to each slot, in display order. */
  slots: Record<SlotId, string[]>
  /** Panel IDs hidden by the user (registered but not shown in any slot). */
  hidden: string[]
}

const LAYOUT_KEY = 'kamp:panel-layout'
const SLOTS: SlotId[] = ['left', 'right', 'bottom', 'main']

function buildDefaultLayout(panels: UnifiedPanel[]): LayoutState {
  const slots: Record<SlotId, string[]> = { left: [], right: [], bottom: [], main: [] }
  for (const panel of panels) {
    slots[panel.defaultSlot].push(panel.id)
  }
  return { version: 1, slots, hidden: [] }
}

function loadPersistedLayout(): LayoutState | null {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as LayoutState
    if (parsed.version !== 1) return null
    // Ensure all slot keys exist (in case new slots were added since last save).
    for (const slot of SLOTS) {
      if (!Array.isArray(parsed.slots[slot])) parsed.slots[slot] = []
    }
    if (!Array.isArray(parsed.hidden)) parsed.hidden = []
    return parsed
  } catch {
    return null
  }
}

function saveLayout(state: LayoutState): void {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(state))
}

function removeFromAllSlots(prev: LayoutState, id: string): LayoutState {
  return {
    ...prev,
    slots: Object.fromEntries(
      SLOTS.map((s) => [s, prev.slots[s].filter((i) => i !== id)])
    ) as Record<SlotId, string[]>,
    hidden: prev.hidden.filter((i) => i !== id)
  }
}

// ---------------------------------------------------------------------------
// usePanelLayout hook
// ---------------------------------------------------------------------------

/** Returns true if the panel can be placed in the given slot. */
export function isPanelCompatibleWithSlot(panel: UnifiedPanel, slot: SlotId): boolean {
  const { compatibleSlots } = panel
  return !compatibleSlots || compatibleSlots.includes(slot)
}

export type PanelLayoutApi = {
  /** Resolved panels currently assigned to the given slot. */
  panelsInSlot: (slot: SlotId) => UnifiedPanel[]
  /** Panels that are registered but not displayed in any slot. */
  hiddenPanels: UnifiedPanel[]
  /** All registered panels (built-in + extension), regardless of visibility. */
  allPanels: UnifiedPanel[]
  /** Move a panel into a slot (removes it from wherever it currently is). */
  movePanel: (id: string, slot: SlotId) => void
  /** Remove a panel from its slot without unregistering it. */
  hidePanel: (id: string) => void
  /** Move a hidden panel back into a slot. Equivalent to movePanel. */
  showPanel: (id: string, slot: SlotId) => void
}

export function usePanelLayout(): PanelLayoutApi {
  const extensionPanels = useRegisteredPanels()

  const allPanels: UnifiedPanel[] = useMemo(
    () => [
      ...builtInRegistry.map((p) => ({ ...p, kind: 'builtin' as const })),
      ...extensionPanels.map((p) => ({ ...p, kind: 'extension' as const }))
    ],
    [extensionPanels]
  )

  // baseLayout holds only what is explicitly persisted (user actions).
  // At init time only built-ins are known; extension panels arrive async.
  const [baseLayout, setBaseLayout] = useState<LayoutState>(() => {
    const persisted = loadPersistedLayout()
    if (persisted) return persisted
    return buildDefaultLayout(allPanels)
  })

  // Effective layout: baseLayout extended with any newly registered panels
  // that haven't been placed or hidden yet. Computed without a side-effect so
  // we avoid calling setState inside an effect.
  const layout = useMemo(() => {
    const placed = new Set([...Object.values(baseLayout.slots).flat(), ...baseLayout.hidden])
    const newPanels = allPanels.filter((p) => !placed.has(p.id))
    if (newPanels.length === 0) return baseLayout
    const next: LayoutState = { ...baseLayout, slots: { ...baseLayout.slots } }
    for (const panel of newPanels) {
      next.slots[panel.defaultSlot] = [...next.slots[panel.defaultSlot], panel.id]
    }
    return next
  }, [baseLayout, allPanels])

  // Persist the effective layout whenever it changes. Skip the initial mount
  // (the value was just loaded from / derived without localStorage needing an
  // update) by tracking whether this is the first render.
  const isFirstRender = useRef(true)
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false
      return
    }
    saveLayout(layout)
  }, [layout])

  const findPanel = (id: string): UnifiedPanel | undefined => allPanels.find((p) => p.id === id)

  const panelsInSlot = (slot: SlotId): UnifiedPanel[] =>
    layout.slots[slot].flatMap((id) => {
      const p = findPanel(id)
      return p ? [p] : []
    })

  const hiddenPanels = layout.hidden.flatMap((id) => {
    const p = findPanel(id)
    return p ? [p] : []
  })

  const movePanel = (id: string, slot: SlotId): void => {
    setBaseLayout((prev) => {
      const next = removeFromAllSlots(prev, id)
      next.slots[slot] = [...next.slots[slot], id]
      return next
    })
  }

  const hidePanel = (id: string): void => {
    setBaseLayout((prev) => {
      const next = removeFromAllSlots(prev, id)
      next.hidden = [...next.hidden, id]
      return next
    })
  }

  const showPanel = (id: string, slot: SlotId): void => movePanel(id, slot)

  return { panelsInSlot, hiddenPanels, allPanels, movePanel, hidePanel, showPanel }
}
