# Kamp Design Lexicon

A shared vocabulary for UI components, layout patterns, and interaction concepts. Use these terms consistently in code, tickets, and conversation.

---

## Module System

**Module** — A self-contained panel displayed on the Home screen. Each module fetches its own data and renders independently. Users configure which modules appear and their order in Preferences → Home.

**Module view** — The layout mode a module uses to present its content. Currently two views exist:

- **Grid** — Album cards arranged in a wrapping multi-column layout. Cards reflow as the panel width changes. Used by: Last Played.
- **Shelf** — A single horizontally scrollable row of album cards. Analogous to a Netflix row or record store shelf. Not yet implemented.

---

## Cards

**Album card** — A rectangular tile representing a single album. Displays cover art, title, artist, and year. Supports click-to-navigate, drag-to-queue, right-click context menu, and a now-playing badge.

---

## Navigation

**Home** / **Base Kamp** — The default landing view. Hosts the module system.

**Library** — The full album grid with sort and search controls.

**Now Playing** — Full-screen view centered on the current track.

---

## Transport

**Transport** — The persistent playback bar fixed at the bottom of the app. Contains play/pause, skip, scrubber, volume, and queue toggle.
