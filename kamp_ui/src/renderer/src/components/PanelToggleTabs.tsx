import React from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { CollectionIcon, QueueIcon } from './TransportIcons'

// Floating rounded toggle tabs anchored to the bottom edges of the app body.
// They render independently of the panels themselves so they stay clickable
// when a panel is collapsed — collapsed panels are unmounted entirely, so the
// panel's own chrome can't host the toggle. When a panel is open the tab sits at
// that panel's inner edge (overlapping the library) and tracks a resize drag;
// when closed it rests against the app edge as the reopen handle. KAMP-612.
export function PanelToggleTabs(): React.JSX.Element {
  const tooltip = useTooltip()
  const activeView = useStore((s) => s.activeView)
  const collectionPanelVisible = useStore((s) => s.collectionPanelVisible)
  const toggleCollectionPanel = useStore((s) => s.toggleCollectionPanel)
  const collectionPanelWidth = useStore((s) => s.collectionPanelWidth)
  const queueVisible = useStore((s) => s.queueVisible)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)
  const queuePanelWidth = useStore((s) => s.queuePanelWidth)

  return (
    <>
      {/* Collection panel only exists in the library view. */}
      {activeView === 'library' && (
        <button
          className={`panel-tab panel-tab--collection${collectionPanelVisible ? ' active' : ''}`}
          style={{ left: collectionPanelVisible ? collectionPanelWidth : 0 }}
          onClick={toggleCollectionPanel}
          {...tooltip(TOOLTIPS.COLLECTION_TOGGLE)}
          aria-label="Collection"
          aria-pressed={collectionPanelVisible}
        >
          <CollectionIcon size={18} />
        </button>
      )}
      {/* Queue is available in every view. */}
      <button
        className={`panel-tab panel-tab--queue${queueVisible ? ' active' : ''}`}
        style={{ right: queueVisible ? queuePanelWidth : 0 }}
        onClick={toggleQueuePanel}
        {...tooltip(TOOLTIPS.TRANSPORT_QUEUE)}
        aria-label="Queue"
        aria-pressed={queueVisible}
      >
        <QueueIcon size={18} />
      </button>
    </>
  )
}
