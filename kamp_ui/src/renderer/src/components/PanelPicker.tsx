/**
 * PanelPicker: floating popover that lets users add, remove, and reposition
 * panels. Accessible from the gear button in the nav bar.
 */

import React, { useEffect, useRef, useState } from 'react'
import { isPanelCompatibleWithSlot } from '../hooks/usePanelLayout'
import type { PanelLayoutApi, UnifiedPanel } from '../hooks/usePanelLayout'
import type { SlotId } from '../../../shared/kampAPI'

const SLOT_LABELS: Record<SlotId, string> = {
  main: 'Main',
  left: 'Left sidebar',
  right: 'Right sidebar',
  bottom: 'Bottom bar'
}

function PanelRow({
  panel,
  currentSlot,
  onMove,
  onHide
}: {
  panel: UnifiedPanel
  currentSlot: SlotId | 'hidden'
  onMove: (slot: SlotId) => void
  onHide: () => void
}): React.JSX.Element {
  const slots: SlotId[] = ['main', 'left', 'right', 'bottom']

  return (
    <div className="panel-picker-row">
      <span className="panel-picker-name">{panel.title}</span>
      <div className="panel-picker-controls">
        {slots.map((slot) => {
          const compatible = isPanelCompatibleWithSlot(panel, slot)
          return (
            <button
              key={slot}
              title={compatible ? SLOT_LABELS[slot] : `${SLOT_LABELS[slot]} (incompatible)`}
              className={
                'panel-picker-slot-btn' +
                (currentSlot === slot ? ' active' : '') +
                (!compatible ? ' disabled' : '')
              }
              disabled={!compatible}
              onClick={() => (currentSlot === slot ? onHide() : onMove(slot))}
            >
              {slotIcon(slot)}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function slotIcon(slot: SlotId): string {
  switch (slot) {
    case 'main':
      return '⊟'
    case 'left':
      return '⊢'
    case 'right':
      return '⊣'
    case 'bottom':
      return '⎵'
  }
}

export function PanelPicker({ layout }: { layout: PanelLayoutApi }): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // Close when clicking outside
  useEffect(() => {
    if (!open) return
    function onPointerDown(e: PointerEvent): void {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  // Build a map of panel ID → current slot (or 'hidden')
  const slotOf = (id: string): SlotId | 'hidden' => {
    const slots: SlotId[] = ['main', 'left', 'right', 'bottom']
    for (const slot of slots) {
      if (layout.panelsInSlot(slot).some((p) => p.id === id)) return slot
    }
    return 'hidden'
  }

  return (
    <div className="panel-picker-anchor" ref={ref}>
      <button
        className={'panel-picker-trigger' + (open ? ' active' : '')}
        title="Manage panels"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="true"
      >
        ⚙
      </button>
      {open && (
        <div className="panel-picker-popover" role="dialog" aria-label="Manage panels">
          <div className="panel-picker-header">Panels</div>
          <div className="panel-picker-legend">
            <span title="Main">⊟ Main</span>
            <span title="Left sidebar">⊢ Left</span>
            <span title="Right sidebar">⊣ Right</span>
            <span title="Bottom bar">⎵ Bottom</span>
          </div>
          {layout.allPanels.map((panel) => (
            <PanelRow
              key={panel.id}
              panel={panel}
              currentSlot={slotOf(panel.id)}
              onMove={(slot) => layout.movePanel(panel.id, slot)}
              onHide={() => layout.hidePanel(panel.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
