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

---

## Theme System

**Theme** — A named color palette applied globally via CSS custom properties on `<html>`. The default theme is *kamp*; additional themes are named after bands. Defined in `shared/theme.ts`, which is the single source of truth — `applyTheme()` sets all tokens as inline styles on mount and on switch.

**Theme token** — A semantic color role used throughout the stylesheet. Tokens are CSS custom properties (e.g. `var(--accent)`). The full set:

| Token | CSS property | Role |
|---|---|---|
| `bg` | `--bg` | Outermost window background. Must match `BrowserWindow.backgroundColor` to avoid edge-color flash on resize. |
| `surface` | `--surface` | Cards, panels, sidebars, transport bar — one step lighter than bg. |
| `surfaceHover` | `--surface-hover` | Hover state for interactive surface elements (album cards, list rows). |
| `border` | `--border` | Dividers, panel edges, input borders. |
| `text` | `--text` | Primary body text — track titles, album names, labels. |
| `textDim` | `--text-dim` | Secondary / muted text — artist names in rows, timestamps, metadata. |
| `accent` | `--accent` | The theme's signature color. Selected states, active indicators, album artist in the hero, play button. |
| `accentDim` | `--accent-dim` | Accent as a dark background tint — selected row fill, badge backgrounds. A very dark shade of the accent. |
| `textOnAccent` | `--text-on-accent` | Text rendered on a filled accent surface (e.g. play button label). Black or white depending on accent luminance. |

**Style Rail** — A collapsible settings bar that appears above the content area. Houses visual preference controls: the theme picker and highlight style settings. Toggled by the palette icon in the nav bar.

**Theme picker** — The row of swatch buttons inside the Style Rail. Each swatch shows three color strips (bg / accent / surface) and a liner-note tooltip. Selecting a swatch applies the theme immediately.

---

## Status Rail

**Status Rail** — The group of ambient status indicators mounted in the nav bar, between the search bar and the panel picker. Visible on every view. Currently contains:

- **Pipeline indicator** — Shows whether the import pipeline is active. Dims when idle; pulses with the accent color when processing.
- **Bandcamp button** — Shows when a Bandcamp account is connected. Click to trigger a sync; right-click to open Bandcamp preferences. Hidden when no account is configured.
