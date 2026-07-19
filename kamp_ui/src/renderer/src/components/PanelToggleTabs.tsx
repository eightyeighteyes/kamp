import React from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { CollectionIcon } from './TransportIcons'

// Floating rounded toggle tabs anchored to the bottom edges of the app body.
// They render independently of the panels themselves so they stay clickable
// when a panel is collapsed — collapsed panels are unmounted entirely, so the
// panel's own chrome can't host the toggle. KAMP-612.
export function PanelToggleTabs(): React.JSX.Element {
  const tooltip = useTooltip()
  const activeView = useStore((s) => s.activeView)
  const collectionPanelVisible = useStore((s) => s.collectionPanelVisible)
  const toggleCollectionPanel = useStore((s) => s.toggleCollectionPanel)

  return (
    <>
      {/* Collection panel only exists in the library view. */}
      {activeView === 'library' && (
        <button
          className={`panel-tab panel-tab--collection${collectionPanelVisible ? ' active' : ''}`}
          onClick={toggleCollectionPanel}
          {...tooltip(TOOLTIPS.COLLECTION_TOGGLE)}
          aria-label="Collection"
          aria-pressed={collectionPanelVisible}
        >
          <CollectionIcon size={18} />
        </button>
      )}
    </>
  )
}
