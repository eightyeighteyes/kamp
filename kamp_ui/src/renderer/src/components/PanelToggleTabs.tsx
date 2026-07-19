import React from 'react'
import { useStore } from '../store'
import { PanelToggleTab } from './PanelToggleTab'

// Reopen handles for the Collection and Queue panels, anchored to the app-body
// edges. Shown ONLY while a panel is collapsed — an open panel renders its own
// inner-edge toggle (which tracks a resize drag). Rendered outside the panel
// visibility gate so a collapsed panel can still be reopened. KAMP-612.
export function PanelToggleTabs(): React.JSX.Element {
  const activeView = useStore((s) => s.activeView)
  const collectionPanelVisible = useStore((s) => s.collectionPanelVisible)
  const toggleCollectionPanel = useStore((s) => s.toggleCollectionPanel)
  const queueVisible = useStore((s) => s.queueVisible)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)

  return (
    <>
      {/* Collection panel only exists in the library view. */}
      {activeView === 'library' && !collectionPanelVisible && (
        <PanelToggleTab
          panel="collection"
          placement="edge"
          active={false}
          onClick={toggleCollectionPanel}
        />
      )}
      {/* Queue is available in every view. */}
      {!queueVisible && (
        <PanelToggleTab panel="queue" placement="edge" active={false} onClick={toggleQueuePanel} />
      )}
    </>
  )
}
