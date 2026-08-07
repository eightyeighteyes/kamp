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
  COLLECTION_TOGGLE: 'Collection (C)',
  TRANSPORT_FAVORITE_ADD: 'Add to favorites',
  TRANSPORT_FAVORITE_REMOVE: 'Remove from favorites',
  TRANSPORT_MUTE: 'Mute',
  TRANSPORT_UNMUTE: 'Unmute',

  // Library / track list actions
  LIBRARY_FETCH_MB: 'Fetch tags from MusicBrainz',
  LIBRARY_FETCH_ART: 'Fetch album art',
  LIBRARY_ADD_TO_QUEUE: 'Add to queue',
  LIBRARY_PLAY_NEXT: 'Play next',

  // Queue panel
  QUEUE_CLOSE: 'Close queue',
  QUEUE_ALBUM_VIEW: 'Album Queue',
  COLLECTION_CLOSE: 'Close collection',

  // Panel management / preferences
  PREFERENCES: 'Preferences',
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

  // Downloads
  DOWNLOADS_VIEW: 'Downloads',

  // The Crate (KAMP-650). The clerk card's tooltip is the plain mechanical
  // answer to "why am I seeing this?" — the shelf tag is the short version.
  CRATE_WHY: 'Picked from your own library and listening — nothing leaves this machine',
  CRATE_WISHLIST_SOON: 'Wishlisting from Kamp is coming — copy the link for now',
  CRATE_PREVIEW: 'Play a preview (Space) — your queue stays put',
  CRATE_COPY: 'Copy link (C)',
  CRATE_PASS: 'Pass (X)',

  // Track / album favorites
  ALBUM_FAVORITE_ADD: 'Add to favorites',
  ALBUM_FAVORITE_REMOVE: 'Remove from favorites'
} as const
