import React from 'react'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import { CollectionIcon, QueueIcon } from './TransportIcons'

// Shared rounded toggle tab for the Collection and Queue panels (KAMP-612).
// Rendered in two places with different positioning:
//  - "inner": a child of the panel, poking over the library at its inner edge —
//    it shares the panel's layout box, so it tracks a resize drag exactly.
//  - "edge": a child of the app body, resting against the window edge — the
//    reopen handle shown while the panel is collapsed (and thus unmounted).
interface PanelToggleTabProps {
  panel: 'collection' | 'queue'
  placement: 'inner' | 'edge'
  active: boolean
  onClick: () => void
}

export function PanelToggleTab({
  panel,
  placement,
  active,
  onClick
}: PanelToggleTabProps): React.JSX.Element {
  const tooltip = useTooltip()
  const isCollection = panel === 'collection'
  return (
    <button
      className={`panel-tab panel-tab--${panel} panel-tab--${placement}${active ? ' active' : ''}`}
      onClick={onClick}
      {...tooltip(isCollection ? TOOLTIPS.COLLECTION_TOGGLE : TOOLTIPS.TRANSPORT_QUEUE)}
      aria-label={isCollection ? 'Collection' : 'Queue'}
      aria-pressed={active}
    >
      {isCollection ? <CollectionIcon size={18} /> : <QueueIcon size={18} />}
    </button>
  )
}
