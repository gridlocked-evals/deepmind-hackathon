---
name: "Gridlocked"
description: "A New York street operations desk: paper controls around a dark geographic scene."
colors:
  paper: "#f5f4ed"
  ink: "#20272a"
  muted: "#626864"
  line: "#d9dcd3"
  yellow: "#f2c94c"
  yellow-border: "#dbb439"
  yellow-hover: "#f9d878"
  field: "#fffefa"
  field-border: "#bfc5ba"
  focus: "#987110"
  search-focus: "#a98728"
  selected-row: "#e4e7dc"
  signal-green: "#2e7247"
  signal-amber: "#8a6510"
  signal-red: "#b3362d"
  error-surface: "#f6e5dc"
  error-ink: "#82371f"
  error-border: "#b35035"
  map-surface: "#09121b"
  map-focus: "#a4ffe1"
  scene-ink: "#f0f3ed"
  scene-muted: "#c3cbd0"
typography:
  display:
    fontFamily: "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "29px"
    fontWeight: 550
    lineHeight: 1.2
    letterSpacing: "-1px"
  headline:
    fontFamily: "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "22px"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.8px"
  title:
    fontFamily: "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.3px"
  body:
    fontFamily: "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "11px"
    fontWeight: 400
  action:
    fontFamily: "'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "12px"
    fontWeight: 700
  telemetry:
    fontFamily: "'IBM Plex Mono', monospace"
    fontSize: "43px"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "-2px"
  metadata:
    fontFamily: "'IBM Plex Mono', monospace"
    fontSize: "9px"
    fontWeight: 400
rounded:
  tag: "3px"
  control: "4px"
  instrument: "5px"
  panel: "6px"
  dialog: "8px"
  circle: "50%"
spacing:
  tight: "4px"
  icon: "8px"
  control: "10px"
  compact: "12px"
  content: "16px"
  section: "20px"
  rail: "22px"
  canvas: "24px"
components:
  button-primary:
    backgroundColor: "{colors.yellow}"
    textColor: "{colors.ink}"
    typography: "{typography.action}"
    rounded: "{rounded.control}"
    padding: "12px 10px"
    width: "100%"
  button-primary-hover:
    backgroundColor: "{colors.yellow-hover}"
  button-stop:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.action}"
    rounded: "{rounded.control}"
    padding: "12px 10px"
    width: "100%"
  button-secondary:
    textColor: "{colors.ink}"
    rounded: "{rounded.instrument}"
    padding: "0 12px"
  search-field:
    backgroundColor: "{colors.field}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 10px"
  perspective-active:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.tag}"
    padding: "8px 13px"
  rail-navigation:
    textColor: "{colors.muted}"
  camera-row-selected:
    backgroundColor: "{colors.selected-row}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "13px 10px"
    width: "100%"
  driver-status:
    backgroundColor: "#e4e8db"
    textColor: "#5b6552"
    rounded: "{rounded.tag}"
    padding: "4px 6px"
  actor-telemetry:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.instrument}"
    padding: "16px"
    width: "174px"
  playback:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "0 18px"
    height: "68px"
---

# Design System: Gridlocked

## Overview

**Creative North Star: "The New York Street Operations Desk"**

Warm paper, charcoal ink, and taxi yellow frame a dark geographic scene. The interface feels like a compact street operations desk: solid materials, flat rows, thin rules, and clear numbers. Camera imagery and rendered vehicles supply the visual detail.

The map remains the main visual field. Controls use restrained color changes and small functional shadows; the rail provides a consistent reading order for camera selection, route planning, and actual model decisions. The map retains its existing dark controls and mint geographic accents as a separate visual layer.

**Key Characteristics:**
- Warm paper controls with a sparse taxi-yellow action accent.
- DM Sans for interface language; IBM Plex Mono for speed, time, and numeric metadata.
- Flat task rows and measured dividers, with floating instruments above the map.
- One Cameras / Route / Driver rail; a modal overlay at narrow widths.
- Explicit source and state labels for camera media, simulation playback, and model control.

This is an implementation record, extracted from `src/styles.css`, `src/App.tsx`, `src/components/NavigationPanel.tsx`, `src/agent/DriverPanel.tsx`, and `src/map/CityMap.{tsx,css}`. `PRODUCT.md` owns product truth; `.impeccable/briefs/workspace.md` owns this surface's operating brief. The frontmatter is the normative primitive layer. Its spacing and radius entries are extracted repeated values, not claims that equivalent CSS custom properties exist. The sidecar extends those primitives with motion, depth, breakpoints, and renderable components.

Visual references are `.impeccable/review/desktop.jpg` (1440 × 900), `mobile.jpg` and `mobile-map.jpg` (390 × 844), and `user-731.jpg` (731 × 727). Final footer and focus behavior are recorded from current source. Historical research documents remain separate from this design system. Sidecar tonal ramps are synthesized panel references, not additional application tokens.

## Colors

The palette combines warm off-white surfaces, charcoal text, and a taxi-yellow action accent. Muted text and rules lean slightly green, connecting the controls to the street scene without tinting the entire workspace.

### Primary

- **Taxi Yellow** (`yellow`): driver launch, Start Gemini drive, route actions, and map-picking instruction. `yellow-border` gives the filled action a visible edge; `yellow-hover` is the rail-action hover. The smaller topbar driver launch uses its existing lighter hover (`#f8d76c`).
- **Charcoal Ink** (`ink`): primary text, selected perspective and source filters, circular transport control, and Stop drive. Selected controls reverse to `paper` text.

### Neutral

- **Warm Paper** (`paper`): topbar, task rail, telemetry, feed frames, location cards, playback, and help dialog.
- **Quiet Sage Gray** (`muted`): explanatory copy, labels, units, and attribution. **Paper Rule** (`line`) divides tasks and measurements.
- **Field Paper** (`field`) and **Field Edge** (`field-border`): search and unfilled route endpoints. A selected camera row uses `selected-row` plus its marker and `aria-pressed` state.
- **Geographic Night** (`map-surface`): existing map canvas. `scene-ink` and `scene-muted` are the overlaid heading and description. `map-focus` retains the mint keyboard outline on dark map controls.

### State colors

- `signal-green`, `signal-amber`, and `signal-red` accompany written signal names. Green also labels playing camera media; it does not imply model safety.
- Route and model failures use `error-surface`, `error-border`, and `error-ink`, with readable error text and the appropriate live announcement.
- `focus` is the general workspace keyboard outline; `search-focus` belongs to the whole camera-search field. Selected and disabled states must retain their non-color cues.

**The Paper and Map Rule.** Use the warm paper palette for workspace controls and preserve the dark geographic layer. Mint belongs to existing map controls and geographic rendering.

## Typography

**Interface:** DM Sans, with the system sans fallbacks in the frontmatter. **Measurements and metadata:** IBM Plex Mono, falling back to monospace. Both families are requested through the Google Fonts import in `src/styles.css`; the interface remains usable through the declared fallbacks.

The typography is compact and operational. Slight negative tracking sharpens headings and large measurements; supporting text uses ordinary case and loose line height where it needs to be read. There is no modular ratio applied to every role.

| Role | Implemented use |
| --- | --- |
| Display | Main scene heading. Default 29px; 34px from 1700px, 25px at 1150px and below, 22px at 960px and below, and 23px at 760px and below. |
| Headline | Rail heading. Default 22px, reduced to 21px at 960px and below. |
| Title | Camera feed title at 17px; docked variants use 19px, then 16px at 960px and below. Planner title uses 17px; driver title uses 16px / 650. |
| Body | Planner description at 12px / 1.65. Driver decision reasoning uses 13px / 1.7; footnotes use 11px / 1.7. |
| Label and action | Labels cluster around 10–12px. Primary rail buttons use 12px / 700; topbar actions use 12px / 600. |
| Telemetry | Actor speed defaults to 43px, then 35px at 960px and 31px at 760px. Driver metrics use 24px / 500; route metrics use 26px / 500. |
| Metadata | Time, model ID, source facts, and compact counts generally use 9–11px. Small mobile time and feed-state labels use 8px. |

The wordmark is DM Sans 800 with tight tracking, accompanied by the slightly rotated four-square mark. Preserve that brand asset rather than applying its tracking to ordinary text.

**The Numeric Voice Rule.** Use IBM Plex Mono for measurements, clocks, counts, and model identifiers; keep explanations, units, and actions in DM Sans.

## Layout

The workspace fills `100dvh`, with a 520px minimum height and no document-level scrolling inside its frame. The map fills the frame behind absolute-positioned controls. The topbar is 68px high; the desktop task rail starts below it, is 326px wide, and reaches the bottom. Rail content scrolls independently. The default rail gutter is 20–22px; map instruments generally sit 24px from an edge.

The rail presents Cameras, Route, and Driver in a three-column switch. The camera view has fixed search/filter controls, a flexible scrolling list, pagination in groups of 40, and a fixed bottom action. Route and Driver are independently scrollable task bodies. Route metrics use two columns; driver metrics use three columns in a `1fr 1.1fr .8fr` proportion.

Map framing reads the actual bounds of the header, rail, heading, feed, and playback bar. Above 760px the open rail contributes left camera padding, capped at 45% of the map width. Top padding is capped at 34% of height and bottom padding at 48%. Do not replace this measured behavior with a fixed assumed visible viewport.

| Breakpoint | Implemented changes |
| --- | --- |
| At least 1700px | Rail grows to 350px; camera rows gain vertical space; docked feed grows to 250px high. |
| At most 1150px | Header and transport gaps tighten; help button is hidden; some feed description text is hidden. |
| At most 960px | Rail is 296px; perspective icons disappear; driver launch becomes icon-only; telemetry is narrower. The scene remains alongside the rail. |
| At most 760px | Header becomes 58px. Rail becomes an overlay with width `min(320px, calc(100% - 34px))`. Perspective controls move to a centered second row at top 73px; the scene heading starts at 135px. The rail is initially closed and, when open, is a modal dialog with a backdrop. |
| At most 370px | Wordmark and topbar gaps tighten further; the topbar camera toggle is hidden. |

On narrow screens the playback bar spans the map with 12px side margins, a 65px height, and its final 50px bottom inset. The docked feed has a 129px bottom inset and a 232px expanded height; its header-only state is 39px high. The caption sits 27px above the bottom and MapLibre attribution sits 1px above the bottom. Keep these final overrides together when changing footer geometry.

The playback identity copy and ordinary replay-reset button are hidden on mobile; the driver replay-reset control remains. Mobile docked feeds show media and header controls, hiding the supplemental information column. Camera selection, perspective changes, map picking, and driver start dismiss the rail below 760px; View Gemini decisions reopens it. Current source uses `<= 760` for rail modality/CSS but `< 760` for feed auto-docking and these action dismissals; the exact 760px boundary is an implementation discrepancy, not a new design rule.

## Elevation & Depth

The rail and topbar are flat, opaque paper with thin rules. Shadows distinguish instruments floating over the map and dialogs that cover the workspace. There is no backdrop blur. The timeline's hard-stop gradient is a functional progress fill, not a decorative surface gradient.

| Surface | Observed shadow |
| --- | --- |
| Playback | `0 3px 20px #0002` |
| Layers menu | `0 8px 22px #0003` |
| Camera feed | `0 8px 30px #0003` |
| Location card | `0 10px 30px #0003` |
| Mobile perspective control | `0 3px 18px #0003` |
| Mobile rail | `14px 0 35px #0003` |
| Help dialog | `0 30px 80px #0004` |

Desktop stacking is deliberate: scene heading 3, telemetry 5, feed 8, playback 10, Layers 12, location/pick instruction 13, rail 15, and topbar 20. On mobile the rail is 30 over backdrop 29; help is 50. The model and camera media do not gain decorative glows to indicate authority.

**The Flat Rail Rule.** Keep the rail flat. Use separators and spacing inside it; reserve shadows for surfaces floating over the geographic scene.

## Shapes

Use small, purposeful corner radii: tags and selected segments use 3px, ordinary fields and rail actions 4px, small floating instruments 5px, larger feed/playback/location panels 6px, and the help dialog 8px. Transport play/stop controls and status dots are circles. Existing dark map control groups retain their 7px corner radius.

Most borders are 1px. Active rail tabs use a 2px underline; the selected camera row carries a 3px left marker in addition to its surface change. Icons are simple Lucide line glyphs, usually 13–20px. Do not turn every shape into a pill; circular geometry belongs to transport and state indicators.

## Components

### Actions and controls

The primary rail action is a full-width taxi-yellow button with a 1px yellow edge. Driver Start uses 12px 10px padding; route and explore actions use 11px 12px. Stop drive uses the same form in charcoal with paper text. Traffic demo is a restrained outlined topbar action, with a 36px minimum height on desktop and 33px on mobile. Disabled buttons use native `disabled`, 0.45 opacity, and `not-allowed` cursor.

General buttons transition background and text color over 150ms using the CSS default `ease`; they do not bounce, scale, or translate. The main icon button is 34px square, while feed and mobile variants range from 24px to 31px and map navigation is 40px square. These are observed sizes, not a universal target-size guarantee.

Keyboard focus is a 2px outline with a 4px offset. The search wrapper uses a 2px outline with a 2px offset and suppresses the redundant input outline. The map uses its separate mint outline inside the canvas/control bounds.

### Task and perspective navigation

Perspective is a solid segmented control labeled Map, Intersection, Follow. The active segment reverses to charcoal and paper. Rail navigation remains flat with an active underline. Source filters use compact filled selected buttons with count metadata. All three patterns are native buttons with `aria-pressed`, not ARIA tabs; do not imply arrow-key tablist behavior that is not implemented.

### Search and camera rows

Camera search uses a paper field, a leading search icon, and a slash key hint. Borough selection is a native select. Results are flat full-width rows with source icon, two-line text hierarchy, and an end chevron or selected dot. Selection has a pale surface, a left marker, and `aria-pressed`. Counts announce politely, and empty/filter/offline states explain what the user can try next.

Route endpoints can be entered through camera search or Pick on map. Unselected fields use the same field material; a chosen location becomes a tinted value row with a labeled clear action. The Find driving route action is disabled until both endpoints exist, and while the request is loading. Route errors appear in a polite live region.

### Driver instrument

The driver panel contains a heading/status badge, three measurements, the latest real decision, Start/Stop, a model identifier, optional decision history, and a provenance footnote. Thinking and driving badges use pale yellow; inactive status uses a muted sage surface. The text distinguishes ready, thinking, driving, stopped, complete, and interrupted states.

The status heading is a live `status`; model errors use `alert`. Model availability and missing prerequisites are explained before a disabled start action, with Check connection when appropriate. During thinking the simulation is paused. A run is limited to 12 decisions and up to 36 simulated seconds; failure leaves the error visible rather than inventing a decision. Stop cancels active work. Starting selects Follow, restores vehicle/route visibility, and collapses the camera feed. Decision history uses native `details`/`summary`.

### Telemetry, playback, and camera feed

Telemetry is a small paper instrument with one large monospaced speed, a readable unit, and named actor/signal context. Replay has a circular play/pause control, reset, cyclic 0.5× / 1× / 2× speed, and a labeled native range input. During model control, the timeline is disabled, the transport becomes Stop, the state text identifies model control, and the decisions button opens Driver. Camera media runs on a separate clock and can be pinned, collapsed, expanded, or hidden. The feed distinguishes video from periodic images and keeps its source label and current media state in the header.

### Dialogs and keyboard behavior

At 760px and below, opening the rail focuses its container, marks covered workspace children inert, contains Tab and Shift+Tab, and exposes `role="dialog"`, `aria-modal`, and the rail heading label. Escape closes it; cleanup restores the previous inert states and focus to the opener if still connected. Its backdrop and collapse control also dismiss it. Desktop rail remains an aside.

The help dialog focuses its close button, traps Tab, restores prior focus, and supports Escape and backdrop dismissal. Its implementation is separate from the mobile rail; do not infer background `inert` behavior for help. Layers exposes `aria-expanded`; layer toggles expose `aria-pressed`. Escape also dismisses Layers, selected-location cards, and map-picking instructions.

Keyboard shortcuts outside form/button targets are Space for replay play/pause or driver stop, C for camera-overlay visibility, 1/2/3 for perspective, and / for camera search. Interactive regions, icon actions, timeline, and route endpoints have accessible names. Signals, model state, and errors carry text in addition to color.

### Motion and reduced motion

Map transitions preserve geographic context: perspective changes use 1500ms; camera selection 1050ms; route fitting 750ms; focused location 650ms; overlay-padding adjustment 300ms; cluster expansion 350ms. Map control repositioning uses 300ms ease. Follow uses continuous heading/position tracking with a 155ms smoothing constant; it remains functional motion.

With `prefers-reduced-motion: reduce`, interface transitions and smooth scrolling are disabled, the 1s loading orbit and 1.3s map-status pulse stop, and map flight durations become zero. Follow smoothing becomes immediate. This does not pause the simulated vehicles or change playback state; those remain under explicit controls.

## Do's and Don'ts

### Do:
- Do keep one shared task rail with a visible active underline and text label.
- Do use taxi yellow for the next consequential action and charcoal for Stop drive or a selected perspective.
- Do retain visible keyboard focus, pressed and expanded states, and readable loading or failure copy.
- Do preserve the mobile rail focus trap, inert background, Escape dismissal, and focus restoration.
- Do keep camera provenance and simulated-motion labels distinct from real model decisions.
- Do account for measured overlays when framing the map and keep mobile captions clear of attribution.
- Do disable interface transitions and loading animation for reduced motion while preserving functional simulation controls.

### Don't:
- Don't replace flat paper controls with glass, decorative gradients, or repeated dashboard cards.
- Don't use mint as a new workspace action color or spread taxi yellow across every surface.
- Don't turn the driver panel into a chat transcript; show status, measurements, the latest decision, and optional history.
- Don't fabricate a model decision, substitute a replay for a failed model response, or imply that camera images drive this simulation.
- Don't style all interface text as monospace or enlarge metadata into competing headlines.
- Don't claim every control has a 44px target or that this extraction constitutes a complete accessibility audit.
