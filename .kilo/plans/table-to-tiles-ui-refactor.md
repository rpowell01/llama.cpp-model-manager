# Plan: Modify web app interface from table/rows to tiles

## Goal
Replace the current HTML `<table>`/row-based model list with a modern **tile / card grid** layout while preserving all existing functionality (start/stop, download, delete, add-model, search, status polling).

## Current state (app.py)
- Model list rendered as `<table>` with `<tbody id="tbody">`.
- JavaScript `refresh()` builds `<tr>` rows with columns:
  `# | Status | Model | File | File size | Port | Actions`
- CSS uses table selectors (`table`, `th`, `td`, `tr.running`, `tr:hover`).
- All controls embedded inside each table `<td>`.

## Target state
- Model list rendered as a responsive **CSS Grid** of tiles (cards).
- Each tile shows: model name, status chip, filename, file size, port link, action buttons.
- No table markup remains in the model list. Table CSS is removed/replaced.

## Tasks (beads)

### 1. Replace HTML markup (currently `<table>…<tbody id="tbody">`)
**Bead title:** `web-ui: convert model list <table> to tile grid container`

**Scope:**
- Remove `<table> <thead>…</thead> <tbody id="tbody"> </table>` block.
- Replace with `<div id="tile-grid" class="tile-grid"></div>` placed in the same `main` context.
- Keep `#add-model-form`, `#loading`, `#download-modal`, and `#log` blocks unchanged.

**Acceptance criteria:**
- Old `<table>` elements deleted; no broken references.
- New container exists and is empty on initial render.

### 2. Update CSS (tile grid + card styles)
**Bead title:** `web-ui: add CSS for tile/grid layout and replace table styles`

**Scope:**
- Introduce `.tile-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; }`
- Add `.tile { background: var(--surface); border: 1px solid var(--surface2); border-radius: 8px; padding: 1rem; display: flex; flex-direction: column; gap: .6rem; }`
- Add `.tile-header`, `.tile-body`, `.tile-actions` sub-layout helpers.
- Preserve existing color vars (`--green`, `--red`, `--yellow`, `--blue`, etc.).
- Remove or neutralize old table-specific styles (`table`, `th`, `td`, `tr.running`, `tr:hover`) so they no longer affect layout.

**Acceptance criteria:**
- Tiles render as a fluid grid on desktop and collapse to 1-col on narrow screens.
- Running state still has a subtle visual indicator (e.g. left border or opacity).

### 3. Rewrite JavaScript render logic
**Bead title:** `web-ui: replace refresh() row rendering with tile rendering`

**Scope:**
- In `refresh()`, replace the `.map(...).join('')` that produces `<tr>` strings with tile markup.
- Each tile should contain a header (model name + status chip), a details row (file, size, port), and an action footer (Start/Stop/Add Variant/Delete).
- Keep all existing onclick bindings (`act()`, `downloadModel()`, `downloadByFilename()`, `promptDeleteModel()`, `retryDownload()`, `resumeDownload()`) intact.
- Keep the filtering/search behavior identical.

**Acceptance criteria:**
- Visual parity for every per-model action button and indicator.
- Search filter still works without modification (since it filters `rows` before rendering).
- Console logs still verify same counts (`Received N models`).

### 4. Move download progress bars into their related tiles
**Bead title:** `web-ui: display download progress bars inside their model tiles`

**Scope:**
- Update `showProgressBar()` so the progress DOM is created **inside the matching tile** instead of before `#log`.
- Key change: resolve the tile element (e.g. via `document.querySelector(\`.tile[data-model="${safeId}\"]\`)`) or use an explicit `data-model` attribute on each tile to anchor insertion.
- Replace current fallback append-to-`main` behavior with tile-scoped insertion: insert progress markup as a child of the tile footer/body so progress lives inside the card.

**Acceptance criteria:**
- Each active download's progress bar is visually contained within its tile.
- Progress still animates and removes correctly after completion (existing SSE flow unchanged).
- No progress bars escape outside tiles on any layout.

### 4b. Fix progress bar disappearing from tile ✅ DONE
**Bead title:** `web-ui: fix progress bar disappearing after initial tile display`
**Bead ID:** web-bg9

**Problem:** Progress bar is appended to tile by `showProgressBar()` but disappears when `refresh()` re-renders all tiles. The `innerHTML` replacement wipes out dynamically added progress bars.

**Solution:** 
- Added `const activeDownloads = new Set()` to track active downloads
- Modified `showProgressBar()` to add model to the Set
- Modified `refresh()` to re-inject progress bars after tiles are rendered
- Modified SSE listener to remove models from Set on completion/error

**Note:** Progress bars are now redrawn during each refresh, which causes a visual flicker. See bead `web-tgc` for alternative approaches.

### 4c. Improve progress bar persistence across refreshes ✅ DONE
**Bead title:** `web-ui: implement smoother progress bar persistence across page refreshes`
**Bead ID:** web-tgc

**Problem:** Progress bars were redrawn during each `refresh()` call, causing visual flicker and a poor user experience.

**Solution implemented:**
1. Added `downloadProgressState = {}` object to store progress state client-side
2. Modified `refresh()` to render progress bars inside tiles using stored state
3. Modified `showProgressBar()` to initialize state instead of creating DOM elements
4. Modified SSE listener to update both DOM elements AND stored state
5. Used consistent ID sanitization (`model.replace(/[^a-zA-Z0-9]/g, '_')`) for element IDs
6. Added XSS sanitization for progress details text

**Result:** Progress bars persist across refreshes without visual flicker since they are rendered as part of the tile HTML, not dynamically injected.

**Scope:**
- Track active downloads and preserve their progress bars across refresh cycles, OR
- Store progress state externally and re-inject progress bars after each render, OR
- Prevent tiles with active downloads from being fully replaced during refresh.

**Acceptance criteria:**
- Progress bars persist while download is active.
- Progress bars are removed cleanly when download completes.

### 5. Responsive and regression verification
**Bead title:** `web-ui: verify tile UI on mobile widths and regression`

**Scope:**
- Confirm `auto-fill` grid collapses cleanly at <=640px (and via the existing media query).
- Confirm all buttons remain tappable (no loss of padding).
- Confirm existing features: start, stop, download, add variant, delete, add-model form toggle, logs link.

**Acceptance criteria:**
- No broken buttons on narrow screens.
- All 5+ user flows complete without error.

## Summary of edits
All changes are confined to the inline `TEMPLATE` string inside `app.py`:
1. Remove `<table>` block (≈ lines 1941–1949).
2. Add new `.tile-grid` / `.tile` CSS rules and remove/redundant-ize old table CSS (≈ within the `<style>` block, lines 1728–1874).
3. Replace row-building JS (≈ lines 2022–2061) with tile-building JS.
4. Update `showProgressBar()` (≈ lines 2315–2351) to insert progress into the corresponding tile.
4. **New:** Bead 4b added to track progress bar disappearing issue caused by `refresh()` re-rendering.
5. No backend endpoints are changed. No new dependencies. No new files.
