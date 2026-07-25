import React from 'react'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { CollectionIcon, QueueIcon } from './TransportIcons'

// Reopen handle for the Collection and Queue panels (KAMP-612). A child of the
// app body, resting against the window edge — shown only while the panel is
// collapsed (an open panel is closed via the X in its own header, KAMP-616).
interface PanelToggleTabProps {
  panel: 'collection' | 'queue'
  onClick: () => void
}

export function PanelToggleTab({ panel, onClick }: PanelToggleTabProps): React.JSX.Element {
  const tooltip = useTooltip()
  const isCollection = panel === 'collection'
  return (
    <button
      className={`panel-tab panel-tab--${panel}`}
      onClick={onClick}
      {...tooltip(isCollection ? TOOLTIPS.COLLECTION_TOGGLE : TOOLTIPS.TRANSPORT_QUEUE)}
      aria-label={isCollection ? 'Collection' : 'Queue'}
    >
      {isCollection ? <CollectionIcon size={18} /> : <QueueIcon size={18} />}
    </button>
  )
}
