import { forwardRef } from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'

export const SearchBar = forwardRef<HTMLInputElement>(function SearchBar(_, ref) {
  const query = useStore((s) => s.searchQuery)
  const setSearchQuery = useStore((s) => s.setSearchQuery)
  const tooltip = useTooltip()

  return (
    <div className="search-bar">
      <span className="search-icon">⌕</span>
      <input
        ref={ref}
        className="search-input"
        type="text"
        placeholder="Search…"
        value={query}
        onChange={(e) => void setSearchQuery(e.target.value)}
        aria-label="Search library"
      />
      {query && (
        <button
          className="search-clear"
          onClick={() => void setSearchQuery('')}
          {...tooltip(TOOLTIPS.SEARCH_CLEAR)}
          aria-label="Clear search"
        >
          ✕
        </button>
      )}
    </div>
  )
})
