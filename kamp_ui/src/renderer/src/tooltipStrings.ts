export const TOOLTIPS = {
  // Transport controls
  TRANSPORT_PLAY: 'Play (Space)',
  TRANSPORT_PAUSE: 'Pause (Space)',
  TRANSPORT_STOP: 'Stop',
  TRANSPORT_PREV: 'Previous (←)',
  TRANSPORT_NEXT: 'Next (→)',
  TRANSPORT_SHUFFLE: 'Shuffle',
  TRANSPORT_REPEAT: 'Repeat',
  TRANSPORT_QUEUE: 'Queue (Q)',
  TRANSPORT_FAVORITE_ADD: 'Add to favorites',
  TRANSPORT_FAVORITE_REMOVE: 'Remove from favorites',

  // Library / track list actions
  LIBRARY_FETCH_MB: 'Fetch tags from MusicBrainz',
  LIBRARY_FETCH_ART: 'Fetch album art',
  LIBRARY_ADD_TO_QUEUE: 'Add to queue',
  LIBRARY_PLAY_NEXT: 'Play next',

  // Queue panel
  QUEUE_CLOSE: 'Close queue',

  // Panel management
  PANEL_PICKER_MANAGE: 'Manage panels',
  PANEL_MODULE_DRAG: 'Drag to reorder, right-click for options',
  PANEL_MODULE_REMOVE: 'Remove module',
  PANEL_VIEW_CUSTOMIZE: 'Customize',
  PANEL_VIEW_DONE: 'Done',

  // Metadata
  META_COPY: 'Copy to clipboard',
  META_WILL_REORGANIZE: 'Will reorganize when playback ends',

  // Bandcamp
  BANDCAMP_SYNC: 'Sync Bandcamp library',
  BANDCAMP_SYNCING: 'Bandcamp sync in progress…',
  BANDCAMP_RECONNECT: 'Log in to Bandcamp',

  // Search
  SEARCH_CLEAR: 'Clear search',

  // Track / album favorites
  ALBUM_FAVORITE_ADD: 'Add to favorites',
  ALBUM_FAVORITE_REMOVE: 'Remove from favorites'
} as const
