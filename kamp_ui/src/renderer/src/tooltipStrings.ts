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

  // Crate (KAMP-650). The clerk card's tooltip is the plain mechanical
  // answer to "why am I seeing this?" — the shelf tag is the short version.
  // "and listening" was wrong for a genre pick (taste_genres has no play signal
  // at all) and wrong twice over for a chart pick, which reads nothing about you.
  // "Chosen here" is the claim that holds for every card: the picking happens on
  // this machine whatever it was based on, which is the part worth promising.
  CRATE_WHY: 'Chosen here from what is on your shelves — nothing leaves this machine',
  // Names Bandcamp explicitly: this writes to the account, not to anything local.
  CRATE_WISHLIST: 'Add to your Bandcamp wishlist (W)',
  CRATE_UNWISHLIST: 'Remove from your Bandcamp wishlist (W)',
  CRATE_PURCHASED: 'You brought this one home',
  // No CRATE_PREVIEW / CRATE_COPY / CRATE_BANDCAMP: the header's icon row was
  // removed, so those three have no button left to hang a tooltip on. Preview is
  // Space and the deck's own transport, copy is C.

  // Track / album favorites
  ALBUM_FAVORITE_ADD: 'Add to favorites',
  ALBUM_FAVORITE_REMOVE: 'Remove from favorites'
} as const
