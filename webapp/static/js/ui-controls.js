/**
 * UI rendering - tiles and panels
 */

/**
 * Tile display preferences.
 *
 * `sortOrder` controls the tile ordering:
 *   - "name"        alphabetical (default)
 *   - "speed"       Speed rating, highest first
 *   - "coding"      Coding rating, highest first
 *   - "speed-coding" Speed rating first, Coding rating as tie-breaker
 *
 * `hideSpecModels` hides tiles for models flagged as speculative decoding
 * models, which are only ever used as a draft model by their parent.
 *
 * Persisted in localStorage so the choice survives a page reload.
 */
const TILE_PREFS_KEY = "tile_prefs";

// Single source of truth for sort orders - values and their labels
const TILE_SORT_ORDERS = [
  { value: "name", label: "Alphabetical (A–Z)" },
  { value: "speed", label: "Speed rating (high → low)" },
  { value: "coding", label: "Coding rating (high → low)" },
  { value: "speed-coding", label: "Speed + Coding (high → low)" },
];

const TILE_SORT_VALUES = TILE_SORT_ORDERS.map((o) => o.value);

function loadTilePrefs() {
  const fallback = { sortOrder: "name", hideSpecModels: false };
  try {
    const raw = localStorage.getItem(TILE_PREFS_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return {
      sortOrder: TILE_SORT_VALUES.includes(parsed?.sortOrder)
        ? parsed.sortOrder
        : fallback.sortOrder,
      hideSpecModels: !!parsed?.hideSpecModels,
    };
  } catch (e) {
    console.warn("Failed to load tile preferences:", e);
    return fallback;
  }
}

let tilePrefs = loadTilePrefs();

/**
 * Format a number with locale formatting, then escape for HTML output.
 * Uses the global `he` from core.js for escaping.
 * @param {number|null|undefined} num - Number to format and escape
 * @returns {string} HTML-escaped formatted number, or 'N/A' for null/undefined
 */
function heNum(num) {
  if (num === null || num === undefined) return 'N/A';
  return he(Number(num).toLocaleString());
}

/**
 * Proxy health badge types and their display properties
 */
const PROXY_HEALTH_BADGES = {
  error: { label: 'Error', color: 'var(--red)', bg: 'var(--red)20', border: 'var(--red)', icon: '❌' },
  guard: { label: 'Guard Active', color: 'var(--orange)', bg: 'var(--orange)20', border: 'var(--orange)', icon: '🛡️' },
  low_reuse: { label: 'Low Reuse', color: 'var(--orange)', bg: 'var(--orange)20', border: 'var(--orange)', icon: '🎭' },
  archive_ok: { label: 'Archive OK', color: 'var(--green)', bg: 'var(--green)20', border: 'var(--green)', icon: '💾' },
  idle: { label: 'Idle', color: 'var(--text2)', bg: 'var(--surface2)', border: 'var(--surface3)', icon: '⏸️' },
};

/**
 * Fetch recent journal events for a model from the archive API.
 * @param {string} model - Model name
 * @param {number} limit - Number of events to fetch (default: 50)
 * @returns {Promise<Array>} Array of journal events
 */
async function fetchProxyJournalEvents(model, limit = 50) {
  try {
    const resp = await fetch(`/api/proxy/archive/journal?model=${encodeURIComponent(model)}&limit=${limit}`);
    const data = await resp.json();
    
    // Handle wrapped response format {success: true, data: {...}}
    const payload = (data && data.success && data.data) ? data.data : data;
    
    if (payload.error) {
      console.warn(`Journal fetch error for ${model}:`, payload.error);
      return [];
    }
    
    // Transform events: API returns event_type, but code expects event
    const events = (payload.events || []).map(e => ({ ...e, event: e.event_type }));
    return events;
  } catch (e) {
    console.warn(`Failed to fetch journal for ${model}:`, e);
    return [];
  }
}

/**
 * Compute proxy health badge state from journal events.
 * Priority order: error > guard > low_reuse > archive_ok > idle
 * @param {Array} events - Journal events (most recent first or last)
 * @returns {Object} Badge state: { type, label, color, bg, border, icon }
 */
function computeProxyHealthBadge(events) {
  if (!events || events.length === 0) {
    return PROXY_HEALTH_BADGES.idle;
  }

  // Analyze last 50 events (most recent)
  const recentEvents = events.slice(-50);

  // 1. Check for errors (highest priority)
  // Note: client_disconnected is a normal event, not an error
  const hasError = recentEvents.some(e => 
    e.event === 'error' || 
    e.event === 'proxy_error'
  );
  if (hasError) {
    return PROXY_HEALTH_BADGES.error;
  }

  // 2. Check for guard/window_guard events
  const hasGuard = recentEvents.some(e => 
    e.event === 'guard' || 
    e.event === 'window_guard'
  );
  if (hasGuard) {
    return PROXY_HEALTH_BADGES.guard;
  }

  // 3. Check for low mask reuse (mask_reuse gap)
  const requestEvents = recentEvents.filter(e => 
    e.event === 'request' && e.virtual_tokens && e.prompt_tokens
  );
  if (requestEvents.length > 0) {
    const gaps = requestEvents.map(e => {
      const virtual = e.virtual_tokens || 0;
      const active = e.prompt_tokens || 0;
      return virtual > 0 ? ((virtual - active) / virtual) * 100 : 0;
    });
    const avgGap = gaps.reduce((a, b) => a + b, 0) / gaps.length;
    if (avgGap > 50) { // More than 50% gap = low reuse
      return PROXY_HEALTH_BADGES.low_reuse;
    }
  }

  // 4. Check for successful kv_archive
  const hasArchiveSuccess = recentEvents.some(e => 
    (e.event === 'kv_archive' || e.event === 'archive_save') && e.success !== false
  );
  if (hasArchiveSuccess) {
    return PROXY_HEALTH_BADGES.archive_ok;
  }

  // 5. Default to idle
  return PROXY_HEALTH_BADGES.idle;
}

/**
 * Render proxy health badge HTML for a model.
 * @param {Object} modelData - Model data row (with mnemonic_proxy info)
 * @param {Array} journalEvents - Optional pre-fetched journal events
 * @returns {string} HTML for the badge
 */
function renderProxyHealthBadge(modelData, journalEvents = null) {
  const proxy = modelData.mnemonic_proxy || {};
  if (!proxy.enabled) {
    return ''; // No badge if proxy not enabled
  }

  const badge = journalEvents ? computeProxyHealthBadge(journalEvents) : PROXY_HEALTH_BADGES.idle;
  
  return `
    <span class="proxy-health-badge" 
          style="display: inline-flex; align-items: center; gap: 0.25rem; 
                 padding: 0.15rem 0.5rem; border-radius: 4px; 
                 font-size: 0.65rem; font-weight: 600; text-transform: uppercase;
                 background: ${badge.bg}; border: 1px solid ${badge.border}; color: ${badge.color};
                 white-space: nowrap;"
          title="Proxy health: ${badge.label}${journalEvents ? ` (from ${journalEvents.length} events)` : ' (no events)'}">
      ${badge.icon} ${badge.label}
    </span>
  `;
}

/**
 * Fetch and update proxy health badge for a specific model tile.
 * @param {string} modelName - Model name
 * @param {HTMLElement} badgeContainer - Container element to update
 */
async function updateProxyHealthBadge(modelName, badgeContainer) {
  if (!badgeContainer) return;
  
  const events = await fetchProxyJournalEvents(modelName, 50);
  const badge = computeProxyHealthBadge(events);
  
  badgeContainer.innerHTML = `
    <span class="proxy-health-badge" 
          style="display: inline-flex; align-items: center; gap: 0.25rem; 
                 padding: 0.15rem 0.5rem; border-radius: 4px; 
                 font-size: 0.65rem; font-weight: 600; text-transform: uppercase;
                 background: ${badge.bg}; border: 1px solid ${badge.border}; color: ${badge.color};
                 white-space: nowrap;"
          title="Proxy health: ${badge.label} (from ${events.length} events)">
      ${badge.icon} ${badge.label}
    </span>
  `;
}

// Store active badge update intervals for cleanup
const proxyBadgeIntervals = new Map();

/**
 * Start auto-refresh for proxy health badges on all visible tiles.
 * Runs every 5 seconds.
 */
function startProxyBadgeAutoRefresh() {
  // Clear any existing intervals
  stopProxyBadgeAutoRefresh();
  
  const interval = setInterval(async () => {
    const tiles = document.querySelectorAll('.tile[data-model]');
    for (const tile of tiles) {
      const modelName = tile.dataset.model;
      if (!modelName) continue;
      
      // Only fetch journal for models where proxy is actually RUNNING (chip has class "ok")
      const proxyChip = tile.querySelector('.proxy-status .chip');
      if (!proxyChip || !proxyChip.classList.contains('ok')) {
        continue; // Proxy is not running, skip journal fetch
      }
      
      const badgeContainer = tile.querySelector('.proxy-health-badge-container');
      if (badgeContainer) {
        await updateProxyHealthBadge(modelName, badgeContainer);
      }
    }
  }, 5000);
  
  proxyBadgeIntervals.set('global', interval);
}

/**
 * Stop all proxy badge auto-refresh intervals.
 */
function stopProxyBadgeAutoRefresh() {
  for (const [key, interval] of proxyBadgeIntervals) {
    clearInterval(interval);
  }
  proxyBadgeIntervals.clear();
}

// Expose globally for use in model-manager.js
window.startProxyBadgeAutoRefresh = startProxyBadgeAutoRefresh;
window.stopProxyBadgeAutoRefresh = stopProxyBadgeAutoRefresh;
window.fetchProxyJournalEvents = fetchProxyJournalEvents;
window.computeProxyHealthBadge = computeProxyHealthBadge;
window.renderProxyHealthBadge = renderProxyHealthBadge;
window.updateProxyHealthBadge = updateProxyHealthBadge;

/**
 * Fetch and update proxy health badges for all visible tiles with enabled proxy.
 * Called after renderTiles completes.
 */
async function updateAllProxyHealthBadges() {
  const tiles = document.querySelectorAll('.tile[data-model]');
  for (const tile of tiles) {
    const modelName = tile.dataset.model;
    if (!modelName) continue;
    
    const proxy = tile.querySelector('.proxy-status');
    if (!proxy) continue; // No proxy enabled for this model
    
    // Only fetch journal for models where proxy is actually RUNNING (chip has class "ok")
    const proxyChip = tile.querySelector('.proxy-status .chip');
    if (!proxyChip || !proxyChip.classList.contains('ok')) {
      continue; // Proxy is not running, skip journal fetch
    }
    
    const badgeContainer = tile.querySelector('.proxy-health-badge-container');
    if (badgeContainer) {
      await updateProxyHealthBadge(modelName, badgeContainer);
    }
  }
  
  // Start auto-refresh if not already running
  startProxyBadgeAutoRefresh();
}

window.updateAllProxyHealthBadges = updateAllProxyHealthBadges;

// Context and YaRN values are persisted in token counts. Keep dropdown option
// values in the same unit so a saved value is not interpreted as kilobytes when
// the properties panel is rendered again.
const CONTEXT_SIZE_OPTIONS = [
  { value: 4096, text: "4 KB (4096)" },
  { value: 8192, text: "8 KB (8192)" },
  { value: 16384, text: "16 KB (16384)" },
  { value: 32768, text: "32 KB (32768)" },
  { value: 65536, text: "64 KB (65536)" },
  { value: 131072, text: "128 KB (131072)" },
  { value: 262144, text: "256 KB (262144)" },
];

const YARN_ORIG_CTX_OPTIONS = [
  { value: 0, text: "Auto (model default)" },
  ...CONTEXT_SIZE_OPTIONS,
];

// Older versions of the dropdown sent KB values to the API even though the
// persisted model metadata uses token counts. Treat those small legacy values
// as KB when loading so they can be corrected on the next save.
const LEGACY_CONTEXT_KB_VALUES = new Set([4, 8, 16, 32, 64, 128, 256]);

/**
 * Convert a persisted context value to tokens.
 * @param {*} storedValue - Value loaded from model metadata
 * @param {number} fallbackValue - Value to use when the stored value is invalid
 * @returns {number} Context size in tokens
 */
function normalizeContextTokenValue(storedValue, fallbackValue) {
  const numericValue = Number(storedValue);
  if (!Number.isFinite(numericValue) || numericValue < 0) {
    return fallbackValue;
  }
  if (LEGACY_CONTEXT_KB_VALUES.has(numericValue)) {
    return numericValue * 1024;
  }
  return numericValue;
}

/**
 * Build context dropdown options while preserving the currently stored value.
 * @param {*} storedValue - Value loaded from model metadata
 * @returns {Array<{value: number, text: string}>} Dropdown options
 */
function getContextSizeOptions(storedValue) {
  const tokenValue = normalizeContextTokenValue(storedValue, 4096);
  const options = [...CONTEXT_SIZE_OPTIONS];
  if (!options.some((option) => option.value === tokenValue)) {
    options.push({ value: tokenValue, text: `${tokenValue} tokens (custom)` });
  }
  return options;
}

/**
 * Build YaRN original-context dropdown options while preserving the stored value.
 * @param {*} storedValue - Value loaded from model metadata
 * @returns {Array<{value: number, text: string}>} Dropdown options
 */
function getYarnOrigCtxOptions(storedValue) {
  const tokenValue = normalizeContextTokenValue(storedValue, 0);
  const options = [...YARN_ORIG_CTX_OPTIONS];
  if (!options.some((option) => option.value === tokenValue)) {
    options.push({ value: tokenValue, text: `${tokenValue} tokens (custom)` });
  }
  return options;
}

/**
 * Select the option matching a persisted context value.
 * @param {Array<{value: number, text: string}>} options - Dropdown options
 * @param {*} storedValue - Value loaded from model metadata
 * @param {number} fallbackValue - Option value to use when no match exists
 * @returns {number} Selected option value
 */
function getSelectedContextOptionValue(options, storedValue, fallbackValue) {
  const tokenValue = normalizeContextTokenValue(storedValue, fallbackValue);
  return options.some((option) => option.value === tokenValue)
    ? tokenValue
    : fallbackValue;
}

// Last rows/map handed to renderTiles, so a sort or visibility change can
// re-render without another round trip to the server.
let lastTileRows = [];
let lastTileDlMap = {};

/**
 * Sort model rows for tile display.
 * @param {Array} rows - Rows to sort in place
 * @param {string} order - One of TILE_SORT_VALUES
 */
function sortModelRows(rows, order) {
  const ratingOf = (r, field) => {
    const v = Number(r[field]);
    return Number.isFinite(v) ? v : 0;
  };
  const byName = (a, b) => {
    const modelCompare = normalizeModelName(a.model).localeCompare(
      normalizeModelName(b.model),
    );
    if (modelCompare !== 0) return modelCompare;
    const aFilename =
      a.filename || (a.model_path ? a.model_path.split("/").pop() : a.model);
    const bFilename =
      b.filename || (b.model_path ? b.model_path.split("/").pop() : b.model);
    return aFilename.localeCompare(bFilename);
  };

  const comparators = {
    name: byName,
    speed: (a, b) => ratingOf(b, "rating_speed") - ratingOf(a, "rating_speed"),
    coding: (a, b) => ratingOf(b, "rating_coding") - ratingOf(a, "rating_coding"),
    "speed-coding": (a, b) =>
      ratingOf(b, "rating_speed") - ratingOf(a, "rating_speed") ||
      ratingOf(b, "rating_coding") - ratingOf(a, "rating_coding"),
  };

  const cmp = comparators[order];
  if (!cmp) {
    console.warn(`Unknown sort order "${order}", falling back to alphabetical`);
    rows.sort(byName);
    return;
  }
  rows.sort((a, b) => cmp(a, b) || byName(a, b));
}

/**
 * Determine whether a model's Start/Stop actions should be enabled.
 *
 * Shared by the tile grid and the properties panel so the two views cannot
 * disagree. They previously duplicated this rule and drifted apart: the panel
 * omitted the missing-file check on Start and treated "starting" as
 * not-stoppable, while the tile did the opposite.
 *
 * Speculative decoding models are managed by their parent model, so they must
 * not be started or stopped independently.
 *
 * @param {Object} r - Model data row from /api/status
 * @returns {{canStart: boolean, canStop: boolean}}
 */
function modelActionState(r) {
  const running = r.status && r.status.startsWith("running");
  const starting = r.status === "starting";
  const spec = r.speculative_decoding_model;
  return {
    canStart: !(running || starting || !r.file_exists || spec),
    canStop: !((!running && !starting) || spec),
  };
}

/**
 * Render all model tiles
 * @param {Array} rows - Model data rows from API
 * @param {Object} dlMap - Download status map
 * @param {string} query - Search query for filtering
 */
function renderTiles(rows, dlMap, query) {
  const grid = document.getElementById("tile-grid");
  if (!grid) return;

  // Remember the inputs so tile preferences can be re-applied without refetching
  lastTileRows = rows;
  lastTileDlMap = dlMap;

  // Deduplicate: keep the first occurrence of each normalized model name
  // normalizeModelName is provided by model-manager.js (loaded before ui-controls.js)
  const seen = new Set();
  const deduplicated = [];
  for (const r of rows) {
    const normalized = normalizeModelName(r.model);
    if (!seen.has(normalized)) {
      seen.add(normalized);
      deduplicated.push(r);
    }
  }

  const filtered = deduplicated.filter((r) => {
    if (tilePrefs.hideSpecModels && r.speculative_decoding_model) return false;
    const q = query.toLowerCase();
    return r.model.toLowerCase().includes(q) || String(r.port).includes(q);
  });

  // Sort by the selected order (alphabetical by default)
  sortModelRows(filtered, tilePrefs.sortOrder);

  const html = filtered
    .map((r) => {
      const running = r.status && r.status.startsWith("running");
      const starting = r.status === "starting";
      const isSpec = !!r.speculative_decoding_model;
      const { canStart, canStop } = modelActionState(r);
      const statusChip = running
        ? '<span class="chip ok">● running</span>'
        : starting
          ? '<span class="chip warn">● starting</span>'
          : r.file_exists
            ? '<span class="chip ok">● stopped</span>'
            : '<span class="chip bad">[x] missing</span>';

      // Mnemonic-proxy status
      const proxy = r.mnemonic_proxy || {};
      const proxyEnabled = proxy.enabled === true;
      const proxyRunning = proxy.running === true;
      const proxyPort = proxy.proxy_port || proxy.port || 8096;
      const proxyMcpEnabled = proxy.mcp_enabled === true;
      const proxyMcpPort = proxy.mcp_port || proxy.mcp?.port || 8133;
      const proxyMcpRunning = proxy.mcp?.running === true;
      const proxyDataDir = proxy.data_dir || '';

      // Define proxy variables with defaults (used in section2 template)
      const proxyStatusClass = proxyRunning ? "ok" : "warn";
      const proxyStatusText = proxyRunning ? "● proxy running" : "● proxy stopped";
      const proxyMcpStatus = proxyMcpEnabled ? ` MCP:${proxyMcpPort}${proxyMcpRunning ? " running" : " stopped"}` : "";
      const canStartProxy = running && !proxyRunning;
      const canStopProxy = running && proxyRunning;
      
      // Initial health badge (idle) - will be updated asynchronously
      const initialBadge = PROXY_HEALTH_BADGES.idle;
      const badgeHtml = `
        <span class="proxy-health-badge" 
              style="display: inline-flex; align-items: center; gap: 0.25rem; 
                     padding: 0.15rem 0.5rem; border-radius: 4px; 
                     font-size: 0.65rem; font-weight: 600; text-transform: uppercase;
                     background: ${initialBadge.bg}; border: 1px solid ${initialBadge.border}; color: ${initialBadge.color};
                     white-space: nowrap;"
              title="Proxy health: ${initialBadge.label} (loading...)">
          ${initialBadge.icon} ${initialBadge.label}
        </span>
      `;

      const dl = dlMap[r.model] || {};
      const partial = r.partial_download || 0;

      // Check if there's an active download - use dl from API or stored progress state
      const apiProgress = dlMap[r.model];
      let dlProgress = apiProgress && apiProgress.status ? apiProgress : {};
      // Prefer client-side progress state for any active or recent download status
      if (
        downloadProgressState[r.model] &&
        ["downloading", "complete", "error"].includes(
          downloadProgressState[r.model].status,
        )
      ) {
        dlProgress = downloadProgressState[r.model];
      }

      // Use dlProgress (which includes client-side state) for button display
      let dlHtml = "";
      if (dlProgress.status === "downloading") {
        dlHtml =
          '<button class="download" disabled>⬇ ' +
          (dlProgress.progress || 0) +
          "%</button>";
      } else if (dlProgress.status === "complete") {
        // File is now downloaded, show Re-download button
        const force = r.file_exists ? "true" : "false";
        dlHtml =
          '<button class="download" onclick="downloadWithResume(\'' +
          jsEscape(r.model) +
          "', false, 0, " +
          force +
          ')">⬇ Re-download</button>';
      } else if (dlProgress.status === "error") {
        dlHtml =
          '<button class="download" onclick="retryDownload(\'' +
          jsEscape(r.model) +
          "', true)\">🔄 Retry</button>";
      } else if (partial > 0) {
        dlHtml =
          '<button class="download" onclick="resumeDownload(\'' +
          jsEscape(r.model) +
          "', " +
          partial +
          ')">[Start] Resume</button>';
      } else {
        const force = r.file_exists ? "true" : "false";
        dlHtml =
          '<button class="download" onclick="downloadWithResume(\'' +
          jsEscape(r.model) +
          "', false, 0, " +
          force +
          ')">⬇ ' +
          (r.file_exists ? "Re-download" : "Download") +
          "</button>";
      }

      const portCell = r.file_exists
        ? '<span class="port-cell">' +
          '<a class="actions btn-goto" style="padding:.3rem .7rem;border-radius:4px;font-size:.8rem;color:#fff;background:var(--blue);text-decoration:none" href="' +
          portUrl(r.port) +
          '" target="_blank">:' +
          r.port +
          "</a>" +
          '<button class="btn-edit-port" data-model="' +
          he(r.model) +
          '" onclick="event.stopPropagation(); editPortInline(this.dataset.model, ' +
          r.port +
          ')" title="Edit port">✏</button>' +
          "</span>"
        : '<span class="chip bad">-</span>';

      const showProgress = dlProgress.status === "downloading";

      const progressHtml = showProgress
        ? '<div id="progress-' +
          safeId(r.model) +
          '" class="download-progress" style="margin-top: .5rem; padding: .5rem; background: var(--surface2); border-radius: 4px;">' +
          '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: .25rem;">' +
          '<strong style="font-size: .85rem;">Download Progress</strong>' +
          '<span id="progress-text-' +
          safeId(r.model) +
          '" style="font-size: .75rem;">' +
          (dlProgress.progress || 0) +
          "%</span>" +
          "</div>" +
          '<div style="background: var(--surface); border-radius: 4px; height: 6px; overflow: hidden;">' +
          '<div id="progress-bar-' +
          safeId(r.model) +
          '" style="background: var(--btn-download); height: 100%; width: ' +
          (dlProgress.progress || 0) +
          '%; transition: width 0.2s;"></div>' +
          "</div>" +
          '<div id="progress-details-' +
          safeId(r.model) +
          '" style="font-size: .7rem; color: var(--text2); margin-top: .25rem;">' +
          (dlProgress.speed_mbps ? dlProgress.speed_mbps + " MB/s" : "") +
          "</div>" +
          "</div>"
        : "";

      // Build three-section tile layout:
      // Section 1: Model properties, status, action buttons
      // Section 2: Mnemonic Proxy / MCP properties, status, action buttons
      // Section 3: Ratings and Comments
      
      // Model action buttons (moved into section1) - wrapped in .actions for CSS styling
      const modelActionButtons = `
        <div class="actions tile-section-actions">
          ${dlHtml}
          <button class="run" onclick="act('${jsEscape(r.model)}', 'start')" ${canStart ? "" : "disabled"}>\u25B6 Start</button>
          <button class="stop" onclick="act('${jsEscape(r.model)}', 'stop')" ${canStop ? "" : "disabled"}>\u25A0 Stop</button>
          <button class="chat" onclick="window.open('http://127.0.0.1:${r.port}?model=' + encodeURIComponent('${jsEscape(r.model)}'), '_blank')" ${canStop ? "" : "disabled"} title="Chat with model">Chat</button>
          <button class="add" onclick="downloadModel('${jsEscape(r.model)}', true)">\u2795 Add Variant</button>
          <button class="del" onclick="promptDeleteModel('${jsEscape(r.model)}')">\u274C Delete</button>
          ${r.mnemonic_proxy && r.mnemonic_proxy.enabled ? 
            '<button class="agent-config" onclick="event.stopPropagation(); openAgentConfigModal(\'' + jsEscape(r.model) + '\')" title="Generate agent config for this model">🤖 Config</button>' : ''}
        </div>`;

      const section1 = `
        <div class="tile-section tile-section-model">
          <div class="tile-section-header">Model</div>
          <div class="tile-section-content">
            <b>${jsEscape(r.model)}</b>
            ${isSpec
              ? '<span class="chip spec" title="Speculative decoding model - used only as a draft model, not as a primary model">⚡ spec</span>'
              : ""}
            ${statusChip}
          </div>
          <div class="tile-section-content">
            <code title="${jsEscape(r.model_path)}">${jsEscape(r.model_path ? r.model_path.split("/").pop() : r.filename)}</code>
            <div>${fmtGb(r.file_size_gb)} · ${r.status || "-"}${r.pid ? " · pid " + r.pid : ""}</div>
            ${portCell}
          </div>
          ${modelActionButtons}
        </div>`;

      const section2 = proxyEnabled ? `
        <div class="tile-section tile-section-proxy">
          <div class="tile-section-header">Mnemonic Proxy</div>
          <div class="tile-section-content">
            <div class="proxy-status">
              <div class="proxy-status-row">
                <span class="chip ${proxyStatusClass}" title="Mnemonic-proxy on port ${proxyPort}${proxyMcpStatus}">${proxyStatusText} :${proxyPort}${proxyMcpStatus}</span>
                ${badgeHtml}
              </div>
              <div class="proxy-actions-row">
                <button class="actions-btn run" onclick="event.stopPropagation(); toggleProxy('${jsEscape(r.model)}', true)" title="Start mnemonic-proxy" ${canStartProxy ? "" : "disabled"}>▶ Start</button>
                <button class="actions-btn stop" onclick="event.stopPropagation(); toggleProxy('${jsEscape(r.model)}', false)" title="Stop mnemonic-proxy" ${canStopProxy ? "" : "disabled"}>■ Stop</button>
                <button class="actions-btn goto" onclick="event.stopPropagation(); showProxyMetrics('${jsEscape(r.model)}')" title="View proxy metrics">📊 Metrics</button>
                <button class="actions-btn goto" onclick="event.stopPropagation(); window.ArchiveBrowser?.open('${jsEscape(r.model)}')" title="Open Archive Browser">📚 Archive</button>
              </div>
            </div>
          </div>
        </div>` : '';

      const section3 = `
        <div class="tile-section tile-section-ratings">
          <div class="tile-section-header">Ratings</div>
          <div class="tile-section-content">
            <div class="rating-row speed">
              <span class="rating-label">Speed</span>
              <span class="rating-stars">
                <span class="star ${(r.rating_speed || 0) >= 1 ? 'filled' : ''}">★</span>
                <span class="star ${(r.rating_speed || 0) >= 2 ? 'filled' : ''}">★</span>
                <span class="star ${(r.rating_speed || 0) >= 3 ? 'filled' : ''}">★</span>
                <span class="star ${(r.rating_speed || 0) >= 4 ? 'filled' : ''}">★</span>
                <span class="star ${(r.rating_speed || 0) >= 5 ? 'filled' : ''}">★</span>
              </span>
            </div>
            <div class="rating-row coding">
              <span class="rating-label">Coding</span>
              <span class="rating-stars">
                <span class="star ${(r.rating_coding || 0) >= 1 ? 'filled' : ''}">★</span>
                <span class="star ${(r.rating_coding || 0) >= 2 ? 'filled' : ''}">★</span>
                <span class="star ${(r.rating_coding || 0) >= 3 ? 'filled' : ''}">★</span>
                <span class="star ${(r.rating_coding || 0) >= 4 ? 'filled' : ''}">★</span>
                <span class="star ${(r.rating_coding || 0) >= 5 ? 'filled' : ''}">★</span>
              </span>
            </div>
          </div>
          ${r.comment ? `<div class="tile-comment">${he(r.comment)}</div>` : ""}
        </div>`;

      return (
        '<div class="tile ' +
        (running ? "running" : "") +
        (isSpec ? " spec-model" : "") +
        '" data-model="' +
        jsEscape(r.model) +
        '" onclick="selectModel(\'' +
        jsEscape(r.model) +
        '\')"' +
        (running && r.port ? 
          ' onmouseenter="showMetricsTooltip(this, \'' + jsEscape(r.model) + '\', ' + r.port + ')"' +
          ' onmouseleave="hideMetricsTooltip(this)"' : '') +
        '>' +
        section1 +
        section2 +
        section3 +
        "</div>"
      );
    })
    .join("");

  grid.innerHTML = html;

  // Bind context menu to proxy status chips for Archive Browser access
  bindProxyStatusContextMenus();

  // Fetch and update proxy health badges for all tiles with enabled proxy
  updateAllProxyHealthBadges();
}

/**
 * Bind context menu to proxy status chips for Archive Browser access
 */
function bindProxyStatusContextMenus() {
  document.querySelectorAll('.proxy-status .chip').forEach(chip => {
    // Remove any existing context menu handler
    chip.oncontextmenu = null;
    
    chip.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      
      // Find the model name from the tile
      const tile = chip.closest('.tile');
      if (!tile) return;
      const modelName = tile.dataset.model;
      if (!modelName) return;
      
      // Remove any existing context menu
      const existingMenu = document.getElementById('proxy-status-context-menu');
      if (existingMenu) existingMenu.remove();
      
      // Create context menu
      const menu = document.createElement('div');
      menu.id = 'proxy-status-context-menu';
      menu.style.cssText = `
        position: fixed;
        top: ${e.clientY}px;
        left: ${e.clientX}px;
        background: var(--surface);
        border: 1px solid var(--surface2);
        border-radius: 6px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        z-index: 2000;
        min-width: 180px;
        padding: 0.25rem 0;
      `;
      
      const openArchiveItem = document.createElement('div');
      openArchiveItem.textContent = '📚 Open Archive Browser';
      openArchiveItem.style.cssText = `
        padding: 0.5rem 1rem;
        cursor: pointer;
        color: var(--text);
        font-size: 0.85rem;
      `;
      openArchiveItem.addEventListener('mouseenter', () => {
        openArchiveItem.style.background = 'var(--surface2)';
      });
      openArchiveItem.addEventListener('mouseleave', () => {
        openArchiveItem.style.background = 'transparent';
      });
      const closeMenu = (ev) => {
        if (!menu.contains(ev.target)) {
          menu.remove();
          document.removeEventListener('click', closeMenu);
        }
      };
      const closeMenuCleanup = () => {
        menu.remove();
        document.removeEventListener('click', closeMenu);
      };
      
      openArchiveItem.addEventListener('click', () => {
        closeMenuCleanup();
        if (window.ArchiveBrowser) {
          window.ArchiveBrowser.open(modelName);
        }
      });
      
      menu.appendChild(openArchiveItem);
      document.body.appendChild(menu);
      
      // Close menu on click outside (reuse closeMenu from above)
      setTimeout(() => document.addEventListener('click', closeMenu), 0);
    });
  });
}

/**
 * Persist the current tile preferences to localStorage
 */
function saveTilePrefs() {
  try {
    localStorage.setItem(TILE_PREFS_KEY, JSON.stringify(tilePrefs));
  } catch (e) {
    console.warn("Failed to save tile preferences:", e);
  }
}

/**
 * Re-render the tile grid using the cached rows.
 * Used when the sort order or the speculative-model visibility changes so
 * the change is reflected without another server round trip.
 */
function rerenderTiles() {
  const searchInput = document.getElementById("search");
  const query = searchInput ? searchInput.value.toLowerCase() : "";
  renderTiles(lastTileRows, lastTileDlMap, query);
}

/**
 * Apply a new tile preference and re-render.
 * @param {Object} changes - Partial preferences to merge in
 */
function updateTilePrefs(changes) {
  tilePrefs = { ...tilePrefs, ...changes };
  saveTilePrefs();
  rerenderTiles();
}

/**
 * Render the properties panel for a model
 * @param {Object} data - Model data row
 * @param {string} modelName - Model name
 */
/**
 * Initialize browse button event handlers
 * @param {HTMLElement} container - Container element to search for browse buttons
 */
function initBrowseButtons(container) {
  container.querySelectorAll(".browse-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const field = btn.dataset.field;
      const browseType = btn.dataset.browseType;
      const input = container.querySelector(`.meta-input[data-field="${field}"]`);
      
      if (!input) return;
      
      try {
        let result;
        
        // Try Electron API first (for Electron apps) - returns full paths
        if (window.electronAPI && typeof window.electronAPI.selectDirectory === 'function') {
          if (browseType === "directory") {
            result = await window.electronAPI.selectDirectory();
          } else if (browseType === "file") {
            result = await window.electronAPI.selectFile({ filters: [{ name: "All Files", extensions: ["*"] }] });
          } else if (browseType === "model-file") {
            result = await window.electronAPI.selectFile({ filters: [{ name: "GGUF Files", extensions: ["gguf"] }] });
          }
          
          if (result && !result.canceled && result.filePaths && result.filePaths.length > 0) {
            input.value = result.filePaths[0];
          }
        } 
        // Use custom File Browser module for File System Access API
        // This maintains an in-memory path stack to return full paths
        else if ('showDirectoryPicker' in window) {
          if (window.FileBrowser && typeof window.FileBrowser.openWithStartPath === 'function') {
            // Use current input value as start path hint (may contain full path like C:/folder)
            const startPath = input.value || undefined;
            window.FileBrowser.openWithStartPath({
              type: browseType,
              startPath: startPath,
              onSelect: (fullPath, entry) => {
                input.value = fullPath;
                // Trigger change event so any listeners know the value changed
                input.dispatchEvent(new Event('change', { bubbles: true }));
              }
            });
          } else {
            // Fallback if FileBrowser module not loaded
            console.warn('FileBrowser module not available, falling back to native picker');
            if (browseType === "directory" && 'showDirectoryPicker' in window) {
              const dirHandle = await window.showDirectoryPicker();
              result = { canceled: false, filePaths: [dirHandle.name] };
            } else if ((browseType === "file" || browseType === "model-file") && 'showOpenFilePicker' in window) {
              const options = browseType === "model-file" 
                ? { types: [{ description: "GGUF Files", accept: { "model/gguf": [".gguf"] } }] }
                : { types: [{ description: "All Files", accept: { "*/*": ["*"] } }] };
              const fileHandles = await window.showOpenFilePicker(options);
              if (fileHandles.length > 0) {
                const file = await fileHandles[0].getFile();
                result = { canceled: false, filePaths: [file.name] };
              }
            }
            
            if (result && !result.canceled && result.filePaths && result.filePaths.length > 0) {
              input.value = result.filePaths[0];
            }
          }
        }
        // Last resort: prompt for manual path entry (user must enter full path)
        else {
          const message = browseType === "directory" 
            ? "Enter full directory path:" 
            : browseType === "model-file" 
              ? "Enter full path to GGUF model file:" 
              : "Enter full file path:";
          const path = prompt(message, input.value);
          if (path) {
            result = { canceled: false, filePaths: [path] };
            input.value = result.filePaths[0];
          }
        }
      } catch (err) {
        console.error("Browse error:", err);
        logHtml('<b>Browse error:</b> ' + err, 'red');
      }
    });
  });
}

window.initBrowseButtons = initBrowseButtons;

async function renderPanelProperties(data, modelName) {
  const content = document.getElementById("panel-content");
  if (!content) return;

  const running = data.status && data.status.startsWith("running");
  const { canStart, canStop } = modelActionState(data);
  const modelSafeId = safeId(modelName);

  // Store original values for cancel functionality
  window.originalModelMeta = { ...data };

  let html = "";

         // Action buttons (Start/Stop) at the top
         html += `<div class="panel-section collapsed">
           <div class="panel-item full-width" style="display: flex; justify-content: flex-end; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap;">
              <button type="button" class="btn-primary" id="start-model-${modelSafeId}" style="padding: 0.5rem 1rem;" ${canStart ? "" : "disabled"}>
                ▶ Start
              </button>
              <button type="button" class="btn-danger" id="stop-model-${modelSafeId}" style="padding: 0.5rem 1rem;" ${canStop ? "" : "disabled"}>
                ■ Stop
              </button>
           </div>
         </div>`;

// Basic Configuration
  html += `<div class="panel-section collapsed">
    <h3>Basic Configuration</h3>
    <div class="panel-grid">`;
    
    const addItem = (label, value, isCode = false, isHtml = false, browseType = null) => {
      const escapedLabel = he(label);
      let escapedValue;
      if (isHtml) {
        escapedValue = value;
      } else if (isCode) {
        escapedValue = `<code>${he(value)}</code>`;
      } else {
        escapedValue = he(value);
      }
      
      let browseBtn = "";
      if (browseType) {
        browseBtn = `<button type="button" class="browse-btn" data-field="${he(label.toLowerCase().replace(/\s+/g, '_'))}" data-browse-type="${browseType}">📁</button>`;
      }
      
      html += `<div class="panel-item">
        <span class="label">${escapedLabel}</span>
        <span class="value" ${browseType ? 'style="display: flex; gap: 0.25rem; align-items: center;"' : ''}>
          ${escapedValue}
          ${browseBtn}
        </span>
      </div>`;
    };

    const addEditable = (
      label,
      field,
      value,
      inputType = "number",
      step = 1,
      min,
      max,
      hint = "",
    ) => {
      let input;
      const escapedLabel = he(label);
      const escapedValue = he(String(value));
      const hintAttr = hint ? ` title="${he(hint)}"` : "";
      if (inputType === "checkbox") {
        input = `<input type="checkbox" class="meta-input" data-field="${he(field)}" ${value ? "checked" : ""}>`;
      } else if (inputType === "text") {
        input = `<input type="text" class="meta-input" data-field="${he(field)}" value="${escapedValue}">`;
      } else {
        const minAttr = min !== undefined ? `min="${he(String(min))}"` : "";
        const maxAttr = max !== undefined ? `max="${he(String(max))}"` : "";
        input = `<input type="number" class="meta-input" data-field="${he(field)}" value="${escapedValue}" step="${step}" ${minAttr} ${maxAttr}>`;
      }
      html += `<div class="panel-item">
        <span class="label"${hintAttr}>${escapedLabel}</span>
        <span class="value">${input}</span>
      </div>`;
    };

    /**
     * Add an editable field with a browse button for file/directory selection
     * @param {string} label - Field label
     * @param {string} field - Field name
     * @param {string} value - Current value
     * @param {string} browseType - Type of browse: 'file', 'directory', 'model-file'
     * @param {string} hint - Optional tooltip text shown on hover
     */
    const addEditableWithBrowse = (
      label,
      field,
      value,
      browseType = "file",
      hint = ""
    ) => {
      const escapedLabel = he(label);
      const escapedValue = he(String(value));
      const hintAttr = hint ? ` title="${he(hint)}"` : "";
      let browseBtn;
      
      if (browseType === "file") {
        browseBtn = `<button type="button" class="browse-btn" data-field="${he(field)}" data-browse-type="file">📁</button>`;
      } else if (browseType === "directory") {
        browseBtn = `<button type="button" class="browse-btn" data-field="${he(field)}" data-browse-type="directory">📁</button>`;
      } else if (browseType === "model-file") {
        browseBtn = `<button type="button" class="browse-btn" data-field="${he(field)}" data-browse-type="model-file">📁</button>`;
      }
      
      html += `<div class="panel-item">
        <span class="label"${hintAttr}>${he(label)}</span>
        <span class="value" style="display: flex; gap: 0.25rem; align-items: center;">
          <input type="text" class="meta-input" data-field="${he(field)}" value="${escapedValue}" style="flex: 1;">
          ${browseBtn}
        </span>
      </div>`;
    };

    addItem("Model Name", modelName);
    // Reset to Default Parameters button (placed after Model Name)
    html += `<div class="panel-item">
      <span class="label"></span>
      <span class="value" style="display:flex; justify-content:flex-start;">
        <button type="button" id="resetPropsToDefaultsBtn" class="btn-goto" style="padding: 0.35rem 0.9rem; font-size: 0.8rem;">Set to Default Parameters</button>
      </span>
    </div>`;
    
    // Bind Host dropdown
    const bindHostVal = data.bind_host || "0.0.0.0";
    let bindHostSelect = `<select class="meta-input" data-field="bind_host">`;
    [["0.0.0.0", "0.0.0.0 (All interfaces - LAN accessible)"], ["127.0.0.1", "127.0.0.1 (Localhost only)"]].forEach(([val, label]) => {
      bindHostSelect += `<option value="${val}" ${bindHostVal === val ? "selected" : ""}>${label}</option>`;
    });
    bindHostSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label" title="Network interface to bind llama-server to">Bind Host</span>
      <span class="value">${bindHostSelect}</span>
    </div>`;
    
    addEditable("Port", "port", data.port, "number", 1, 0);
    // Reuse Port checkbox
    const reusePortVal = data.reuse_port !== undefined ? data.reuse_port : true;
    html += `<div class="panel-item">
      <span class="label">Reuse Port</span>
      <span class="value">
        <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer;">
          <input type="checkbox" class="meta-input" data-field="reuse_port" ${reusePortVal ? "checked" : ""}>
          <span>Allow multiple sockets to bind to the same port (SO_REUSEPORT)</span>
        </label>
      </span>
    </div>`;
    
    // Note: Hugging Face Repo, Filename, Expected Size are typically static/model info
    // These would come from data but aren't in the standard model metadata API
    // For now, we'll show them as static info if available
    if (data.repo) {
      addItem("Hugging Face Repo", data.repo);
    }
    if (data.filename) {
      addItem("Filename", data.filename, false, false, "model-file");
    }
    if (data.file_size_gb) {
      addItem("Expected Size (GB)", fmtGb(data.file_size_gb));
    }
    
    // Log Verbosity
    addEditable("Log Verbosity", "log_verbosity", data.log_verbosity !== undefined ? data.log_verbosity : 3, "number", 1, 0);

    // llama.cpp Platform dropdown
    const platformVal = data.llama_cpp_platform || "auto";
    let platformSelect = `<select class="meta-input" data-field="llama_cpp_platform">`;
    ["auto", "windows", "linux"].forEach((val) => {
      const displayVal = val === "auto" ? "Auto-detect" : val.charAt(0).toUpperCase() + val.slice(1);
      platformSelect += `<option value="${val}" ${platformVal === val ? "selected" : ""}>${displayVal}</option>`;
    });
    platformSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label" title="Select llama.cpp platform (windows/linux) for this model">llama.cpp Platform</span>
      <span class="value">${platformSelect}</span>
    </div>`;

    // llama.cpp Backend dropdown
    const backendVal = data.llama_cpp_backend || "auto";
    let backendSelect = `<select class="meta-input" data-field="llama_cpp_backend">`;
    ["auto", "vulkan", "sycl", "openvino"].forEach((val) => {
      const displayVal = val === "auto" ? "Auto-detect" : val.toUpperCase();
      backendSelect += `<option value="${val}" ${backendVal === val ? "selected" : ""}>${displayVal}</option>`;
    });
    backendSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label" title="Select llama.cpp backend (vulkan/sycl/openvino) for this model">Backend</span>
      <span class="value">${backendSelect}</span>
    </div>`;

    // Multimodal projector file
    addEditableWithBrowse("Multimodal Projector File", "mmproj", data.mmproj || "", "file");
    addEditable("Use Multimodal Projector Auto", "mmproj_auto", data.mmproj_auto !== false, "checkbox");
    addEditable("Multimodal Projector Offload", "mmproj_offload", data.mmproj_offload !== false, "checkbox");

    html += `</div></div>`;

  // Performance & Threading
  html += `<div class="panel-section collapsed">
    <h3>Performance & Threading</h3>
    <div class="panel-grid">`;
    
    addEditable("Threads", "threads", data.threads, "number", 1, 0);
    addEditable("Threads Batch", "threads_batch", data.threads_batch, "number", 1, 0);
    addEditable("Timeout", "timeout", data.timeout, "number", 1, 0);
    
    // Parallel dropdown with "auto" option
    const parallelVal = data.parallel == null ? "1" : String(data.parallel);
    let parallelSelect = `<select class="meta-input" data-field="parallel">`;
    [1, 2, 3, 4, 5, 6, 7, 8, "auto"].forEach((val) => {
      const valStr = String(val);
      const displayVal = val === "auto" ? "auto (set --kv-unified=true)" : val;
      parallelSelect += `<option value="${valStr}" ${parallelVal === valStr ? "selected" : ""}>${displayVal}</option>`;
    });
    parallelSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">Parallel</span>
      <span class="value">${parallelSelect}</span>
    </div>`;
    
    addEditable("UBatch Size", "ubatch_size", data.ubatch_size, "number", 1, 1);
    addEditable("Batch Size", "batch_size", data.batch_size, "number", 1, 1);
    
    html += `</div></div>`;

  // Memory Management
  html += `<div class="panel-section collapsed">
    <h3>Memory Management</h3>
    <div class="panel-grid">`;
    
    addEditable("Use MLock", "mlock", data.mlock, "checkbox");
    addEditable("No MMap", "no_mmap", data.no_mmap, "checkbox");
    addEditable("NUMA Aware", "numa", data.numa, "checkbox");
    
    // NUMA Mode dropdown
    const numaVal = data.numa_value == null ? "distribute" : data.numa_value;
    let numaSelect = `<select class="meta-input" data-field="numa_value">`;
    ["distribute", "isolate", "numactl"].forEach((val) => {
      numaSelect += `<option value="${val}" ${numaVal === val ? "selected" : ""}>${val}</option>`;
    });
    numaSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">NUMA Mode</span>
      <span class="value">${numaSelect}</span>
    </div>`;
    
    addEditable("CPU Affinity", "cpu_affinity", data.cpu_affinity, "text");
    addEditable("CPU Strict Mode", "cpu_strict", data.cpu_strict, "checkbox");
    
    html += `</div></div>`;

  // GPU & Acceleration
  html += `<div class="panel-section collapsed">
    <h3>GPU & Acceleration</h3>
    <div class="panel-grid">`;
    
    addEditable("GPU Layers (0=CPU only)", "gpu_layers", data.gpu_layers, "number", 1, 0);
    
    // CPU MoE - checkbox that disables n-cpu-moe when checked
    const cpuMoe = data.cpu_moe ?? false;
    const nCpuMoe = data.n_cpu_moe ?? 0;
    const moeCacheMib = data.moe_cache_mib ?? 0;
    html += `<div class="panel-item">
      <span class="label">CPU MoE (all layers)</span>
      <span class="value">
        <input type="checkbox" class="meta-input" data-field="cpu_moe" ${cpuMoe ? "checked" : ""}>
      </span>
    </div>`;
    html += `<div class="panel-item" id="n-cpu-moe-row" style="${cpuMoe ? 'display:none;' : ''}">
      <span class="label">N CPU MoE (layers)</span>
      <span class="value">
        <input type="number" class="meta-input" data-field="n_cpu_moe" value="${he(String(nCpuMoe))}" step="1" min="0">
      </span>
    </div>`;
    html += `<div class="panel-item" id="moe-cache-mib-row" style="${(!cpuMoe && nCpuMoe === 0) ? 'display:none;' : ''}">
      <span class="label">MoE Cache (MiB)</span>
      <span class="value">
        <input type="number" class="meta-input" data-field="moe_cache_mib" value="${he(String(moeCacheMib))}" step="1" min="0" placeholder="0 = disabled">
      </span>
    </div>`;
    
    // Flash Attn dropdown
    const flashVal = data.flash_attn == null ? "off" : data.flash_attn;
    const isFlashSelected = (val) =>
      flashVal === val;
    let flashSelect = `<select class="meta-input" data-field="flash_attn">`;
    ["off", "auto", "on"].forEach((val) => {
      flashSelect += `<option value="${val}" ${isFlashSelected(val) ? "selected" : ""}>${val}</option>`;
    });
    flashSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">Flash Attn</span>
      <span class="value">${flashSelect}</span>
    </div>`;
    
addEditableWithBrowse("Slot Save Path", "slot_save_path", data.slot_save_path, "directory",
      "Required: directory where KV cache slots are saved/loaded for cache reload");
    addEditable("Cache Reuse (tokens)", "cache_reuse", data.cache_reuse, "number", 1, 1, undefined,
      "Required: number of tokens to reuse from cached slot (must be > 0 for cache reload)");
    addEditable("Keep (tokens from initial prompt)", "keep", data.keep, "number", 1, 0, undefined,
      "Required: tokens to keep from prompt start (must be > 0 for cache reload)");
    addEditable("SWA Full (for SWA models)", "swa_full", data.swa_full, "checkbox", undefined, undefined, undefined,
      "Must be OFF (false) for cache reload - SWA breaks cache persistence (see llama.cpp PR #13194)");
    
    // Chat template file with browse button
    addEditableWithBrowse("Chat Template File", "chat_template_file", data.chat_template_file, "file");
    
    html += `</div></div>`;

  // Context & RoPE/YaRN
  html += `<div class="panel-section collapsed">
    <h3>Context & RoPE / YaRN</h3>
    <div class="panel-grid">`;
    
    // Context Size dropdown. Option values are token counts to match the
    // persisted models.json values and prevent reloads from resetting to 4K.
    const ctxSizeOptions = getContextSizeOptions(data.ctx_size);
    const selectedCtxSize = getSelectedContextOptionValue(
      ctxSizeOptions,
      data.ctx_size,
      4096,
    );
    
    html += `<div class="panel-item">
      <span class="label">Context Size</span>
      <span class="value">
        <select class="meta-input ctx_size-select" data-field="ctx_size">
          ${ctxSizeOptions.map(option => 
            `<option value="${option.value}" ${option.value === selectedCtxSize ? 'selected' : ''}>${option.text}</option>`
          ).join('')}
        </select>
      </span>
    </div>`;
    addEditable("RoPE Freq Base", "rope_freq_base", data.rope_freq_base, "number", 0.1, 1);
    addEditable("RoPE Freq Scale", "rope_freq_scale", data.rope_freq_scale, "number", 0.1, 0.1);
    
    // RoPE Scaling Type dropdown
    const ropeScalingVal = data.rope_scaling_type == null ? "none" : data.rope_scaling_type;
    let ropeScalingSelect = `<select class="meta-input" data-field="rope_scaling_type">`;
    ["none", "linear", "yarn"].forEach((val) => {
      ropeScalingSelect += `<option value="${val}" ${ropeScalingVal === val ? "selected" : ""}>${val}</option>`;
    });
    ropeScalingSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">RoPE Scaling Type</span>
      <span class="value">${ropeScalingSelect}</span>
    </div>`;
    
    addEditable("RoPE Scale (context factor)", "rope_scale", data.rope_scale, "number", 0.1, 0.1);
    // YaRN Orig Context dropdown. Zero is a valid value that lets llama.cpp
    // use the model's original context size, so it is shown as Auto.
    const yarnOrigCtxOptions = getYarnOrigCtxOptions(data.yarn_orig_ctx);
    const selectedYarnOrigCtx = getSelectedContextOptionValue(
      yarnOrigCtxOptions,
      data.yarn_orig_ctx,
      0,
    );
    
    html += `<div class="panel-item">
      <span class="label">YaRN Orig Context</span>
      <span class="value">
        <select class="meta-input yarn_orig_ctx-select" data-field="yarn_orig_ctx">
          ${yarnOrigCtxOptions.map(option => 
            `<option value="${option.value}" ${option.value === selectedYarnOrigCtx ? 'selected' : ''}>${option.text}</option>`
          ).join('')}
        </select>
      </span>
    </div>`;
    addEditable("YaRN Ext Factor", "yarn_ext_factor", data.yarn_ext_factor, "number", 0.1);
    addEditable("YaRN Attn Factor", "yarn_attn_factor", data.yarn_attn_factor, "number", 0.1, 0.1);
    addEditable("YaRN Beta Slow", "yarn_beta_slow", data.yarn_beta_slow, "number", 0.1, 0.1);
    addEditable("YaRN Beta Fast", "yarn_beta_fast", data.yarn_beta_fast, "number", 0.1, 0.1);
    
    html += `</div></div>`;

  // KV Cache
  html += `<div class="panel-section collapsed">
    <h3>KV Cache</h3>
    <div class="panel-grid">`;
    
    // KV Cache Type K dropdown
    const kvCacheKVal = data.cache_type_k == null ? "f16" : data.cache_type_k;
    let kvCacheKSelect = `<select class="meta-input" data-field="cache_type_k">`;
    ["f16", "fp8", "q4_0", "q4_1", "q5_0", "q5_1", "q8_0", "dq1_5", "dq2_5", "iq2_XXS", "iq2_xs", "iq3_xl", "xq4_0", "xq8_0", "xxq3_2", "xxq4_0", "yyxq3_2", "xxq4_0_sfp"].forEach((val) => {
      kvCacheKSelect += `<option value="${val}" ${kvCacheKVal === val ? "selected" : ""}>${val}</option>`;
    });
    kvCacheKSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">KV Cache Type K</span>
      <span class="value">${kvCacheKSelect}</span>
    </div>`;
    
    // KV Cache Type V dropdown
    const kvCacheVVal = data.cache_type_v == null ? "f16" : data.cache_type_v;
    let kvCacheVSelect = `<select class="meta-input" data-field="cache_type_v">`;
    ["f16", "fp8", "q4_0", "q4_1", "q5_0", "q5_1", "q8_0", "dq1_5", "dq2_5", "iq2_XXS", "iq2_xs", "iq3_xl", "xq4_0", "xq8_0", "xxq3_2", "xxq4_0", "yyxq3_2", "xxq4_0_sfp"].forEach((val) => {
      kvCacheVSelect += `<option value="${val}" ${kvCacheVVal === val ? "selected" : ""}>${val}</option>`;
    });
    kvCacheVSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">KV Cache Type V</span>
      <span class="value">${kvCacheVSelect}</span>
    </div>`;
    
    html += `</div></div>`;

  // Speculative Decoding
  html += `<div class="panel-section collapsed">
    <h3>Speculative Decoding</h3>
    <div class="panel-grid">`;
    
    // Spec Type multi-select (comma-separated list)
    const specTypeVal = data.spec_type == null ? "none" : data.spec_type;
    const specTypeOptions = ["none", "draft-simple", "draft-eagle3", "draft-mtp", "draft-dflash", "draft-dspark", "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod", "ngram-cache"];
    const selectedValues = specTypeVal === "none" ? ["none"] : specTypeVal.split(",").map(v => v.trim());
    let specTypeSelect = `<select class="meta-input" data-field="spec_type" multiple style="min-height: 120px;">`;
    specTypeOptions.forEach((val) => {
      specTypeSelect += `<option value="${val}" ${selectedValues.includes(val) ? "selected" : ""}>${val}</option>`;
    });
    specTypeSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">Spec Type</span>
      <span class="value">${specTypeSelect}</span>
      <small style="color: var(--muted); display: block; margin-top: 0.25rem;">Hold Ctrl/Cmd to select multiple (comma-separated)</small>
    </div>`;
    
    // Draft Model Path select: options are the speculative-decoding candidates plus
    // the model's current value. The current value stays selectable even when it is
    // not a candidate, so saving unrelated fields cannot clear the draft model path.
    // Option values use model_path_for_binary (Windows form, e.g. C:/llama.cpp-models/...)
    // because llama-server.exe runs in a Windows context, not WSL.
    const currentDraftPath = String(data.spec_draft_model || "");
    const specDraftChoices = [{ path: "", label: "None" }];
    getSpecDraftModels().forEach(r => {
      const path = String(r.model_path_for_binary || r.model_path || "");
      if (!path || specDraftChoices.some(c => c.path === path)) return;
      specDraftChoices.push({ path, label: r.filename || path || r.model || "Unknown" });
    });
    if (currentDraftPath && !specDraftChoices.some(c => c.path === currentDraftPath)) {
      specDraftChoices.push({ path: currentDraftPath, label: currentDraftPath });
    }
    const specDraftOptions = specDraftChoices.map(c =>
      `<option value="${he(c.path)}" ${c.path === currentDraftPath ? "selected" : ""}>${he(String(c.label))}</option>`
    ).join("");
    html += `<div class="panel-item">
      <span class="label">Draft Model Path</span>
      <span class="value">
        <select class="meta-input" data-field="spec_draft_model">
          ${specDraftOptions}
        </select>
      </span>
    </div>`;
    addEditable("Draft HF Repo", "spec_draft_hf_repo", data.spec_draft_hf_repo, "text");
    addEditable("Draft N Max", "spec_draft_n_max", data.spec_draft_n_max, "number", 1, 0);
    addEditable("Draft N Min", "spec_draft_n_min", data.spec_draft_n_min, "number", 1, 0);
    addEditable("Draft P Split", "spec_draft_p_split", data.spec_draft_p_split, "number", 0.01, 0, 1);
    addEditable("Draft P Min", "spec_draft_p_min", data.spec_draft_p_min, "number", 0.01, 0, 1);
    addEditable("Draft NGL", "spec_draft_ngl", data.spec_draft_ngl, "text");
    
    // Draft model sampling parameters
    addEditable("Draft Temp", "spec_draft_temp", data.spec_draft_temp, "number", 0.1, 0, 2);
    addEditable("Draft Top P", "spec_draft_top_p", data.spec_draft_top_p, "number", 0.01, 0, 1);
    addEditable("Draft Min P", "spec_draft_min_p", data.spec_draft_min_p, "number", 0.01, 0, 1);
    addEditable("Draft Typical", "spec_draft_typical", data.spec_draft_typical, "number", 0.01, 0, 1);
    
    // Ngram Simple settings
    addEditable("Ngram Size N (lookup)", "spec_ngram_size_n", data.spec_ngram_size_n, "number", 1, 0);
    addEditable("Ngram Size M (draft)", "spec_ngram_size_m", data.spec_ngram_size_m, "number", 1, 0);
    addEditable("Ngram Min Hits", "spec_ngram_min_hits", data.spec_ngram_min_hits, "number", 1, 1);
    
    // Ngram-mod settings
    addEditable("Ngram-mod N Match", "spec_ngram_mod_n_match", data.spec_ngram_mod_n_match, "number", 1, 0);
    addEditable("Ngram-mod N Min", "spec_ngram_mod_n_min", data.spec_ngram_mod_n_min, "number", 1, 0);
    addEditable("Ngram-mod N Max", "spec_ngram_mod_n_max", data.spec_ngram_mod_n_max, "number", 1, 0);
    
    // Draft model KV cache types
    const draftKCacheVal = data.spec_draft_type_k == null ? "f16" : data.spec_draft_type_k;
    let draftKCacheSelect = `<select class="meta-input" data-field="spec_draft_type_k">`;
    ["f16", "q4_0", "q8_0"].forEach((val) => {
      draftKCacheSelect += `<option value="${val}" ${draftKCacheVal === val ? "selected" : ""}>${val}</option>`;
    });
    draftKCacheSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">Draft KV Cache Type K</span>
      <span class="value">${draftKCacheSelect}</span>
    </div>`;
    
    const draftVCacheVal = data.spec_draft_type_v == null ? "f16" : data.spec_draft_type_v;
    let draftVCacheSelect = `<select class="meta-input" data-field="spec_draft_type_v">`;
    ["f16", "q4_0", "q8_0"].forEach((val) => {
      draftVCacheSelect += `<option value="${val}" ${draftVCacheVal === val ? "selected" : ""}>${val}</option>`;
    });
    draftVCacheSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">Draft KV Cache Type V</span>
      <span class="value">${draftVCacheSelect}</span>
    </div>`;
    
    // Warmup settings
    addEditable("Enable Warmup", "warmup_enabled", data.warmup_enabled, "checkbox");
    
    html += `</div></div>`;

  // Mnemonic Proxy Configuration
  const mp = data.mnemonic_proxy || {};
  // Auto-populate defaults based on model name
  const defaultDataDir = mp.data_dir || `./data/${modelName}`;
  const defaultSlotDir = mp.slot_dir || data.slot_save_path || "";
  html += `<div class="panel-section collapsed">
    <h3>Mnemonic Proxy (Context Management)</h3>
    <div class="panel-grid">`;
    
    addEditable("Enable Proxy", "mnemonic_proxy.enabled", mp.enabled !== undefined ? mp.enabled : false, "checkbox");
    addEditable("Proxy Port", "mnemonic_proxy.port", mp.port !== undefined ? mp.port : 8096, "number", 1, 1);
    addEditableWithBrowse("Data Directory", "mnemonic_proxy.data_dir", defaultDataDir, "directory",
      "Proxy data directory (journal, metrics, etc.)");
    addEditableWithBrowse("Slot Directory", "mnemonic_proxy.slot_dir", defaultSlotDir, "directory",
      "Proxy slot directory for KV cache persistence (falls back to model's slot_save_path)");
    addEditableWithBrowse("Tokenizer Path", "mnemonic_proxy.tokenizer_path", mp.tokenizer_path || "", "file",
      "Optional: tokenizer file for accurate token counting");
    
    // Config Override (JSON)
    const configOverride = mp.config_override || {};
    html += `<div class="panel-item">
      <span class="label">Config Override (JSON)
        <button type="button" class="btn btn-small refresh-config-override" title="Refresh from default config file" style="margin-left: 8px; padding: 2px 8px; font-size: 0.75rem;">Refresh</button>
      </span>
      <span class="value">
        <textarea class="meta-input" data-field="mnemonic_proxy.config_override" rows="4" style="width: 100%; font-family: monospace; font-size: 0.85rem;">${he(JSON.stringify(configOverride, null, 2))}</textarea>
      </span>
    </div>`;
    
    addEditable("Enable MCP Server", "mnemonic_proxy.mcp_enabled", mp.mcp_enabled !== undefined ? mp.mcp_enabled : false, "checkbox");
    addEditable("MCP Port", "mnemonic_proxy.mcp_port", mp.mcp_port !== undefined ? mp.mcp_port : 8133, "number", 1, 1);
    
    // Open in Archive button (only show if proxy is enabled)
    const proxyEnabled = mp.enabled === true;
    html += `<div class="panel-item" id="archive-browser-btn-container" style="${proxyEnabled ? '' : 'display: none;'}">
      <span class="label"></span>
      <span class="value">
        <button type="button" class="btn-goto" id="open-archive-browser-${modelSafeId}" style="padding: 0.5rem 1rem;" title="Open Archive Browser for this model">
          📚 Open in Archive Browser
        </button>
      </span>
    </div>`;
    
    html += `</div></div>`;

  // Agent/Tool Parameters
  html += `<div class="panel-section collapsed">
    <h3>Agent/Tool Parameters</h3>
    <div class="panel-grid">`;
    
    addEditable("Jinja Tool Parsing", "jinja", data.jinja, "checkbox");
    addEditable("Reasoning Preserve", "reasoning_preserve", data.reasoning_preserve, "checkbox");
    addEditable("Auto-download", "auto_download", data.auto_download, "checkbox");
    addEditable("Speculative Decoding Model", "speculative_decoding_model", data.speculative_decoding_model, "checkbox");
    
    // Custom HTTP Header for Kilo Code Config
    addEditable("Custom Header Key", "custom_header_key", data.custom_header_key || "", "text");
    addEditable("Custom Header Value", "custom_header_value", data.custom_header_value || "", "text");
    
    // Use Generation Parameters checkbox
    const useGenParams = data.use_generation_params ?? true;
    addEditable("Use Generation Parameters", "use_generation_params", useGenParams, "checkbox");
    
    // Generation / Sampling Parameters (wrapped in a container for show/hide)
    html += `<div class="panel-item" id="generation-params-container" style="${useGenParams ? '' : 'display: none;'}">`;
    html += `<span class="label">Generation Parameters</span>
      <span class="value"><em style="color: var(--muted); font-size: 0.85rem;">Configure sampling parameters below</em></span>
    </div>`;
    
    const genParamsHtml = `
      <div class="panel-item generation-param" data-field="temperature">
        <span class="label">Temperature (--temp)</span>
        <span class="value"><input type="number" class="meta-input" data-field="temperature" value="${data.temperature !== undefined ? data.temperature : ''}" step="0.1" min="0" max="2" placeholder="0.7 (server default)"></span>
      </div>
      <div class="panel-item generation-param" data-field="top_k">
        <span class="label">Top-K (--top-k)</span>
        <span class="value"><input type="number" class="meta-input" data-field="top_k" value="${data.top_k !== undefined ? data.top_k : ''}" step="1" min="0" placeholder="40 (server default)"></span>
      </div>
      <div class="panel-item generation-param" data-field="top_p">
        <span class="label">Top-P (--top-p)</span>
        <span class="value"><input type="number" class="meta-input" data-field="top_p" value="${data.top_p !== undefined ? data.top_p : ''}" step="0.01" min="0" max="1" placeholder="0.9 (server default)"></span>
      </div>
      <div class="panel-item generation-param" data-field="min_p">
        <span class="label">Min-P (--min-p)</span>
        <span class="value"><input type="number" class="meta-input" data-field="min_p" value="${data.min_p !== undefined ? data.min_p : ''}" step="0.01" min="0" max="1" placeholder="0.05 (server default)"></span>
      </div>
      <div class="panel-item generation-param" data-field="repeat_penalty">
        <span class="label">Repeat Penalty (--repeat-penalty)</span>
        <span class="value"><input type="number" class="meta-input" data-field="repeat_penalty" value="${data.repeat_penalty !== undefined ? data.repeat_penalty : ''}" step="0.01" min="0" max="2" placeholder="1.1 (server default)"></span>
      </div>
      <div class="panel-item generation-param" data-field="presence_penalty">
        <span class="label">Presence Penalty (--presence-penalty)</span>
        <span class="value"><input type="number" class="meta-input" data-field="presence_penalty" value="${data.presence_penalty !== undefined ? data.presence_penalty : ''}" step="0.01" min="0" max="2" placeholder="0.0 (server default)"></span>
      </div>
      
      <div class="panel-item generation-param" data-field="reasoning">
        <span class="label">Reasoning (--reasoning)</span>
        <span class="value">
          <select class="meta-input" data-field="reasoning">
            <option value="auto" ${(data.reasoning ?? "auto") === "auto" ? "selected" : ""}>auto (server default)</option>
            <option value="on" ${(data.reasoning ?? "auto") === "on" ? "selected" : ""}>on</option>
            <option value="off" ${(data.reasoning ?? "auto") === "off" ? "selected" : ""}>off</option>
          </select>
        </span>
      </div>
      <div class="panel-item generation-param" data-field="reasoning_effort">
        <span class="label">Reasoning Effort (--reasoning-effort)</span>
        <span class="value">
          <select class="meta-input" data-field="reasoning_effort">
            <option value="default" ${(data.reasoning_effort ?? "default") === "default" ? "selected" : ""}>default (server default)</option>
            <option value="minimal" ${(data.reasoning_effort ?? "default") === "minimal" ? "selected" : ""}>minimal</option>
            <option value="low" ${(data.reasoning_effort ?? "default") === "low" ? "selected" : ""}>low</option>
            <option value="medium" ${(data.reasoning_effort ?? "default") === "medium" ? "selected" : ""}>medium</option>
            <option value="high" ${(data.reasoning_effort ?? "default") === "high" ? "selected" : ""}>high</option>
            <option value="xhigh" ${(data.reasoning_effort ?? "default") === "xhigh" ? "selected" : ""}>xhigh</option>
            <option value="max" ${(data.reasoning_effort ?? "default") === "max" ? "selected" : ""}>max</option>
          </select>
        </span>
      </div>
      <div class="panel-item generation-param" data-field="reasoning_budget">
        <span class="label">Reasoning Budget (--reasoning-budget)</span>
        <span class="value"><input type="number" class="meta-input" data-field="reasoning_budget" value="${data.reasoning_budget !== undefined ? data.reasoning_budget : ''}" step="1" min="-1" placeholder="-1 (disabled, server default)"></span>
      </div>
      <div class="panel-item generation-param" data-field="reasoning_budget_message">
        <span class="label">Reasoning Budget Message (--reasoning-budget-message)</span>
        <span class="value"><input type="text" class="meta-input" data-field="reasoning_budget_message" value="${he(String(data.reasoning_budget_message ?? ""))}" placeholder="(empty, server default)"></span>
      </div>
    `;
    
html += genParamsHtml;
    
    // Reset to Defaults button for generation parameters
    html += `<div class="panel-item generation-param" style="margin-top: 0.5rem;">
      <span class="label"></span>
      <span class="value">
        <button type="button" class="btn-secondary" id="reset-gen-params-${modelSafeId}" style="padding: 0.375rem 0.75rem; font-size: 0.85rem;">
          🔄 Reset Generation Parameters to Server Defaults
        </button>
      </span>
    </div>`;
    
    html += `</div></div>`;

  // Overthinking Penalty
  const overthinkingWords = Array.isArray(data.overthinking_penalty_words)
    ? data.overthinking_penalty_words
    : [];
  html += `<div class="panel-section collapsed">
    <h3>Overthinking Penalty</h3>
    <div class="panel-grid">`;
  addEditable(
    "Enable Overthinking Penalty",
    "overthinking_penalty_enabled",
    data.overthinking_penalty_enabled === true,
    "checkbox",
    undefined,
    undefined,
    undefined,
    "Resolve the configured entries with this model's tokenizer and apply --logit-bias at startup",
  );
  html += `<div class="panel-item full-width" id="overthinking-penalty-settings">
    <span class="label">Penalized Token Strings</span>
    <span class="value">
      <textarea class="meta-input" data-field="overthinking_penalty_words" rows="12" spellcheck="false"
        title="One exact string per line. Leading and trailing spaces are significant; multi-token entries are skipped.">${he(overthinkingWords.join("\n"))}</textarea>
      <small style="color: var(--muted); display: block; margin-top: 0.35rem;">
        One exact string per line; leading spaces are significant. Each entry must tokenize to one token to be applied.
      </small>
    </span>
    <span class="label" style="margin-top: 0.75rem;">Logit Bias (negative values penalize)</span>
    <span class="value">
      <input type="number" class="meta-input" data-field="overthinking_penalty_bias"
        value="${he(String(data.overthinking_penalty_bias ?? -2.0))}" step="0.1" max="0">
    </span>
    <span class="label" style="margin-top: 0.75rem;">Resolved --logit-bias</span>
    <span class="value">
      <textarea rows="4" readonly spellcheck="false" title="Generated and cached when the model starts">${he(String(data.logit_bias || "Not resolved yet; generated on model start"))}</textarea>
      <small style="color: var(--muted); display: block; margin-top: 0.35rem;">
        Resolved with the llama-tokenize binary beside the selected llama-server. Cached IDs invalidate when the model, tokenizer, or entries change.
      </small>
    </span>
  </div>`;
  html += `</div></div>`;

  // Running Statistics section
  if (running) {
    html += `<div class="panel-section collapsed">
      <h3>Running Statistics</h3>
      <div class="panel-grid">`;
    addItem("Status", '<span class="chip ok">● running</span>', false, true);
    if (data.pid) addItem("PID", `<code>${data.pid}</code>`, false, true);
    html += `</div></div>`;
  }

  // Hugging Face Metadata section
  html += `<div class="panel-section collapsed">
    <h3>Model Information</h3>`;

  if (!data.repo) {
    html += `<div id="hf-metadata-missing" style="text-align: center; padding: 1rem 0;">
       <p style="margin-bottom: 0.75rem; color: var(--muted);">Hugging Face repository not configured.</p>
       <button id="hf-find-btn-${modelSafeId}" class="btn-primary" data-model="${modelName}">Find on Hugging Face</button>
       <div id="hf-search-status-${modelSafeId}" style="margin-top: 0.5rem; font-size: 0.875rem; color: var(--muted);"></div>
     </div>`;
  }

  html += `
    <div id="hf-metadata-loading" class="loading" style="display: none;">Loading Hugging Face data...</div>
    <div id="hf-metadata-content" style="display: none;"></div>
    <div id="hf-metadata-error" class="error" style="display: none;"></div>
  </div>`;

  // Performance Benchmarking section
  html += `<div class="panel-section collapsed">
    <h3>Performance Benchmarking</h3>
    <div class="panel-grid">
      <div class="panel-item full-width" style="display: flex; justify-content: flex-end; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap;">
        <button type="button" class="btn-secondary" id="quick-optimize-${modelSafeId}" style="padding: 0.5rem 1rem;">
          ⚡ Quick Optimize (Heuristics)
        </button>
        <button type="button" class="btn-primary" id="optimize-settings-${modelSafeId}" style="padding: 0.5rem 1rem;">
          ⚙️ Optimize Settings (Empirical Benchmarks)
        </button>
        <button type="button" class="btn-primary" id="deep-sweep-${modelSafeId}" style="padding: 0.5rem 1rem;">
          🔍 Deep Sweep (Batched Bench)
        </button>
        <button type="button" class="btn-secondary" id="view-history-${modelSafeId}" style="padding: 0.5rem 1rem;">
          📊 View Historical Benchmark Results
        </button>
      </div>
      <div class="panel-item full-width" style="margin-top: 0.5rem;">
        <div style="display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; font-size: 0.85rem;">
          <label style="display: flex; align-items: center; gap: 0.35rem; cursor: pointer;">
            <span>Max benchmark duration:</span>
            <input type="number" id="max-duration-${modelSafeId}" value="0" min="0" max="3600" step="10" style="width: 70px;">
            <span>seconds (0 = no timeout)</span>
          </label>
          <label style="display: flex; align-items: center; gap: 0.35rem; cursor: pointer;">
            <span>Target:</span>
            <select id="optimize-target-${modelSafeId}" style="width: 120px;">
              <option value="balanced">Balanced</option>
              <option value="throughput">Throughput</option>
              <option value="latency">Latency</option>
            </select>
          </label>
          <label style="display: flex; align-items: center; gap: 0.35rem; cursor: pointer;">
            <input type="checkbox" id="bench-current-config-${modelSafeId}" style="width: 16px; height: 16px;">
            <span>Run benchmark using currently defined property values</span>
          </label>
        </div>
      </div>
      <div class="panel-item full-width" id="optimize-progress-${modelSafeId}" style="margin-top: 0.5rem; display: none;">
        <div style="display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
          <div id="optimize-spinner-${modelSafeId}" style="width: 18px; height: 18px; border: 2px solid var(--surface2); border-top-color: var(--blue); border-radius: 50%; animation: spin 1s linear infinite;"></div>
          <span id="optimize-progress-text-${modelSafeId}" style="font-size: 0.85rem; color: var(--text);">Starting benchmark...</span>
          <button type="button" id="optimize-cancel-${modelSafeId}" class="btn-secondary" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">Cancel</button>
        </div>
        <div id="optimize-progress-bar-container-${modelSafeId}" style="margin-top: 0.5rem; background: var(--surface); border-radius: 4px; height: 6px; overflow: hidden; display: none;">
          <div id="optimize-progress-bar-${modelSafeId}" style="background: var(--blue); height: 100%; width: 0%; transition: width 0.3s;"></div>
        </div>
        <div id="optimize-details-${modelSafeId}" style="margin-top: 0.35rem; font-size: 0.75rem; color: var(--muted); display: none;"></div>
      </div>
    </div>
  </div>`;

  // User Ratings & Comments section
  html += `<div class="panel-section collapsed">
    <h3>User Ratings & Comments</h3>
    <div class="panel-grid">`;
  
  // Speed rating (0-5 stars) with editable input
  const speedVal = data.rating_speed;
  let speedStars = '';
  for (let i = 1; i <= 5; i++) {
    speedStars += `<span class="${i <= speedVal ? 'filled' : ''}">★</span>`;
  }
  html += `<div class="rating-row">
    <span class="label">Speed Rating:</span>
    <div class="rating-input">
      ${speedStars}
    </div>
    <input type="number" min="0" max="5" value="${speedVal || 0}"
      class="meta-input" data-field="rating_speed"
      style="width: 50px; text-align: center;" title="Speed rating (0-5)">
  </div>`;

  // Coding rating (0-5 stars) with editable input
  const codingVal = data.rating_coding;
  let codingStars = '';
  for (let i = 1; i <= 5; i++) {
    codingStars += `<span class="${i <= codingVal ? 'filled' : ''}">★</span>`;
  }
  html += `<div class="rating-row">
    <span class="label">Coding Skills Rating:</span>
    <div class="rating-input">
      ${codingStars}
    </div>
    <input type="number" min="0" max="5" value="${codingVal || 0}"
      class="meta-input" data-field="rating_coding"
      style="width: 50px; text-align: center;" title="Coding rating (0-5)">
  </div>`;
  
    // Comment text area
    html += `<div class="rating-row full-width">
      <label for="model-comment-${modelSafeId}" class="label">Your Comments:</label>
      <textarea id="model-comment-${modelSafeId}" 
        class="meta-input text" 
        data-field="comment" 
        rows="3" 
        placeholder="Share your thoughts about this model...">${he(data.comment || "")}</textarea>
    </div>`;

  // Close User Ratings & Comments panel-section
  html += `</div></div>`;

  // Kilo Code Configuration section
  html += `<div class="panel-section collapsed">
     <h3>Kilo Code Integration</h3>
     <div class="panel-grid">
       <div class="panel-item full-width">
         <button id="generate-config-${modelSafeId}" class="btn-primary">📋 Generate Kilo Code Config</button>
         <p style="margin-top: 0.5rem; font-size: 0.85rem; color: var(--text2);">
           Generate a provider configuration for Kilo Code based on this model's settings.
         </p>
       </div>
     </div>
   </div>`;
  // Add the previously removed Save/Cancel buttons after Kilo Code section
  html += `<div class="panel-item full-width" style="margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--surface2);">
     <button type="button" class="btn-cancel" id="meta-cancel-${modelSafeId}" style="padding: 0.5rem 1rem;">Cancel</button>
     <button type="button" class="btn-primary" id="meta-save-${modelSafeId}" style="padding: 0.5rem 1rem;">Save</button>
   </div>`;

  content.innerHTML = html;

// Toggle panels
content.querySelectorAll('.panel-section h3').forEach(h3 => {
  h3.addEventListener('click', () => {
    const section = h3.parentElement;
    section.classList.toggle('collapsed');
  });
});

  // Initialize browse buttons
  initBrowseButtons(content);

  // Initialize Use Generation Parameters checkbox toggle
  const useGenParamsCheckbox = content.querySelector('.meta-input[data-field="use_generation_params"]');
  const genParamsContainer = content.querySelector('#generation-params-container');
  if (useGenParamsCheckbox && genParamsContainer) {
    const toggleGenParams = () => {
      const show = useGenParamsCheckbox.checked;
      genParamsContainer.style.display = show ? '' : 'none';
      // Also hide/show all generation-param items
      content.querySelectorAll('.generation-param').forEach(el => {
        el.style.display = show ? '' : 'none';
      });
    };
    useGenParamsCheckbox.addEventListener('change', toggleGenParams);
    // Initial state
    toggleGenParams();
  }

  // Show overthinking settings only when the feature is enabled.
  const overthinkingEnabledCheckbox = content.querySelector('.meta-input[data-field="overthinking_penalty_enabled"]');
  const overthinkingSettings = content.querySelector('#overthinking-penalty-settings');
  if (overthinkingEnabledCheckbox && overthinkingSettings) {
    const toggleOverthinkingSettings = () => {
      overthinkingSettings.style.display = overthinkingEnabledCheckbox.checked ? '' : 'none';
    };
    overthinkingEnabledCheckbox.addEventListener('change', toggleOverthinkingSettings);
    toggleOverthinkingSettings();
  }

  // Reset to Default Parameters button handler
  const resetBtn = content.querySelector('#resetPropsToDefaultsBtn');
  if (resetBtn) {
    resetBtn.addEventListener('click', async () => await resetPropertiesToDefaults(content, modelName));
  }

  /**
   * Reset all properties panel fields to their default values
   * @param {HTMLElement} content - The panel content container
   * @param {string} modelName - The model name (used for slot paths)
   */
  async function resetPropertiesToDefaults(content, modelName) {
    // Default values from config.py DEFAULTS
    const defaults = {
      // Basic Configuration
      'port': 9931,
      'bind_host': '0.0.0.0',
      'reuse_port': true,
      'log_verbosity': 4,
      'llama_cpp_platform': 'auto',
      'llama_cpp_backend': 'auto',
      
      // Performance & Threading
      'threads': 16,
      'threads_batch': 16,
      'timeout': 3600,
      'parallel': '1',
      'ubatch_size': 4096,
      'batch_size': 32768,
      
      // Memory Management
      'mlock': true,
      'no_mmap': false,
      'numa': true,
      'numa_value': 'distribute',
      'cpu_affinity': 'auto',
      'cpu_strict': true,
      
      // GPU & Acceleration
      'gpu_layers': 99,
      'cpu_moe': false,
      'n_cpu_moe': 0,
      'moe_cache_mib': 0,
      'flash_attn': 'on',
      
      // KV Cache
      'cache_type_k': 'f16',
      'cache_type_v': 'f16',
      
      // Context & RoPE/YaRN
      'ctx_size': 131072,
      'rope_freq_base': 10000.0,
      'rope_freq_scale': 1.0,
      'rope_scaling_type': 'none',
      'rope_scale': 1.0,
      'yarn_orig_ctx': 0,
      'yarn_ext_factor': -1.0,
      'yarn_attn_factor': 1.0,
      'yarn_beta_slow': 1.0,
      'yarn_beta_fast': 32.0,
      
      // Speculative Decoding
      'spec_type': 'ngram-mod',
      'spec_draft_model': '',
      'spec_draft_hf_repo': '',
      'spec_draft_n_max': 8,
      'spec_draft_n_min': 3,
      'spec_draft_p_split': 0.10,
      'spec_draft_p_min': 0.00,
      'spec_draft_ngl': 'auto',
      'spec_draft_temp': 0.0,
      'spec_draft_top_p': 1.0,
      'spec_draft_min_p': 0.0,
      'spec_draft_typical': 1.0,
      'spec_ngram_size_n': 12,
      'spec_ngram_size_m': 48,
      'spec_ngram_min_hits': 1,
      'spec_ngram_mod_n_match': 24,
      'spec_ngram_mod_n_min': 48,
      'spec_ngram_mod_n_max': 64,
      'spec_draft_type_k': 'f16',
      'spec_draft_type_v': 'f16',
      
      // Warmup
      'warmup_enabled': false,
      'warmup_message': '// TODO: add two numbers def add(a, b):',
      'warmup_max_tokens': 10,
      
      // Slot KV Cache Persistence
      'slot_save_path': `./data/${modelName}/slots`,
      'cache_reuse': 32768,
      'keep': 32768,
      'swa_full': false,
      
      // Chat template
      'chat_template_file': '',
      
      // Multimodal projector
      'mmproj': '',
      'mmproj_auto': true,
      'mmproj_offload': true,
      
      // Generation / Sampling parameters
      'use_generation_params': false,
      'temperature': 0.7,
      'top_k': 40,
      'top_p': 0.9,
      'min_p': 0.05,
      'repeat_penalty': 1.1,
      'presence_penalty': 0.0,
      'reasoning': 'auto',
      'reasoning_effort': 'default',
      'reasoning_budget': -1,
      'reasoning_budget_message': '',

      // Overthinking logit penalty
      'overthinking_penalty_enabled': false,
      'overthinking_penalty_words': [
        ' perhaps', ' maybe', ' wait', ' Wait', ' actually',
        ' hold', ' Hmm', ' hmm', ' Alternatively', ' alternatively',
        ' However', ' however', ' instead', ' Instead', ' But',
        ' but', ' though', ' although', ' yet', ' rather',
        ' unless', ' otherwise', ' nonetheless', ' nevertheless', ' regardless',
        ' still', ' anyway', ' Or', ' or', ' either',
        ' whether', ' uncertain', ' unsure', ' possibly', ' might',
        ' could', ' another', ' different', ' reconsider', ' rethink',
        ' backtrack', ' retry', ' revisit', ' doubt', ' confused',
        ' wrong', ' mistake', ' error', ' incorrect',
      ],
      'overthinking_penalty_bias': -2.0,
      
      // Intel/AMD GPU Options
      'sycl': false,
      'grp_attn': false,
      'grp_attn_n': 8,
      
      // Mnemonic Proxy
      'mnemonic_proxy.enabled': false,
      'mnemonic_proxy.port': 8096,
      'mnemonic_proxy.data_dir': `./data/${modelName}`,
      'mnemonic_proxy.slot_dir': `./data/${modelName}/slots`,
      'mnemonic_proxy.tokenizer_path': '',
      // mnemonic_proxy.config_override will be fetched from API
      'mnemonic_proxy.mcp_enabled': false,
      'mnemonic_proxy.mcp_port': 8133,
    };
    
    // Fetch mnemonic default config override from API
    let mnemonicConfigOverride = {"mask_anchor": true, "autosave": true, "window_estimate_slack": 0.15};
    try {
      const resp = await fetch('/api/mnemonic-default-config-override');
      if (resp.ok) {
        mnemonicConfigOverride = await resp.json();
      }
    } catch (e) {
      console.warn('Failed to fetch mnemonic default config, using fallback:', e);
    }
    
    // Set each field to its default value
    Object.entries(defaults).forEach(([field, value]) => {
      const input = content.querySelector(`.meta-input[data-field="${field}"]`);
      if (input) {
        if (input.type === 'checkbox') {
          input.checked = value;
        } else if (input.multiple) {
          // Handle multi-select (spec_type)
          const values = Array.isArray(value) ? value : String(value).split(',').map(v => v.trim());
          Array.from(input.options).forEach(opt => {
            opt.selected = values.includes(opt.value);
          });
        } else if (input.tagName === 'SELECT') {
          input.value = value;
        } else if (field === 'overthinking_penalty_words') {
          input.value = value.join('\n');
        } else if (input.tagName === 'TEXTAREA') {
          input.value = JSON.stringify(value, null, 2);
        } else {
          input.value = value;
        }
      }
    });
    
    // Set mnemonic proxy config override from fetched API
    const mnemonicConfigTextarea = content.querySelector('.meta-input[data-field="mnemonic_proxy.config_override"]');
    if (mnemonicConfigTextarea) {
      mnemonicConfigTextarea.value = JSON.stringify(mnemonicConfigOverride, null, 2);
    }
    
    // Trigger change event for Use Generation Parameters to update visibility
    const useGenParamsCheckbox = content.querySelector('.meta-input[data-field="use_generation_params"]');
    if (useGenParamsCheckbox) {
      useGenParamsCheckbox.dispatchEvent(new Event('change'));
    }
    
    // Trigger change event for CPU MoE to update N CPU MoE visibility
    const cpuMoeCheckbox = content.querySelector('.meta-input[data-field="cpu_moe"]');
    if (cpuMoeCheckbox) {
      cpuMoeCheckbox.dispatchEvent(new Event('change'));
    }

    const overthinkingEnabledCheckbox = content.querySelector('.meta-input[data-field="overthinking_penalty_enabled"]');
    if (overthinkingEnabledCheckbox) {
      overthinkingEnabledCheckbox.dispatchEvent(new Event('change'));
    }
    
    logHtml('<b>Properties reset to default parameters</b>', 'green');
  }

  // Initialize CPU MoE checkbox toggle (shows/hides N CPU MoE field and MoE Cache field)
  const cpuMoeCheckbox = content.querySelector('.meta-input[data-field="cpu_moe"]');
  const nCpuMoeRow = content.querySelector('#n-cpu-moe-row');
  const moeCacheMibRow = content.querySelector('#moe-cache-mib-row');
  if (cpuMoeCheckbox && nCpuMoeRow && moeCacheMibRow) {
    const toggleCpuMoe = () => {
      const cpuMoeChecked = cpuMoeCheckbox.checked;
      const nCpuMoeInput = content.querySelector('.meta-input[data-field="n_cpu_moe"]');
      const nCpuMoeVal = nCpuMoeInput ? parseInt(nCpuMoeInput.value, 10) || 0 : 0;
      const showNcpuMoe = !cpuMoeChecked; // Show n_cpu_moe when cpu_moe is FALSE
      const showMoeCache = cpuMoeChecked || nCpuMoeVal > 0; // Show moe_cache_mib when cpu_moe is TRUE or n_cpu_moe > 0
      nCpuMoeRow.style.display = showNcpuMoe ? '' : 'none';
      moeCacheMibRow.style.display = showMoeCache ? '' : 'none';
    };
    cpuMoeCheckbox.addEventListener('change', toggleCpuMoe);
    // Also listen for changes to n_cpu_moe to update moe_cache_mib visibility
    const nCpuMoeInput = content.querySelector('.meta-input[data-field="n_cpu_moe"]');
    if (nCpuMoeInput) {
      nCpuMoeInput.addEventListener('input', toggleCpuMoe);
      nCpuMoeInput.addEventListener('change', toggleCpuMoe);
    }
    // Initial state
    toggleCpuMoe();
  }

  // Reset Generation Parameters button handler
  const resetGenParamsBtn = document.getElementById(`reset-gen-params-${modelSafeId}`);
  if (resetGenParamsBtn) {
    resetGenParamsBtn.addEventListener("click", () => {
      // Clear all generation parameter inputs (empty = use server defaults)
      const genParamFields = [
        "temperature", "top_k", "top_p", "min_p", 
        "repeat_penalty", "presence_penalty",
        "reasoning_budget", "reasoning_budget_message"
      ];
      genParamFields.forEach(field => {
        const input = content.querySelector(`.meta-input[data-field="${field}"]`);
        if (input) {
          input.value = "";
        }
      });
      // Reset dropdowns to server defaults
      const reasoningSelect = content.querySelector(`.meta-input[data-field="reasoning"]`);
      if (reasoningSelect) reasoningSelect.value = "auto";
      const reasoningEffortSelect = content.querySelector(`.meta-input[data-field="reasoning_effort"]`);
      if (reasoningEffortSelect) reasoningEffortSelect.value = "default";
    });
  }

  // Attach meta input listeners (no auto-save - values collected on Save)
  content.closest(".properties-panel").dataset.modelName = modelName;

  // Save button handler
  const saveBtn = document.getElementById(`meta-save-${modelSafeId}`);
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const panel = document.getElementById("properties-panel");
      const model = panel.dataset.modelName;
      if (!model) return;

      const payload = { model };
      let hasChanges = false;
      const mnemonicProxyFields = {};

      content.querySelectorAll(".meta-input").forEach((input) => {
        const field = input.dataset.field;
        let value;
        if (input.multiple) {
          // Handle multi-select: get all selected options
          value = Array.from(input.selectedOptions).map(opt => opt.value).join(",");
        } else if (input.type === "checkbox") {
          value = input.checked;
        } else {
          value = input.value;
        }
        if (field === "overthinking_penalty_words") {
          // Preserve leading/trailing spaces: they are part of the token string.
          value = value.split(/\r?\n/).filter(word => word.trim().length > 0);
        }
        const numericFields = [
          "threads", "threads_batch", "timeout",
          "ubatch_size", "gpu_layers", "batch_size",
          "ctx_size", "rope_freq_base", "rope_freq_scale",
          "rope_scale", "yarn_orig_ctx", "yarn_ext_factor",
          "yarn_attn_factor", "yarn_beta_slow", "yarn_beta_fast",
          "spec_draft_n_max", "spec_draft_n_min", "spec_draft_p_split",
          "spec_draft_p_min", "spec_ngram_size_n", "spec_ngram_size_m",
          "spec_ngram_min_hits",
          "grp_attn_n",
          "rating_speed", "rating_coding",
          // MoE CPU offloading
          "n_cpu_moe",
          // Generation / Sampling parameters
          "temperature", "top_k", "top_p", "min_p",
          "repeat_penalty", "presence_penalty",
          "reasoning_budget", "overthinking_penalty_bias",
          // Mnemonic Proxy numeric fields
          "mnemonic_proxy.port", "mnemonic_proxy.mcp_port"
        ];
        if (numericFields.includes(field)) {
          // If empty string, treat as null (to clear the field)
          value = value === "" ? null : Number(value);
        }

        // Handle mnemonic_proxy nested object fields
        if (field.startsWith("mnemonic_proxy.")) {
          const subField = field.substring("mnemonic_proxy.".length);
          const originalMP = window.originalModelMeta?.mnemonic_proxy || {};
          const originalValue = originalMP[subField];
          
          // Special handling for config_override (JSON)
          if (subField === "config_override") {
            try {
              value = value ? JSON.parse(value) : {};
            } catch (e) {
              console.warn("Invalid JSON in config_override:", e);
              value = {};
            }
          }
          
          // Always collect the current value from UI for mnemonic_proxy fields
          mnemonicProxyFields[subField] = value;
          
          const isChanged = JSON.stringify(value) !== JSON.stringify(originalValue);
          if (isChanged) {
            hasChanges = true;
          }
          return; // Skip normal field processing
        }

        // Only include changed values
        const originalValue = window.originalModelMeta?.[field];
        // For parallel field, compare as strings since it can be "auto" or numeric
        const isChanged = field === "parallel"
          ? String(value) !== String(originalValue)
          : field === "overthinking_penalty_words"
            ? JSON.stringify(value) !== JSON.stringify(originalValue)
            : value !== originalValue;
        
        // For generation parameters:
        // - Empty string/null means "use server default" - send null to clear the field
        // - Only include if changed (including clearing a previously set value)
        const genParamFields = ["temperature", "top_k", "top_p", "min_p", "repeat_penalty", "presence_penalty", "reasoning", "reasoning_effort", "reasoning_budget", "reasoning_budget_message"];
        const isGenParam = genParamFields.includes(field);
        const isGenParamCleared = isGenParam && (value === "" || value === null);
        
        if (isChanged) {
          // For cleared generation params, send null to delete the field
          payload[field] = isGenParamCleared ? null : value;
          hasChanges = true;
        }
      });

      // Add mnemonic_proxy object if any fields changed
      if (Object.keys(mnemonicProxyFields).length > 0) {
        payload.mnemonic_proxy = mnemonicProxyFields;
      }

      if (!hasChanges) {
        alert("No changes to save");
        return;
      }

      // Check if model is currently running
      const running = data.status && data.status.startsWith("running");

      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";

      try {
        const resp = await fetch("/api/update-model-meta", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          alert(`Error saving changes: ${err.error || "unknown"}`);
        } else {
          // Update original values - deep merge for mnemonic_proxy to preserve unchanged fields
          if (payload.mnemonic_proxy && window.originalModelMeta.mnemonic_proxy) {
            window.originalModelMeta.mnemonic_proxy = {
              ...window.originalModelMeta.mnemonic_proxy,
              ...payload.mnemonic_proxy
            };
            // Remove mnemonic_proxy from payload so it doesn't get shallow-merged again
            const { mnemonic_proxy, ...restPayload } = payload;
            Object.assign(window.originalModelMeta, restPayload);
          } else {
            Object.assign(window.originalModelMeta, payload);
          }
          
          // Force refresh models cache by re-reading models.json
          try {
            const refreshResp = await fetch("/api/refresh-models", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
            });
            if (refreshResp.ok) {
              const refreshData = await refreshResp.json();
              if (refreshData.status === "refreshed") {
                console.log("Models cache refreshed:", refreshData.count, "models loaded");
              } else {
                console.warn("Models cache refresh returned unexpected status:", refreshData);
              }
            } else {
              const err = await refreshResp.json().catch(() => ({}));
              console.warn("Failed to refresh models cache:", err.error || "unknown");
            }
          } catch (refreshErr) {
            console.warn("Failed to refresh models cache:", refreshErr);
          }

          // If model is running, ask if user wants to restart with new settings
          if (running) {
            const shouldRestart = confirm(
              "Changes saved successfully.\n\n" +
              "The model is currently running. Some changes (like threads, GPU layers, context size, " +
              "KV cache settings, etc.) require a restart to take effect.\n\n" +
              "Do you want to restart the model now with the new settings?\n\n" +
              "Note: Slot persistence (slot_save_path, cache_reuse) will be preserved if configured."
            );
            
            if (shouldRestart) {
              saveBtn.disabled = true;
              saveBtn.textContent = "Restarting…";
              
              try {
                // Stop the model
                const stopResp = await fetch("/api/stop", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ model }),
                });
                const stopData = await stopResp.json();
                if (!stopResp.ok || !stopData.success) {
                  throw new Error(stopData.error || "Failed to stop model");
                }
                
                // Wait a bit for the model to fully stop
                await new Promise(resolve => setTimeout(resolve, 1500));
                
                // Start the model with new settings
                const startResp = await fetch("/api/start", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ model }),
                });
                const startData = await startResp.json();
                if (!startResp.ok) {
                  throw new Error(startData.error || "Failed to start model");
                }
                
                alert("Model restarted successfully with new settings!");
                // Refresh the UI to show updated status in both panel and tile grid
                if (typeof refresh === "function") {
                  await refresh(false);
                }
              } catch (restartErr) {
                console.error("Failed to restart model:", restartErr);
                alert(`Model changes saved, but restart failed: ${restartErr.message}\n\nYou can manually start the model from the panel.`);
                // Still refresh UI to show current state
                if (typeof refresh === "function") {
                  try { await refresh(false); } catch (e) { console.warn("Refresh after restart failure:", e); }
                }
              } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = "Save";
              }
            } else {
              alert("Changes saved successfully. Restart the model manually for changes to take effect.");
              // Still refresh the UI to show updated values
              if (typeof refresh === "function") {
                try { await refresh(false); } catch (e) { console.warn("Refresh after save:", e); }
              }
            }
          } else {
            alert("Changes saved successfully");
            // Auto-refresh model configuration to reflect changes
            if (typeof refresh === "function") {
              try { await refresh(false); } catch (e) { console.warn("Refresh after save:", e); }
            }
          }
        }
      } catch (err) {
        console.error("Failed to update meta", err);
        alert("Network error while saving");
      } finally {
        if (saveBtn.textContent === "Saving…" || saveBtn.textContent === "Restarting…") {
          saveBtn.disabled = false;
          saveBtn.textContent = "Save";
        }
      }
    });
  }

  // Cancel button handler
  const cancelBtn = document.getElementById(`meta-cancel-${modelSafeId}`);
  if (cancelBtn) {
    cancelBtn.addEventListener("click", () => {
      // Restore original values in the form
      content.querySelectorAll(".meta-input").forEach((input) => {
        const field = input.dataset.field;
        const originalValue = window.originalModelMeta?.[field];
        if (originalValue !== undefined) {
          if (input.type === "checkbox") {
            input.checked = originalValue;
          } else if (input.tagName === "SELECT") {
            input.value = originalValue;
          } else {
            input.value = originalValue;
          }
        }
      });
    });
  }

  // Refresh config override button handler
  const refreshConfigOverrideBtn = content.querySelector(".refresh-config-override");
  if (refreshConfigOverrideBtn) {
    refreshConfigOverrideBtn.addEventListener("click", async () => {
      const btn = refreshConfigOverrideBtn;
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Loading…";
      try {
        const resp = await fetch("/api/mnemonic-default-config-override");
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`);
        }
        const defaultConfig = await resp.json();
        const textarea = content.querySelector('.meta-input[data-field="mnemonic_proxy.config_override"]');
        if (textarea) {
          textarea.value = JSON.stringify(defaultConfig, null, 2);
        }
        btn.textContent = "Refreshed!";
        setTimeout(() => {
          btn.textContent = originalText;
          btn.disabled = false;
        }, 1500);
      } catch (err) {
        console.error("Failed to load default config override:", err);
        alert("Failed to load default config override: " + err.message);
        btn.textContent = originalText;
        btn.disabled = false;
      }
    });
  }

  // Attach generate config button listener
  const configBtn = document.getElementById(`generate-config-${modelSafeId}`);
  if (configBtn) {
    configBtn.addEventListener("click", () => {
      // Read current mmproj value from UI input (may have been changed but not saved yet)
      const mmprojInput = content.querySelector('.meta-input[data-field="mmproj"]');
      const mmprojAutoInput = content.querySelector('.meta-input[data-field="mmproj_auto"]');
      const mmprojOffloadInput = content.querySelector('.meta-input[data-field="mmproj_offload"]');
      const currentData = { ...data };
      if (mmprojInput) currentData.mmproj = mmprojInput.value;
      if (mmprojAutoInput) currentData.mmproj_auto = mmprojAutoInput.checked;
      if (mmprojOffloadInput) currentData.mmproj_offload = mmprojOffloadInput.checked;
      openKiloCodeModal(currentData, modelName);
    });
  }

  // Attach Open in Archive Browser button listener
  const openArchiveBtn = document.getElementById(`open-archive-browser-${modelSafeId}`);
  if (openArchiveBtn) {
    openArchiveBtn.addEventListener("click", () => {
      if (window.ArchiveBrowser) {
        window.ArchiveBrowser.open(modelName);
      }
    });
  }

  // Toggle Archive Browser button visibility based on proxy enabled checkbox
  const proxyEnabledCheckbox = content.querySelector('.meta-input[data-field="mnemonic_proxy.enabled"]');
  const archiveBtnContainer = document.getElementById(`archive-browser-btn-container`);
  if (proxyEnabledCheckbox && archiveBtnContainer) {
    const toggleArchiveBtn = () => {
      archiveBtnContainer.style.display = proxyEnabledCheckbox.checked ? '' : 'none';
    };
    proxyEnabledCheckbox.addEventListener('change', toggleArchiveBtn);
    // Initial state
    toggleArchiveBtn();
  }

  // Attach optimize settings button listener (Empirical Benchmarks)
  const optimizeBtn = document.getElementById(`optimize-settings-${modelSafeId}`);
  if (optimizeBtn) {
    optimizeBtn.addEventListener("click", async () => {
      await runOptimization(modelName, modelSafeId, content, true);
    });
  }

  // Attach quick optimize button listener (Heuristics)
  const quickOptimizeBtn = document.getElementById(`quick-optimize-${modelSafeId}`);
  if (quickOptimizeBtn) {
    quickOptimizeBtn.addEventListener("click", async () => {
      await runOptimization(modelName, modelSafeId, content, false);
    });
  }

  // Attach view history button listener
  const viewHistoryBtn = document.getElementById(`view-history-${modelSafeId}`);
  if (viewHistoryBtn) {
    viewHistoryBtn.addEventListener("click", async () => {
      await viewHistoricalBenchmarks(modelName, modelSafeId);
    });
  }

  // Attach deep sweep button listener
  const deepSweepBtn = document.getElementById(`deep-sweep-${modelSafeId}`);
  if (deepSweepBtn) {
    deepSweepBtn.addEventListener("click", async () => {
      await runDeepSweep(modelName, modelSafeId, content);
    });
  }

  // Shared optimization logic
  async function runOptimization(modelName, modelSafeId, content, useEmpirical) {
    const statusEl = document.getElementById(`optimize-status-${modelSafeId}`);
    const progressContainer = document.getElementById(`optimize-progress-${modelSafeId}`);
    const progressText = document.getElementById(`optimize-progress-text-${modelSafeId}`);
    const progressBarContainer = document.getElementById(`optimize-progress-bar-container-${modelSafeId}`);
    const progressBar = document.getElementById(`optimize-progress-bar-${modelSafeId}`);
    const detailsEl = document.getElementById(`optimize-details-${modelSafeId}`);
    const cancelBtn = document.getElementById(`optimize-cancel-${modelSafeId}`);
    const maxDurationInput = document.getElementById(`max-duration-${modelSafeId}`);
    const targetSelect = document.getElementById(`optimize-target-${modelSafeId}`);
    const benchCurrentConfigCheckbox = document.getElementById(`bench-current-config-${modelSafeId}`);

    const btn = useEmpirical ? optimizeBtn : quickOptimizeBtn;
    btn.disabled = true;
    btn.textContent = useEmpirical ? "[Optimize] Benchmarking…" : "[Quick] Computing…";
    
    if (statusEl) {
      statusEl.textContent = "";
      statusEl.style.color = "var(--muted)";
    }
    
    // Show progress UI for empirical mode
    if (useEmpirical) {
      progressContainer.style.display = "block";
      progressBarContainer.style.display = "block";
      detailsEl.style.display = "block";
      progressText.textContent = "Initializing benchmark...";
      progressBar.style.width = "0%";
      detailsEl.textContent = "";
    }

    // Create abort controller for cancellation
    const abortController = new AbortController();
    let isCancelled = false;

    // Setup cancel button
    if (cancelBtn) {
      cancelBtn.onclick = async () => {
        isCancelled = true;
        progressText.textContent = "Cancelling...";
        cancelBtn.disabled = true;
        cancelBtn.textContent = "Cancelling...";
        
        // Signal cancellation to the server
        try {
          const resp = await fetch("/api/benchmark-cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model: modelName }),
          });
          if (resp.ok) {
            const data = await resp.json();
            if (data.success) {
              log("Benchmark cancelled on server");
            } else {
              log("Failed to cancel benchmark on server: " + data.message);
            }
          } else {
            log("Failed to cancel benchmark: " + (await resp.text()));
          }
        } catch (err) {
          log("Error cancelling benchmark: " + err);
        }
      };
    }

    try {
      // Determine which endpoint to call
      const endpoint = useEmpirical ? "/api/benchmark-optimize" : "/api/optimize-settings";
      
      // Build request body
      const requestBody = { model: modelName };
      if (useEmpirical) {
        requestBody.max_duration_sec = parseInt(maxDurationInput.value, 10);
        if (isNaN(requestBody.max_duration_sec)) {
          requestBody.max_duration_sec = 0;
        }
        requestBody.target = targetSelect.value || "balanced";
        
        // Check if "Run benchmark using currently defined property values" is checked
        if (benchCurrentConfigCheckbox && benchCurrentConfigCheckbox.checked) {
          // Read current form values from the panel
          const currentConfig = {};
          content.querySelectorAll(".meta-input").forEach((input) => {
            const field = input.dataset.field;
            if (field) {
              let value;
              if (input.type === "checkbox") {
                value = input.checked;
              } else if (input.tagName === "SELECT") {
                value = input.value;
              } else {
                value = input.value;
                // Try to convert to number if it looks like a number
                if (value !== "" && !isNaN(value)) {
                  value = parseFloat(value);
                }
              }
              // Map field names to optimizer parameter names
              const fieldMap = {
                "gpu_layers": "n_gpu_layers",
                "threads_batch": "threads_batch",
                "flash_attn": "flash_attn",
                "cache_type_k": "cache_type_k",
                "cache_type_v": "cache_type_v",
                "numa": "numa",
                "numa_value": "numa_value",
                "cpu_affinity": "cpu_affinity",
                "cpu_strict": "cpu_strict",
                "batch_size": "batch_size",
                "ubatch_size": "ubatch_size",
                "threads": "threads",
                "ctx_size": "ctx_size",
                "parallel": "parallel",
                "spec_type": "spec_type",
                "spec_draft_n_max": "spec_draft_n_max",
                "cache_reuse": "cache_reuse",
                "cache_ram": "cache_ram",
                "swa_full": "swa_full",
              };
              const mappedField = fieldMap[field] || field;
              currentConfig[mappedField] = value;
            }
          });
          requestBody.test_current_config = true;
          requestBody.current_config = currentConfig;
        }
      }

      const fetchPromise = fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
        signal: abortController.signal,
      });

      // For empirical mode, simulate progress updates since we can't get real-time progress from the server
      let progressInterval = null;
      let progressStartTime = Date.now();
      let estimatedTotal = requestBody.max_duration_sec || 60;
      
      // Check if running single config test (checkbox checked)
      const isSingleConfigTest = useEmpirical && requestBody.test_current_config === true;
      
      if (useEmpirical) {
        progressInterval = setInterval(() => {
          if (isCancelled) return;
          
          const elapsed = (Date.now() - progressStartTime) / 1000;
          const progressPct = Math.min(95, (elapsed / estimatedTotal) * 100);
          progressBar.style.width = `${progressPct}%`;
          
          if (isSingleConfigTest) {
            // Single config test - show single benchmark progress
            progressText.textContent = `Running single benchmark... (${Math.floor(elapsed)}s / ~${estimatedTotal}s)`;
            
            if (progressPct < 10) {
              detailsEl.textContent = "Initializing benchmark...";
            } else if (progressPct < 40) {
              detailsEl.textContent = "Running warmup...";
            } else if (progressPct < 85) {
              detailsEl.textContent = "Running timed iterations...";
            } else {
              detailsEl.textContent = "Finalizing results...";
            }
          } else {
            // Full optimization sweep - generic progress (configs vary by hardware)
            const configNum = Math.min(12, Math.max(1, Math.floor(progressPct / 8) + 1));
            
            progressText.textContent = `Running benchmarks... (${Math.floor(elapsed)}s / ~${estimatedTotal}s)`;
            detailsEl.textContent = `Testing configuration ${configNum} of ~12...`;
            
            if (elapsed > estimatedTotal) {
              progressText.textContent = `Benchmark running longer than estimated (${Math.floor(elapsed)}s)...`;
              detailsEl.textContent = "Finalizing results...";
            }
          }
        }, 2000);
      }

      const resp = await fetchPromise;
      
      if (progressInterval) {
        clearInterval(progressInterval);
      }
      
      const result = await resp.json();
      
      if (!resp.ok) {
        throw new Error(result.error || "Failed to optimize settings");
      }

      // Update progress to 100% for empirical mode
      if (useEmpirical) {
        progressBar.style.width = "100%";
        progressText.textContent = "Benchmark complete!";
        detailsEl.textContent = "";
      }

      const settings = result.optimal_settings;
      const hardware = result.hardware_info || result.hardware;

      // Populate form fields with optimal settings
      let updatedCount = 0;
      content.querySelectorAll(".meta-input").forEach((input) => {
        const field = input.dataset.field;
        if (settings.hasOwnProperty(field)) {
          const value = settings[field];
          if (input.type === "checkbox") {
            input.checked = Boolean(value);
          } else if (input.tagName === "SELECT") {
            input.value = String(value);
          } else {
            input.value = value;
          }
          updatedCount++;
        }
      });

      // Note: do NOT update originalModelMeta here - save should compare
      // against the pre-optimization baseline so changes are detected

      // Build status message
      let statusMsg = "";
      if (useEmpirical) {
        const bestResult = result.benchmark_results && result.benchmark_results.length > 0 
          ? result.benchmark_results.reduce((best, r) => (r.tg_avg > best.tg_avg ? r : best), result.benchmark_results[0])
          : null;
        
        const cachedStr = result.cached ? " (cached)" : "";
        const fallbackStr = result.fallback_reason ? ` ⚠ Fallback: ${result.fallback_reason}` : "";
        const throughputStr = bestResult ? `Best: ${bestResult.tg_avg.toFixed(1)} tok/s` : "";
        const configStr = bestResult && bestResult.config ? ` (gpu_layers=${bestResult.config.n_gpu_layers || bestResult.config.gpu_layers}, batch=${bestResult.config.batch_size})` : "";
        
        statusMsg = `Empirical optimization complete${cachedStr}! ${throughputStr}${configStr}. Applied ${updatedCount} settings. Duration: ${result.duration_sec}s.${fallbackStr}`;
      } else {
        statusMsg = `Heuristic optimization complete! Applied ${updatedCount} settings based on: ${hardware.cpu_count_logical} CPUs, ${hardware.memory_total_gb}GB RAM${hardware.total_gpu_vram_gb ? `, ${hardware.total_gpu_vram_gb}GB GPU VRAM` : ""}${hardware.has_nvidia_gpu ? " (NVIDIA)" : ""}${hardware.has_amd_gpu ? " (AMD)" : ""}`;
      }

      if (statusEl) {
        statusEl.textContent = statusMsg;
        statusEl.style.color = result.fallback_reason ? "var(--orange)" : "var(--green)";
      }

      // Show summary alert with option to view full results
      if (useEmpirical && result.benchmark_results && result.benchmark_results.length > 0) {
        const viewDetails = confirm(
          `${statusMsg}\n\nClick OK to view detailed benchmark results, or Cancel to continue.`
        );
        if (viewDetails) {
          showBenchmarkResultsModal(result);
        }
      } else if (!useEmpirical) {
        alert(`Optimization complete! Applied ${updatedCount} settings. Review and click Save to persist.`);
      }
      
    } catch (err) {
      if (progressInterval) {
        clearInterval(progressInterval);
      }
      
      console.error("Optimization failed", err);
      
      if (err.name === "AbortError" || isCancelled) {
        if (statusEl) {
          statusEl.textContent = "Optimization cancelled by user";
          statusEl.style.color = "var(--orange)";
        }
        if (useEmpirical) {
          progressText.textContent = "Cancelled";
          detailsEl.textContent = "";
        }
      } else {
        if (statusEl) {
          statusEl.textContent = `Error: ${err.message}`;
          statusEl.style.color = "var(--red)";
        }
        if (useEmpirical) {
          progressText.textContent = `Error: ${err.message}`;
          detailsEl.textContent = "";
        }
        alert(`Optimization failed: ${err.message}`);
      }
    } finally {
      btn.disabled = false;
      btn.textContent = useEmpirical ? "[Optimize] Optimize Settings (Empirical Benchmarks)" : "[Quick] Quick Optimize (Heuristics)";
      
      if (useEmpirical) {
        progressContainer.style.display = "none";
        cancelBtn.disabled = false;
        cancelBtn.textContent = "Cancel";
      }
    }
  }

  // View historical benchmarks without running new ones
  async function viewHistoricalBenchmarks(modelName, modelSafeId) {
    const btn = document.getElementById(`view-history-${modelSafeId}`);
    if (btn) {
      btn.disabled = true;
      btn.textContent = "[History] Loading…";
    }
    
    try {
      const resp = await fetch("/api/benchmark-history", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelName }),
      });
      
      const result = await resp.json();
      
      if (!resp.ok) {
        throw new Error(result.error || "Failed to load benchmark history");
      }
      
      // Show the results modal with cached data
      showBenchmarkResultsModal(result);
      
    } catch (err) {
      console.error("Failed to load benchmark history", err);
      alert(`Failed to load benchmark history: ${err.message}`);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "[History] View Historical Benchmark Results";
      }
    }
  }

  // Deep sweep using llama-batched-bench native sweep capability
  async function runDeepSweep(modelName, modelSafeId, content) {
    const btn = document.getElementById(`deep-sweep-${modelSafeId}`);
    const statusEl = document.getElementById(`optimize-status-${modelSafeId}`);
    const progressContainer = document.getElementById(`optimize-progress-${modelSafeId}`);
    const progressText = document.getElementById(`optimize-progress-text-${modelSafeId}`);
    const progressBarContainer = document.getElementById(`optimize-progress-bar-container-${modelSafeId}`);
    const progressBar = document.getElementById(`optimize-progress-bar-${modelSafeId}`);
    const detailsEl = document.getElementById(`optimize-details-${modelSafeId}`);
    const cancelBtn = document.getElementById(`optimize-cancel-${modelSafeId}`);
    const maxDurationInput = document.getElementById(`max-duration-${modelSafeId}`);
    const targetSelect = document.getElementById(`optimize-target-${modelSafeId}`);

    if (btn) {
      btn.disabled = true;
      btn.textContent = "[Deep Sweep] Deep Sweep Running…";
    }

    if (statusEl) {
      statusEl.textContent = "";
      statusEl.style.color = "var(--muted)";
    }

    // Show progress UI
    progressContainer.style.display = "block";
    progressBarContainer.style.display = "block";
    detailsEl.style.display = "block";
    progressText.textContent = "Initializing deep sweep...";
    progressBar.style.width = "0%";
    detailsEl.textContent = "";

    // Create abort controller for cancellation
    const abortController = new AbortController();
    let isCancelled = false;

    // Setup cancel button
    if (cancelBtn) {
      cancelBtn.onclick = async () => {
        isCancelled = true;
        progressText.textContent = "Cancelling...";
        cancelBtn.disabled = true;
        cancelBtn.textContent = "Cancelling...";
        
        // Signal cancellation to the server
        try {
          const resp = await fetch("/api/benchmark-cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model: modelName }),
          });
          if (resp.ok) {
            const data = await resp.json();
            if (data.success) {
              log("Benchmark cancelled on server");
            } else {
              log("Failed to cancel benchmark on server: " + data.message);
            }
          } else {
            log("Failed to cancel benchmark: " + (await resp.text()));
          }
        } catch (err) {
          log("Error cancelling benchmark: " + err);
        }
      };
    }

    let progressInterval = null;
    const startTime = Date.now();

    try {
      // Build request body
      const requestBody = {
        model: modelName,
        max_duration_sec: parseInt(maxDurationInput.value, 10) || 0,
        target: targetSelect.value || "throughput",
      };

      const fetchPromise = fetch("/api/benchmark-deep-sweep", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
        signal: abortController.signal,
      });

      // Progress polling for deep sweep
      let progressIntervalMs = 2000;
      let lastCompleted = 0;

      progressInterval = setInterval(() => {
        if (isCancelled) return;
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        progressText.textContent = `Running deep sweep... (${elapsed}s elapsed)`;
        detailsEl.textContent = "Each run tests 27 combinations (npp × ntg × npl) via llama-batched-bench native sweep";
        // Estimate progress - deep sweep takes ~2-5 min per run
        // We can't know exact progress without server-side reporting
      }, progressIntervalMs);

      const response = await fetchPromise;

      if (progressInterval) {
        clearInterval(progressInterval);
      }

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.error || "Deep sweep failed");
      }

      const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
      progressBar.style.width = "100%";
      progressText.textContent = "Deep sweep complete!";
      detailsEl.textContent = `Completed ${result.completed_runs}/${result.total_runs} runs in ${elapsedSec}s`;

      // Build status message
      const bestConfig = result.best_config || {};
      const bestScore = result.best_score || 0;
      const throughputStr = bestScore > 0 ? `Best throughput: ${bestScore.toFixed(1)} tok/s` : "";
      const configStr = bestConfig ? 
        ` (ctx=${bestConfig.ctx_size}, batch=${bestConfig.batch_size}, threads=${bestConfig.threads}, cache=${bestConfig.cache_type_k})` : "";

      let statusMsg = `Deep sweep complete! ${throughputStr}${configStr}. ` +
        `${result.completed_runs}/${result.total_runs} successful. Duration: ${elapsedSec}s.`;

      if (statusEl) {
        statusEl.textContent = statusMsg;
        statusEl.style.color = result.failed_runs > 0 ? "var(--orange)" : "var(--green)";
      }

      // Show summary with option to view details
      const viewDetails = confirm(
        `${statusMsg}\n\nClick OK to view detailed sweep results, or Cancel to continue.`
      );
      if (viewDetails) {
        showDeepSweepResultsModal(result);
      }

    } catch (err) {
      if (progressInterval) {
        clearInterval(progressInterval);
      }

      console.error("Deep sweep failed", err);

      if (err.name === "AbortError" || isCancelled) {
        if (statusEl) {
          statusEl.textContent = "Deep sweep cancelled by user";
          statusEl.style.color = "var(--orange)";
        }
        progressText.textContent = "Cancelled";
        detailsEl.textContent = "";
      } else {
        if (statusEl) {
          statusEl.textContent = `Error: ${err.message}`;
          statusEl.style.color = "var(--red)";
        }
        progressText.textContent = `Error: ${err.message}`;
        detailsEl.textContent = "";
        alert(`Deep sweep failed: ${err.message}`);
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "[Deep Sweep] Deep Sweep (Batched Bench)";
      }
      progressContainer.style.display = "none";
      cancelBtn.disabled = false;
      cancelBtn.textContent = "Cancel";
    }
  }

  // Modal to display deep sweep results
  async function showDeepSweepResultsModal(result) {
    // Remove existing modal if any
    const existingModal = document.getElementById("deep-sweep-results-modal");
    if (existingModal) {
      existingModal.remove();
    }

    // Create the modal element
    const modal = document.createElement("div");
    modal.id = "deep-sweep-results-modal";
    modal.style.cssText = "position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 10000;";
    document.body.appendChild(modal);

    const model = result.model || "Unknown";
    const target = result.optimization_target || "balanced";
    
    // Fetch persisted results from the benchmark history API
    try {
      const fetchPromise = fetch(`/api/benchmark-history`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      const historyResponse = await fetchPromise;
      const history = await historyResponse.json();
      
      if (history.error) {
        console.error("Failed to fetch deep sweep history for model", model, history.error);
        // Fall back to using the transient result if history fetch fails
        const allResults = result.all_results || [];
        const successfulResults = allResults.filter(r => r.success);
        const failedResults = allResults.filter(r => !r.success);
        // Sort by batched_throughput descending
        successfulResults.sort((a, b) => (b.batched_throughput || 0) - (a.batched_throughput || 0));
        
        const resultsHtml = successfulResults.length > 0 ? 
          `<table style="width: 100%; border-collapse: collapse; font-size: 0.75rem; margin-top: 1rem;">
            <thead>
              <tr style="background: var(--surface2);">
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Rank</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Throughput (tok/s)</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">PP Avg</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">TG Avg</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Latency P50 (ms)</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Ctx Size</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Batch</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Threads</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Cache</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Parallel Sweep</th>
              </tr>
            </thead>
            <tbody>
              ${successfulResults.map((r, idx) => {
                const c = r.config || {};
                const pr = r.parallel_results || [];
                const sweepInfo = pr.length > 0 ? 
                  pr.map(p => `pl=${p.parallel}:${p.throughput?.toFixed(0) || 0}`).join(", ") :
                  "N/A";
                const isBest = idx === 0;
                const rowStyle = isBest ? "background: rgba(0,200,0,0.1); font-weight: bold;" : "";
                return `
                <tr style="${rowStyle}">
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${isBest ? "🥇 1" : idx + 1}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${(r.batched_throughput || 0).toFixed(1)}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${(r.pp_avg || 0).toFixed(1)}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${(r.tg_avg || 0).toFixed(1)}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${r.latency_p50 ? r.latency_p50.toFixed(1) : "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.ctx_size || "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.batch_size || "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.threads || "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.cache_type_k || "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${sweepInfo}</td>
                </tr>`;
              }).join("")}
            </tbody>
          </table>` : "<p>No successful results.</p>";

        const failedHtml = failedResults.length > 0 ? 
          `<div style="margin-top: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px;">
            <strong>Failed runs (${failedResults.length}):</strong>
            <ul style="margin: 0.5rem 0; font-size: 0.8rem;">
              ${failedResults.map(r => `<li>${r.config?.ctx_size || "?"}ctx/${r.config?.batch_size || "?"}b/${r.config?.threads || "?"}t/${r.config?.cache_type_k || "?"} - ${r.error || "Unknown error"}</li>`).join("")}
            </ul>
          </div>` : "";

        modal.innerHTML = `
          <div style="background: var(--surface); border-radius: 8px; padding: 1.5rem; max-width: 1100px; max-height: 85vh; overflow: auto; width: 95%;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
              <h2 style="margin: 0;">Deep Sweep Results: ${model}</h2>
              <button onclick="this.closest('#deep-sweep-results-modal').remove()" style="background: none; border: none; font-size: 1.5rem; cursor: pointer; color: var(--muted);">[x]</button>
            </div>
            <div style="margin-bottom: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px; font-size: 0.85rem;">
              <strong>Model max context:</strong> ${result.model_max_context ? (result.model_max_context/1024).toFixed(0) + "k" : "N/A"} |
              <strong>Target:</strong> ${target} |
              <strong>Total runs:</strong> ${result.total_runs} | 
              <strong>Successful:</strong> ${result.completed_runs} | 
              <strong>Failed:</strong> ${result.failed_runs} | 
              <strong>Elapsed:</strong> ${result.elapsed_sec ? result.elapsed_sec.toFixed(1) + "s" : "N/A"}
            </div>
            <div style="margin-bottom: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px; font-size: 0.85rem;">
              <strong>Best configuration:</strong>
              <pre style="margin: 0.5rem 0; font-size: 0.75rem;">${JSON.stringify(result.best_config || {}, null, 2)}</pre>
            </div>
            ${resultsHtml}
            ${failedHtml}
          </div>
        `;
      } else {
        // Use persisted results from cache
        const optimalSettings = history.optimal_settings || {};
        const benchmarkResults = history.benchmark_results || [];
        const hardwareInfo = history.hardware_info || {};
        const optimizationTarget = history.optimization_target || target;
        
        // Sort benchmark results by batched_throughput descending
        benchmarkResults.sort((a, b) => (b.batched_throughput || 0) - (a.batched_throughput || 0));
        
        const allResults = benchmarkResults;
        const successfulResults = allResults.filter(r => r.success);
        const failedResults = allResults.filter(r => !r.success);

        const resultsHtml = successfulResults.length > 0 ? 
          `<table style="width: 100%; border-collapse: collapse; font-size: 0.75rem; margin-top: 1rem;">
            <thead>
              <tr style="background: var(--surface2);">
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Rank</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Throughput (tok/s)</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">PP Avg</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">TG Avg</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Latency P50 (ms)</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Ctx Size</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Batch</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Threads</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Cache</th>
                <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Parallel Sweep</th>
              </tr>
            </thead>
            <tbody>
              ${successfulResults.map((r, idx) => {
                const c = r.config || {};
                const pr = r.parallel_results || [];
                const sweepInfo = pr.length > 0 ? 
                  pr.map(p => `pl=${p.parallel}:${p.throughput?.toFixed(0) || 0}`).join(", ") :
                  "N/A";
                const isBest = idx === 0;
                const rowStyle = isBest ? "background: rgba(0,200,0,0.1); font-weight: bold;" : "";
                return `
                <tr style="${rowStyle}">
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${isBest ? "🥇 1" : idx + 1}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${(r.batched_throughput || 0).toFixed(1)}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${(r.pp_avg || 0).toFixed(1)}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${(r.tg_avg || 0).toFixed(1)}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${r.latency_p50 ? r.latency_p50.toFixed(1) : "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.ctx_size || "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.batch_size || "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.threads || "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.cache_type_k || "N/A"}</td>
                  <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${sweepInfo}</td>
                </tr>`;
              }).join("")}
            </tbody>
          </table>` : "<p>No successful results.</p>";

        const failedHtml = failedResults.length > 0 ? 
          `<div style="margin-top: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px;">
            <strong>Failed runs (${failedResults.length}):</strong>
            <ul style="margin: 0.5rem 0; font-size: 0.8rem;">
              ${failedResults.map(r => `<li>${r.config?.ctx_size || "?"}ctx/${r.config?.batch_size || "?"}b/${r.config?.threads || "?"}t/${r.config?.cache_type_k || "?"} - ${r.error || "Unknown error"}</li>`).join("")}
            </ul>
          </div>` : "";

        modal.innerHTML = `
          <div style="background: var(--surface); border-radius: 8px; padding: 1.5rem; max-width: 1100px; max-height: 85vh; overflow: auto; width: 95%;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
              <h2 style="margin: 0;">Deep Sweep Results: ${model}</h2>
              <button onclick="this.closest('#deep-sweep-results-modal').remove()" style="background: none; border: none; font-size: 1.5rem; cursor: pointer; color: var(--muted);">[x]</button>
            </div>
            <div style="margin-bottom: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px; font-size: 0.85rem;">
              <strong>Model max context:</strong> ${result.model_max_context ? (result.model_max_context/1024).toFixed(0) + "k" : "N/A"} |
              <strong>Target:</strong> ${optimizationTarget} |
              <strong>Total runs:</strong> ${benchmarkResults.length} | 
              <strong>Successful:</strong> ${successfulResults.length} | 
              <strong>Failed:</strong> ${failedResults.length} | 
              <strong>Elapsed:</strong> ${history.elapsed_sec ? history.elapsed_sec.toFixed(1) + "s" : "N/A"}
            </div>
            <div style="margin-bottom: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px; font-size: 0.85rem;">
              <strong>Best configuration:</strong>
              <pre style="margin: 0.5rem 0; font-size: 0.75rem;">${JSON.stringify(optimalSettings, null, 2)}</pre>
            </div>
            ${resultsHtml}
            ${failedHtml}
          </div>
        `;
      }
    } catch (err) {
      console.error("Error fetching deep sweep history", err);
      // Fall back to using the transient result if history fetch fails
      const allResults = result.all_results || [];
      const successfulResults = allResults.filter(r => r.success);
      const failedResults = allResults.filter(r => !r.success);
      // Sort by batched_throughput descending
      successfulResults.sort((a, b) => (b.batched_throughput || 0) - (a.batched_throughput || 0));
      
      const resultsHtml = successfulResults.length > 0 ? 
        `<table style="width: 100%; border-collapse: collapse; font-size: 0.75rem; margin-top: 1rem;">
          <thead>
            <tr style="background: var(--surface2);">
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Rank</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Throughput (tok/s)</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">PP Avg</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">TG Avg</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Latency P50 (ms)</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Ctx Size</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Batch</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Threads</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Cache</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Parallel Sweep</th>
            </tr>
          </thead>
          <tbody>
            ${successfulResults.map((r, idx) => {
              const c = r.config || {};
              const pr = r.parallel_results || [];
              const sweepInfo = pr.length > 0 ? 
                pr.map(p => `pl=${p.parallel}:${p.throughput?.toFixed(0) || 0}`).join(", ") :
                "N/A";
              const isBest = idx === 0;
              const rowStyle = isBest ? "background: rgba(0,200,0,0.1); font-weight: bold;" : "";
              return `
              <tr style="${rowStyle}">
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${isBest ? "🥇 1" : idx + 1}</td>
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${(r.batched_throughput || 0).toFixed(1)}</td>
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${(r.pp_avg || 0).toFixed(1)}</td>
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${(r.tg_avg || 0).toFixed(1)}</td>
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${r.latency_p50 ? r.latency_p50.toFixed(1) : "N/A"}</td>
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.ctx_size || "N/A"}</td>
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.batch_size || "N/A"}</td>
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.threads || "N/A"}</td>
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${c.cache_type_k || "N/A"}</td>
                <td style="padding: 0.4rem; border-bottom: 1px solid var(--border);">${sweepInfo}</td>
              </tr>`;
            }).join("")}
          </tbody>
        </table>` : "<p>No successful results.</p>";

      const failedHtml = failedResults.length > 0 ? 
        `<div style="margin-top: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px;">
          <strong>Failed runs (${failedResults.length}):</strong>
          <ul style="margin: 0.5rem 0; font-size: 0.8rem;">
            ${failedResults.map(r => `<li>${r.config?.ctx_size || "?"}ctx/${r.config?.batch_size || "?"}b/${r.config?.threads || "?"}t/${r.config?.cache_type_k || "?"} - ${r.error || "Unknown error"}</li>`).join("")}
          </ul>
        </div>` : "";

      modal.innerHTML = `
        <div style="background: var(--surface); border-radius: 8px; padding: 1.5rem; max-width: 1100px; max-height: 85vh; overflow: auto; width: 95%;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
            <h2 style="margin: 0;">Deep Sweep Results: ${model}</h2>
            <button onclick="this.closest('#deep-sweep-results-modal').remove()" style="background: none; border: none; font-size: 1.5rem; cursor: pointer; color: var(--muted);">[x]</button>
          </div>
          <div style="margin-bottom: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px; font-size: 0.85rem;">
            <strong>Model max context:</strong> ${result.model_max_context ? (result.model_max_context/1024).toFixed(0) + "k" : "N/A"} |
            <strong>Target:</strong> ${target} |
            <strong>Total runs:</strong> ${result.total_runs} | 
            <strong>Successful:</strong> ${result.completed_runs} | 
            <strong>Failed:</strong> ${result.failed_runs} | 
            <strong>Elapsed:</strong> ${result.elapsed_sec ? result.elapsed_sec.toFixed(1) + "s" : "N/A"}
          </div>
          <div style="margin-bottom: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px; font-size: 0.85rem;">
            <strong>Best configuration:</strong>
            <pre style="margin: 0.5rem 0; font-size: 0.75rem;">${JSON.stringify(result.best_config || {}, null, 2)}</pre>
          </div>
          ${resultsHtml}
          ${failedHtml}
        </div>
      `;
    }
    
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        modal.remove();
      }
    });

    // Close on Escape key
    const handleEscape = (e) => {
      if (e.key === "Escape") {
        modal.remove();
        document.removeEventListener("keydown", handleEscape);
      }
    };
    document.addEventListener("keydown", handleEscape);
  }

  // Modal to display benchmark results
  function showBenchmarkResultsModal(result) {
    // Remove existing modal if any
    const existingModal = document.getElementById("benchmark-results-modal");
    if (existingModal) {
      existingModal.remove();
    }

    const modal = document.createElement("div");
    modal.id = "benchmark-results-modal";
    modal.style.cssText = `
      position: fixed; top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0,0,0,0.5); z-index: 10000;
      display: flex; align-items: center; justify-content: center;
    `;

    const hardware = result.hardware_info || result.hardware;
    const hwInfo = hardware ? `
      <div style="margin-bottom: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px; font-size: 0.85rem;">
        <strong>Hardware:</strong> ${hardware.cpu_count_logical} CPUs, ${hardware.memory_total_gb}GB RAM
        ${hardware.total_gpu_vram_gb ? `, ${hardware.total_gpu_vram_gb}GB GPU VRAM` : ""}
        ${hardware.has_nvidia_gpu ? " (NVIDIA)" : ""}${hardware.has_amd_gpu ? " (AMD)" : ""}
      </div>
    ` : "";

const results = result.benchmark_results || [];
    const cacheKey = result.cache_key || "N/A";
    const resultsHtml = results.length > 0 ? `
      <table style="width: 100%; border-collapse: collapse; font-size: 0.75rem;">
        <thead>
          <tr style="background: var(--surface2);">
            <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border);">Config</th>
            <th style="padding: 0.5rem; text-align: center; border-bottom: 1px solid var(--border);">Type</th>
            <th style="padding: 0.5rem; text-align: center; border-bottom: 1px solid var(--border);">Date/Time</th>
            <th style="padding: 0.5rem; text-align: center; border-bottom: 1px solid var(--border);">Target</th>
            <th style="padding: 0.5rem; text-align: right; border-bottom: 1px solid var(--border);">Tokens/s</th>
            <th style="padding: 0.5rem; text-align: right; border-bottom: 1px solid var(--border);">Latency (ms)</th>
            <th style="padding: 0.5rem; text-align: center; border-bottom: 1px solid var(--border);">Status</th>
          </tr>
        </thead>
        <tbody>
          ${results.map((r, idx) => {
            // Filter config to only show relevant benchmark parameters
            const benchmarkParamKeys = new Set([
              "n_gpu_layers", "gpu_layers", "batch_size", "ubatch_size", 
              "threads", "threads_batch", "cache_type_k", "cache_type_v", 
              "flash_attn", "ctx_size", "parallel", "numa", "numa_value",
              "cpu_affinity", "cpu_strict", "spec_type", "spec_draft_n_max",
              "cache_reuse", "cache_ram", "swa_full"
            ]);
            const filteredConfig = r.config ? Object.entries(r.config)
              .filter(([k]) => benchmarkParamKeys.has(k))
              .map(([k,v]) => `${k}=${v}`)
              .join(', ') : 'N/A';
            
            // Format timestamp
            let dateStr = 'N/A';
            if (r.timestamp) {
              const date = new Date(r.timestamp * 1000);
              dateStr = date.toLocaleString();
            }
            
            // Test type
            const testType = r.test_type || (r.optimization_target === 'current_config_test' ? 'custom' : 'automatic');
            const typeBadge = testType === 'custom' 
              ? '<span style="background: var(--blue); color: white; padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.7rem;">Custom</span>'
              : '<span style="background: var(--green); color: white; padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.7rem;">Auto</span>';
            
            // Optimization target
            const target = r.optimization_target || result.optimization_target || 'balanced';
            
            return `
            <tr style="border-bottom: 1px solid var(--border); ${r.success ? '' : 'opacity: 0.6;'}">
              <td style="padding: 0.5rem; font-family: monospace; font-size: 0.7rem;">${filteredConfig}</td>
              <td style="padding: 0.5rem; text-align: center;">${typeBadge}</td>
              <td style="padding: 0.5rem; text-align: center; font-size: 0.7rem;">${dateStr}</td>
              <td style="padding: 0.5rem; text-align: center; text-transform: capitalize;">${target}</td>
              <td style="padding: 0.5rem; text-align: right; ${r.success && r.tg_avg === Math.max(...results.filter(x => x.success).map(x => x.tg_avg)) ? 'font-weight: bold; color: var(--green);' : ''}">
                ${r.success && r.tg_avg ? r.tg_avg.toFixed(1) : 'N/A'}
              </td>
              <td style="padding: 0.5rem; text-align: right;">
                ${r.success && r.tg_avg ? (1000 / r.tg_avg).toFixed(1) : 'N/A'}
              </td>
              <td style="padding: 0.5rem; text-align: center;">
                ${r.success ? '<span style="color: var(--green);">✓</span>' : '<span style="color: var(--red);">✗</span>'}
              </td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    ` : '<p style="color: var(--muted);">No benchmark results available</p>';

    modal.innerHTML = `
      <div style="background: var(--surface); border-radius: 8px; padding: 1.5rem; max-width: 1000px; max-height: 80vh; overflow: auto; width: 95%; box-shadow: 0 4px 20px rgba(0,0,0,0.3);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
          <h2 style="margin: 0;">Benchmark Results</h2>
          <button id="close-benchmark-modal" style="background: none; border: none; font-size: 1.5rem; cursor: pointer; color: var(--muted);">&times;</button>
        </div>
        <div style="margin-bottom: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 4px; font-size: 0.85rem;">
          <strong>Model:</strong> ${result.model} | 
          <strong>Target:</strong> ${result.optimization_target} | 
          <strong>Duration:</strong> ${result.duration_sec}s | 
          <strong>Cached:</strong> ${result.cached ? 'Yes' : 'No'}
          ${result.fallback_reason ? `<br><strong style="color: var(--orange);">Fallback: ${result.fallback_reason}</strong>` : ''}
        </div>
        <div style="margin-bottom: 1rem; padding: 0.5rem; background: var(--surface3); border-radius: 4px; font-size: 0.75rem; font-family: monospace; word-break: break-all;">
          <strong>Cache Key:</strong> ${cacheKey}
        </div>
        ${hwInfo}
        <div style="margin-bottom: 1rem;">
          <strong>Optimal Settings Applied:</strong>
          <pre style="background: var(--surface2); padding: 0.75rem; border-radius: 4px; font-size: 0.75rem; overflow: auto; max-height: 200px; margin-top: 0.5rem;">${JSON.stringify(result.optimal_settings, null, 2)}</pre>
        </div>
        <div>
          <strong>All Benchmark Results (${results.length} configurations tested):</strong>
          ${resultsHtml}
        </div>
      </div>
    `;

    document.body.appendChild(modal);
    
    // Close handlers
    modal.querySelector("#close-benchmark-modal").onclick = () => modal.remove();
    modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
    
    // ESC key to close
    const escHandler = (e) => { if (e.key === "Escape") { modal.remove(); document.removeEventListener("keydown", escHandler); }};
    document.addEventListener("keydown", escHandler);
  }

  // Attach start model button listener
  const startBtn = document.getElementById(`start-model-${modelSafeId}`);
  if (startBtn) {
    startBtn.addEventListener("click", () => {
      act(modelName, 'start');
    });
  }

  // Attach stop model button listener
  const stopBtn = document.getElementById(`stop-model-${modelSafeId}`);
  if (stopBtn) {
    stopBtn.addEventListener("click", () => {
      if (!confirm(`Stop model "${modelName}"?`)) return;
      act(modelName, 'stop');
    });
  }

  // Auto-search for HF repo if not configured and not already attempted
  if (!data.repo && !autoSearchAttempted.has(modelName)) {
    autoSearchAttempted.add(modelName);
    const statusEl = document.getElementById(`hf-search-status-${modelSafeId}`);
    if (statusEl) {
      statusEl.textContent = "Automatically searching Hugging Face...";
      statusEl.style.display = "block";
    }
    autoSearchAndSelectRepo(modelName);
  }

  // If no repo after attempting auto-search, skip metadata fetch
  if (!data.repo) {
    return;
  }

  // Fetch HF metadata asynchronously
  try {
    const resp = await fetch(
      `/api/hf-metadata?model=${encodeURIComponent(modelName)}`,
    );
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const msg = err.error || "Failed to load Hugging Face metadata";
      document.getElementById("hf-metadata-error").textContent = msg;
      document.getElementById("hf-metadata-error").style.display = "block";
      document.getElementById("hf-metadata-loading").style.display = "none";
      return;
    }
    const meta = await resp.json();
    document.getElementById("hf-metadata-loading").style.display = "none";

    let metaHtml = '<div class="panel-grid">';

    if (meta.downloads !== undefined) {
      metaHtml += `<div class="panel-item"><span class="label">Downloads</span><span class="value">${he(meta.downloads.toLocaleString())}</span></div>`;
    }
    if (meta.likes !== undefined) {
      metaHtml += `<div class="panel-item"><span class="label">Likes</span><span class="value">${he(meta.likes.toLocaleString())}</span></div>`;
    }
    if (meta.author) {
      metaHtml += `<div class="panel-item"><span class="label">Author</span><span class="value">${he(meta.author)}</span></div>`;
    }
    if (meta.lastModified) {
      const date = new Date(meta.lastModified).toLocaleDateString();
      metaHtml += `<div class="panel-item"><span class="label">Updated</span><span class="value">${he(date)}</span></div>`;
    }
    if (Array.isArray(meta.tags) && meta.tags.length > 0) {
      metaHtml += `<div class="panel-item full-width"><span class="label">Tags</span><span class="value">`;
      meta.tags.slice(0, 8).forEach((tag) => {
        metaHtml += `<span class="tag">${he(tag)}</span>`;
      });
      if (meta.tags.length > 8) {
        metaHtml += `<span class="tag">+${meta.tags.length - 8} more</span>`;
      }
      metaHtml += `</span></div>`;
    }
    if (meta.pipeline_tag) {
      metaHtml += `<div class="panel-item"><span class="label">Type</span><span class="value">${he(meta.pipeline_tag)}</span></div>`;
    }
    if (meta.description) {
      const maxLen = 200;
      const desc =
        meta.description.length > maxLen
          ? meta.description.substring(0, maxLen) + "..."
          : meta.description;
      metaHtml += `<div class="panel-item full-width"><span class="label">Description</span><span class="value" style="white-space: pre-wrap;">${he(desc)}</span></div>`;
    }
    if (meta.repo) {
      const repoUrl = `https://huggingface.co/${meta.repo}`;
      metaHtml += `<div class="panel-item full-width"><a href="${he(repoUrl)}" target="_blank" rel="noopener">View on Hugging Face ↗</a></div>`;
    }

    metaHtml += `</div>`;
    document.getElementById("hf-metadata-content").innerHTML = metaHtml;
    document.getElementById("hf-metadata-content").style.display = "block";
  } catch (e) {
    document.getElementById("hf-metadata-loading").style.display = "none";
    document.getElementById("hf-metadata-error").textContent =
      "Network error while fetching metadata";
    document.getElementById("hf-metadata-error").style.display = "block";
  }
}

// Tooltip element for metrics
let metricsTooltip = null;
let metricsTooltipInterval = null;
let metricsTooltipContext = null; // { modelName, port, tile }

/**
 * Show metrics tooltip for a running model tile
 * @param {HTMLElement} tile - The tile element
 * @param {string} modelName - Model name
 * @param {number} port - Model port
 */
async function showMetricsTooltip(tile, modelName, port) {
  console.log('showMetricsTooltip called for', modelName, 'port', port);
  console.log('window.fetchModelMetrics:', typeof window.fetchModelMetrics);
  console.log('window.formatMetricsTooltip:', typeof window.formatMetricsTooltip);
  
  // Remove any existing tooltip
  hideMetricsTooltip();
  
  // Store context for auto-refresh
  metricsTooltipContext = { modelName, port, tile };
  
  // Show loading state
  const tooltip = document.createElement('div');
  tooltip.className = 'metrics-tooltip-popup';
  tooltip.innerHTML = '<div class="metrics-tooltip">Loading metrics...</div>';
  document.body.appendChild(tooltip);
  metricsTooltip = tooltip;
  
  // Position tooltip near the tile - getBoundingClientRect is viewport-relative
  // For position: fixed, use viewport coordinates directly (no scrollY)
  const rect = tile.getBoundingClientRect();
  tooltip.style.left = rect.left + 'px';
  tooltip.style.top = (rect.bottom + 8) + 'px';
  
  // Fetch and update metrics
  await updateTooltipContent(tooltip, modelName, port);
  
  // Start auto-refresh interval (every 3 seconds)
  if (metricsTooltipInterval) {
    clearInterval(metricsTooltipInterval);
  }
  metricsTooltipInterval = setInterval(async () => {
    if (!metricsTooltip || !metricsTooltipContext) return;

    // Stop polling if the model is no longer present/running (e.g. removed from
    // models.json or stopped). Otherwise we keep hammering a dead port and spam
    // the console with net::ERR_CONNECTION_REFUSED.
    const stillRunning = currentModelRows?.some(
      (r) => r.port === metricsTooltipContext.port && (r.status || "").startsWith("running")
    );
    if (!stillRunning) {
      hideMetricsTooltip();
      return;
    }

    await updateTooltipContent(
      metricsTooltip,
      metricsTooltipContext.modelName,
      metricsTooltipContext.port,
      true
    );
  }, 3000);
}

/**
 * Update tooltip content with fresh metrics
 * @param {HTMLElement} tooltip - Tooltip element
 * @param {string} modelName - Model name
 * @param {number} port - Model port
 * @param {boolean} bypassCache - If true, fetch fresh data bypassing cache
 */
async function updateTooltipContent(tooltip, modelName, port, bypassCache = false) {
  if (window.fetchModelMetrics) {
    const metrics = await window.fetchModelMetrics(modelName, port, bypassCache);
    if (metricsTooltip === tooltip) {
      tooltip.innerHTML = window.formatMetricsTooltip ? 
        window.formatMetricsTooltip(metrics) : 
        '<div class="metrics-tooltip">No metrics available</div>';
      
      // Reposition if needed (in case content changed size)
      const tooltipRect = tooltip.getBoundingClientRect();
      if (tooltipRect.right > window.innerWidth - 10) {
        tooltip.style.left = (window.innerWidth - tooltipRect.width - 10) + 'px';
      }
      if (tooltipRect.bottom > window.innerHeight - 10) {
        // Move above the tile
        const rect = metricsTooltipContext?.tile?.getBoundingClientRect();
        if (rect) {
          tooltip.style.top = (rect.top - tooltipRect.height - 8) + 'px';
        }
      }
    }
  } else {
    console.error('fetchModelMetrics not available!');
    tooltip.innerHTML = '<div class="metrics-tooltip">Metrics function not loaded</div>';
  }
}

/**
 * Hide metrics tooltip
 */
function hideMetricsTooltip(tile = null) {
  if (metricsTooltip) {
    metricsTooltip.remove();
    metricsTooltip = null;
  }
  if (metricsTooltipInterval) {
    clearInterval(metricsTooltipInterval);
    metricsTooltipInterval = null;
  }
  metricsTooltipContext = null;
}

// Expose functions globally for use in model-manager.js
window.showMetricsTooltip = showMetricsTooltip;
window.hideMetricsTooltip = hideMetricsTooltip;

/**
 * Export model metrics to CSV (background operation)
 * @param {string} modelName - Model name
 * @param {number} port - Model port
 */
async function exportMetrics(modelName, port) {
  try {
    const response = await fetch('/api/export-metrics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelName })
    });
    const data = await response.json();
    if (data.error) {
      console.error('Export metrics error:', data.error);
    } else {
      console.log('Metrics exported:', data.csv_path);
    }
  } catch (e) {
    console.error('Error exporting metrics:', e);
  }
}
window.exportMetrics = exportMetrics;

// Expose functions globally for use in model-manager.js
window.showMetricsTooltip = showMetricsTooltip;
window.hideMetricsTooltip = hideMetricsTooltip;
window.exportMetrics = exportMetrics;
window.renderTiles = renderTiles;
window.updateTilePrefs = updateTilePrefs;

/**
 * Populate the performance graph model dropdown with current models.
 * Running models appear first (sorted alphabetically), then stopped models (sorted alphabetically).
 * @param {HTMLSelectElement} modelSelect - The dropdown element
 * @param {string} [preSelectModel] - Optional model name to pre-select
 */
function populatePerformanceModelDropdown(modelSelect, preSelectModel) {
  if (!modelSelect) return;

  modelSelect.innerHTML = '<option value="">Select a model</option>';

  const modelRows = currentModelRows || [];
  const models = modelRows.filter(r => !r.speculative_decoding_model);

  const runningModels = models.filter(r => r.status && r.status.startsWith("running"));
  const nonRunningModels = models.filter(r => !r.status || !r.status.startsWith("running"));

  // Sort alphabetically within each group
  const sortByName = (a, b) => normalizeModelName(a.model).localeCompare(normalizeModelName(b.model));
  runningModels.sort(sortByName);
  nonRunningModels.sort(sortByName);

  const sortedModels = [...runningModels, ...nonRunningModels];

  const optionsHtml = sortedModels.map(r => {
    const isRunning = r.status && r.status.startsWith("running");
    const indicator = isRunning ? ' ● running' : ' ● stopped';
    const value = r.model;
    return `<option value="${he(String(value))}">${he(String(r.model))}${indicator}</option>`;
  }).join('');

  modelSelect.innerHTML += optionsHtml;

  // Pre-select model if provided
  if (preSelectModel) {
    const matchingOption = modelSelect.querySelector(`option[value="${he(String(preSelectModel))}"]`);
    if (matchingOption) {
      matchingOption.selected = true;
    }
  }
}

/**
 * Show the performance graph modal for a specific model (from tile click)
 * @param {string} modelName - Model name to show
 * @param {number} port - Model port
 * @param {Event} event - Click event for button state
 */
async function openPerformanceGraphModal(modelName, port, event) {
  try {
    if (event && event.target) {
      const btn = event.target;
      const originalText = btn.textContent;
      btn.disabled = true;
      btn.textContent = '📈 Loading...';
    }

    const modelSelect = document.getElementById('model-select');
    populatePerformanceModelDropdown(modelSelect, modelName);

    // Re-register cleanup callback for each open/close cycle
    // (ModalManager deletes the callback after close)
    if (typeof window.cleanupPerformanceGraph === 'function') {
      ModalManager.register('performance-graph-modal', window.cleanupPerformanceGraph);
    }

    // Open the performance graph modal via ModalManager FIRST
    // This ensures the canvas is visible when Chart.js initializes
    ModalManager.open('performance-graph-modal');

    // Start auto-refresh for the performance graph (60-second interval)
    if (typeof window.startPerformanceGraphAutoRefresh === 'function') {
      window.startPerformanceGraphAutoRefresh();
    }

    // NOW load metrics - canvas is visible so Chart.js can size correctly
    if (modelSelect && typeof loadModelMetrics === 'function') {
      loadModelMetrics({ target: modelSelect });
    }

    if (event && event.target) {
      const btn = event.target;
      btn.disabled = false;
      btn.textContent = '📈 Performance';
    }
  } catch (e) {
    console.error('Error opening performance graph:', e);
    if (event && event.target) {
      const btn = event.target;
      btn.disabled = false;
      btn.textContent = '📈 Performance';
    }
  }
}
window.openPerformanceGraphModal = openPerformanceGraphModal;

/**
 * Show the performance graph modal and prompt for model selection (from toolbar)
 */
function showPerformanceModal() {
  const modelSelect = document.getElementById('model-select');
  const modal = document.getElementById('performance-graph-modal');
  
  // If modelSelect doesn't exist (modal hasn't initialized yet), show an error
  if (!modelSelect) {
    alert('Performance graph modal not initialized. Please refresh the page.');
    return;
  }
  
  // Populate dropdown without pre-selection
  populatePerformanceModelDropdown(modelSelect);
  
// Show the modal
  ModalManager.open('performance-graph-modal');

  // Start auto-refresh for the performance graph (60-second interval)
  if (typeof window.startPerformanceGraphAutoRefresh === 'function') {
    window.startPerformanceGraphAutoRefresh();
  }

  // Add event listener for Open Archive button
  const openArchiveBtn = document.getElementById('open-archive-from-perf-btn');
  if (openArchiveBtn) {
    // Remove any existing listener
    openArchiveBtn.replaceWith(openArchiveBtn.cloneNode(true));
    const newBtn = document.getElementById('open-archive-from-perf-btn');
    if (newBtn) {
      newBtn.addEventListener('click', () => {
        const selectedModel = modelSelect.value;
        if (selectedModel) {
          ModalManager.close('performance-graph-modal');
          // Small delay to let the first modal close
          setTimeout(() => {
            if (window.ArchiveBrowser) {
              window.ArchiveBrowser.open(selectedModel);
            }
          }, 100);
        } else {
          alert('Please select a model first');
        }
      });
    }
  }

  // Wait for modal to be visible, then register cleanup
  setTimeout(() => {
    // Register cleanup for this modal
    if (typeof window.cleanupPerformanceGraph === 'function') {
      ModalManager.register('performance-graph-modal', window.cleanupPerformanceGraph);
    }
  }, 100);
}

window.showPerformanceModal = showPerformanceModal;

/**
 * Start/stop the mnemonic-proxy (and its MCP server) for a model.
 * These controls operate only on the mnemonic-proxy + MCP layer and never
 * touch the running model process, so they are independent of the tile's own
 * Start/Stop button which manages the llama-server model lifecycle.
 *
 * When start is requested, the proxy is started first (via /api/proxy/start);
 * if MCP is enabled for this model it is then brought up separately via
 * /api/proxy/mcp/start (mirroring the auto-start behavior when a model starts).
 * On stop, the MCP server is stopped first (if enabled) followed by the proxy.
 *
 * @param {string} model - Model name
 * @param {boolean} start - true to start, false to stop
 * @param {boolean} [mcpEnabled] - Whether MCP is enabled for this model
 */
async function toggleProxy(model, start, mcpEnabled) {
  const action = start ? 'start' : 'stop';
  // Disable every proxy control button on the tile to prevent double-clicks.
  const controls = document.querySelectorAll('.proxy-status button');
  controls.forEach((b) => { b.disabled = true; });

  try {
    if (start) {
      const res = await fetch(`/api/proxy/start/${encodeURIComponent(model)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await res.json();
      if (data.error) {
        logHtml(`<b>Proxy start error:</b> ${data.error}`, 'red');
        return;
      }
      logHtml(`<b>Proxy started:</b> ${model} → ${data.status}`, 'green');

      // Bring up the MCP server too when enabled (matches model auto-start).
      if (mcpEnabled === true) {
        const mres = await fetch(`/api/proxy/mcp/start/${encodeURIComponent(model)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        const mdata = await mres.json();
        if (mdata.error) {
          logHtml(`<b>MCP start error:</b> ${mdata.error}`, 'orange');
        } else {
          logHtml(`<b>MCP started:</b> ${model} → ${mdata.status || 'started'}`, 'green');
        }
      }
    } else {
      // Stop MCP first (if enabled) so the proxy isn't left with a dangling child.
      if (mcpEnabled === true) {
        const mres = await fetch(`/api/proxy/mcp/stop/${encodeURIComponent(model)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        const mdata = await mres.json();
        if (mdata.error && mdata.status !== 'not_running') {
          logHtml(`<b>MCP stop error:</b> ${mdata.error}`, 'orange');
        }
      }
      const res = await fetch(`/api/proxy/stop/${encodeURIComponent(model)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await res.json();
      if (data.error) {
        logHtml(`<b>Proxy stop error:</b> ${data.error}`, 'red');
      } else {
        logHtml(`<b>Proxy stopped:</b> ${model} → ${data.status}`, 'blue');
      }
    }
    // Refresh to update status chips and button enabled/disabled states.
    setTimeout(() => refresh(true), 500);
  } catch (e) {
    console.error('toggleProxy error:', e);
    logHtml(`<b>Proxy ${action} error:</b> ${e}`, 'red');
  } finally {
    controls.forEach((b) => { b.disabled = false; });
  }
}
window.toggleProxy = toggleProxy;

/**
 * Show mnemonic-proxy metrics dashboard for a model
 * @param {string} model - Model name
 */
async function showProxyMetrics(model) {
  let refreshInterval = null;
  let countdownInterval = null;
  let currentModal = null;
  let strataContext = null;
  let conversationId = null;
  let currentTab = 'metrics'; // 'metrics', 'strata', 'journal'
  let journalEvents = null;
  let healthSummary = null;
  let journalFetchError = null;
  
  async function fetchAllData() {
    try {
      // Build strata-context URL with conversation_id if available
      let strataUrl = `/api/proxy/strata-context/${encodeURIComponent(model)}`;
      if (conversationId) {
        strataUrl += `?conversation_id=${encodeURIComponent(conversationId)}`;
      }
      
      // Fetch metrics and strata first (critical), then journal (non-critical)
      const [metricsRes, strataRes] = await Promise.all([
        fetch(`/api/proxy/metrics/${encodeURIComponent(model)}`),
        fetch(strataUrl)
      ]);
      
      const metricsData = await metricsRes.json();
      const strataData = await strataRes.json();
      
      if (metricsData.error) {
        if (currentModal) {
          document.getElementById('proxy-metrics-content').innerHTML = `<div style="color: var(--red); padding: 1rem;">Error: ${he(metricsData.error)}</div>`;
        }
        return;
      }
      
      // Update conversation_id from response if available
      if (strataData.strata_context && strataData.strata_context.conversation_id) {
        conversationId = strataData.strata_context.conversation_id;
      }
      strataContext = strataData.strata_context || null;
      
      // Fetch journal events (non-critical, handle errors gracefully)
      journalFetchError = null;
      try {
        const journalRes = await fetch(`/api/proxy/archive/journal?model=${encodeURIComponent(model)}&limit=200`);
        const journalData = await journalRes.json();
        
        // Handle wrapped response format {success: true, data: {...}}
        const payload = (journalData && journalData.success && journalData.data) ? journalData.data : journalData;
        
        if (payload.error) {
          journalFetchError = payload.error;
          journalEvents = [];
        } else {
          // Transform events: API returns event_type, but code expects event
          journalEvents = (payload.events || []).map(e => ({ ...e, event: e.event_type }));
        }
      } catch (e) {
        console.warn('Journal fetch failed:', e);
        journalFetchError = e.message;
        journalEvents = [];
      }
      
      healthSummary = computeHealthSummary(journalEvents, strataContext);
      renderCurrentTab(metricsData.metrics, metricsData.raw);
    } catch (e) {
      console.error('showProxyMetrics error:', e);
      if (currentModal) {
        document.getElementById('proxy-metrics-content').innerHTML = `<div style="color: var(--red); padding: 1rem;">Error: ${he(e.message)}</div>`;
      }
    }
  }
  
  function computeHealthSummary(events, strata) {
    const summary = {
      maskReuseGap: null,      // % gap between virtual and active tokens (last N events)
      guardPressure: 0,        // frequency of guard events
      recentErrors: 0,         // count of error events in recent events
      lastKvArchive: null,     // last kv_archive outcome
    };
    
    if (!events || events.length === 0) return summary;
    
    // Analyze last 50 events for trends
    const recentEvents = events.slice(-50);
    
    // Helper to get data object from event
    const getData = (e) => e.data || {};
    
    // Mask reuse gap: average of (virtual_tokens - active_tokens) / virtual_tokens for request events
    // Data is in e.data object
    const requestEvents = recentEvents.filter(e => e.event === 'request' && getData(e).virtual_tokens && getData(e).prompt_tokens);
    if (requestEvents.length > 0) {
      const gaps = requestEvents.map(e => {
        const d = getData(e);
        const virtual = d.virtual_tokens || 0;
        const active = d.prompt_tokens || 0;
        return virtual > 0 ? ((virtual - active) / virtual) * 100 : 0;
      });
      summary.maskReuseGap = gaps.reduce((a, b) => a + b, 0) / gaps.length;
    }
    
    // Guard pressure: count of window_guard events (API uses window_guard, not guard)
    summary.guardPressure = recentEvents.filter(e => e.event === 'window_guard').length;
    
    // Recent errors: check for error events in data or event type
    summary.recentErrors = recentEvents.filter(e => 
      e.event === 'error' || e.event === 'error_recover' || getData(e).error
    ).length;
    
    // Last kv_archive outcome: check for kv_archive or archive_save events
    const kvArchiveEvents = events.filter(e => e.event === 'kv_archive' || e.event === 'archive_save');
    if (kvArchiveEvents.length > 0) {
      const last = kvArchiveEvents[kvArchiveEvents.length - 1];
      const d = getData(last);
      summary.lastKvArchive = {
        success: last.success !== false && d.success !== false,
        savings: d.bytes_saved || d.savings || 0,
        duration: d.duration_ms || d.duration || 0,
        timestamp: last.timestamp || last.time || null
      };
    }
    
    return summary;
  }
  
  function formatJournalEvent(event) {
    const evType = event.event;
    const time = event.timestamp ? new Date(event.timestamp * 1000).toLocaleTimeString() : 'N/A';
    const conv = event.conv ? `${String(event.conv).slice(0, 8)}...` : 'N/A';
    
    // Helper to get data from event.data object with fallback to event itself
    const d = event.data || {};
    const get = (key, fallback) => d[key] !== undefined ? d[key] : (event[key] !== undefined ? event[key] : fallback);
    
    let icon = '📝';
    let color = 'var(--text)';
    let summaryParts = [];
    let detailParts = [];
    
    switch (evType) {
      case 'request': {
        icon = '📥';
        color = 'var(--blue)';
        const reused = get('reused', 0);
        const virtual = get('virtual_tokens', 0);
        const prompt = get('prompt_tokens', 0);
        const reusePct = virtual > 0 ? ((reused / virtual) * 100).toFixed(1) : 0;
        summaryParts = [
          { label: 'Prompt', value: prompt, isNum: true },
          { label: 'Reused', value: reused, isNum: true, pct: reusePct },
          { label: 'Virtual', value: virtual, isNum: true }
        ];
        if (get('invalidation_cause')) {
          detailParts.push({ label: 'Invalidation', value: get('invalidation_cause') });
        }
        if (get('seg') !== undefined) {
          detailParts.push({ label: 'Segment', value: get('seg') });
        }
        if (get('mode')) {
          detailParts.push({ label: 'Mode', value: get('mode') });
        }
        break;
      }
      case 'mask':
      case 'mask_invalidate': {
        icon = evType === 'mask' ? '🎭' : '🎭⚡';
        color = 'var(--purple)';
        const delta = get('delta', 0);
        const ratio = get('ratio', 0);
        const saved = get('tokens_saved', 0);
        summaryParts = [
          { label: 'Delta', value: delta, isNum: true },
          { label: 'Ratio', value: (ratio * 100).toFixed(1) + '%' },
          { label: 'Saved', value: saved, isNum: true, suffix: 'tokens' }
        ];
        if (get('reason')) {
          detailParts.push({ label: 'Reason', value: get('reason') });
        }
        break;
      }
      case 'window_guard':
      case 'guard': {
        icon = '🛡️';
        color = 'var(--orange)';
        const action = get('action', 'unknown');
        const pressure = get('window_pressure', get('pressure', 0));
        summaryParts = [
          { label: 'Action', value: action },
          { label: 'Window Pressure', value: pressure, isNum: true }
        ];
        if (get('trigger')) {
          detailParts.push({ label: 'Trigger', value: get('trigger') });
        }
        break;
      }
      case 'compress': {
        icon = '🗜️';
        color = 'var(--teal)';
        const ratio = get('ratio', get('compression_ratio', 0));
        const saved = get('bytes_saved', get('saved', 0));
        const duration = get('duration_ms', get('duration', 0));
        summaryParts = [
          { label: 'Ratio', value: (ratio * 100).toFixed(1) + '%' },
          { label: 'Saved', value: saved, isNum: true, suffix: 'bytes' },
          { label: 'Duration', value: duration, isNum: true, suffix: 'ms' }
        ];
        if (get('method')) {
          detailParts.push({ label: 'Method', value: get('method') });
        }
        break;
      }
      case 'error':
      case 'error_recover': {
        icon = evType === 'error' ? '❌' : '🔄';
        color = 'var(--red)';
        const message = get('message', get('error', 'Unknown error'));
        summaryParts = [{ label: '', value: message }];
        if (get('code')) {
          detailParts.push({ label: 'Code', value: get('code') });
        }
        if (get('recovered') === true) {
          summaryParts.push({ label: '', value: '(Recovered)' });
        }
        break;
      }
      case 'warn':
      case 'warning': {
        icon = '⚠️';
        color = 'var(--warning)';
        const message = get('message', get('warning', 'Warning'));
        summaryParts = [{ label: '', value: message }];
        break;
      }
      case 'recall':
      case 'recall_use': {
        icon = '🔁';
        color = 'var(--green)';
        const count = get('count', 1);
        const tokens = get('tokens', 0);
        summaryParts = [
          { label: 'Recalls', value: count, isNum: true },
          { label: 'Tokens', value: tokens, isNum: true }
        ];
        break;
      }
      case 'kv_archive':
      case 'archive_save': {
        icon = '💾';
        color = 'var(--info)';
        const success = get('success', true) !== false;
        const savings = get('bytes_saved', get('savings', 0));
        const duration = get('duration_ms', get('duration', 0));
        summaryParts = [
          { label: '', value: success ? 'Success' : 'Failed' },
          { label: 'Saved', value: savings, isNum: true, suffix: 'bytes' },
          { label: 'Duration', value: duration, isNum: true, suffix: 'ms' }
        ];
        break;
      }
      default: {
        icon = '📄';
        color = 'var(--text2)';
        summaryParts = [{ label: '', value: JSON.stringify(event).slice(0, 200) }];
      }
    }
    
    return { icon, color, summaryParts, detailParts, time, conv, type: evType };
  }
  
  function renderHealthBadges() {
    if (!healthSummary) return '';
    
    const badges = [];
    
    // Mask Reuse Gap
    if (healthSummary.maskReuseGap !== null) {
      const gap = healthSummary.maskReuseGap;
      const badgeColor = gap < 20 ? 'var(--success)' : gap < 50 ? 'var(--warning)' : 'var(--red)';
      const badgeLabel = gap < 20 ? 'Good' : gap < 50 ? 'Fair' : 'Poor';
      badges.push(`
        <div style="background: ${badgeColor}20; border: 1px solid ${badgeColor}; border-radius: 6px; padding: 0.5rem 0.75rem; display: flex; flex-direction: column; align-items: center; min-width: 100px;">
          <div style="font-size: 0.65rem; color: var(--text2); text-transform: uppercase;">Mask Reuse Gap</div>
          <div style="font-size: 1.25rem; font-weight: 700; color: ${badgeColor}; font-family: monospace;">${gap.toFixed(1)}%</div>
          <div style="font-size: 0.6rem; color: ${badgeColor}; text-transform: uppercase;">${badgeLabel}</div>
        </div>
      `);
    }
    
    // Guard Pressure
    badges.push(`
      <div style="background: var(--surface2); border: 1px solid var(--surface3); border-radius: 6px; padding: 0.5rem 0.75rem; display: flex; flex-direction: column; align-items: center; min-width: 100px;">
        <div style="font-size: 0.65rem; color: var(--text2); text-transform: uppercase;">Guard Pressure</div>
        <div style="font-size: 1.25rem; font-weight: 700; color: ${healthSummary.guardPressure > 5 ? 'var(--red)' : healthSummary.guardPressure > 2 ? 'var(--warning)' : 'var(--success)'}; font-family: monospace;">${healthSummary.guardPressure}</div>
        <div style="font-size: 0.6rem; color: var(--text2); text-transform: uppercase;">Events (last 50)</div>
      </div>
    `);
    
    // Recent Errors
    const errorColor = healthSummary.recentErrors > 0 ? 'var(--red)' : 'var(--success)';
    badges.push(`
      <div style="background: ${errorColor}20; border: 1px solid ${errorColor}; border-radius: 6px; padding: 0.5rem 0.75rem; display: flex; flex-direction: column; align-items: center; min-width: 100px;">
        <div style="font-size: 0.65rem; color: var(--text2); text-transform: uppercase;">Recent Errors</div>
        <div style="font-size: 1.25rem; font-weight: 700; color: ${errorColor}; font-family: monospace;">${healthSummary.recentErrors}</div>
        <div style="font-size: 0.6rem; color: var(--text2); text-transform: uppercase;">Events (last 50)</div>
      </div>
    `);
    
    // Last KV Archive
    if (healthSummary.lastKvArchive) {
      const kv = healthSummary.lastKvArchive;
      const kvColor = kv.success ? 'var(--success)' : 'var(--red)';
      badges.push(`
        <div style="background: ${kvColor}20; border: 1px solid ${kvColor}; border-radius: 6px; padding: 0.5rem 0.75rem; display: flex; flex-direction: column; align-items: center; min-width: 120px;">
          <div style="font-size: 0.65rem; color: var(--text2); text-transform: uppercase;">Last KV Archive</div>
          <div style="font-size: 0.85rem; font-weight: 700; color: ${kvColor};">${kv.success ? '✓ Success' : '✗ Failed'}</div>
          <div style="font-size: 0.6rem; color: var(--text2);">Saved: ${heNum(kv.savings)}B</div>
        </div>
      `);
    } else {
      badges.push(`
        <div style="background: var(--surface2); border: 1px dashed var(--surface3); border-radius: 6px; padding: 0.5rem 0.75rem; display: flex; flex-direction: column; align-items: center; min-width: 120px; color: var(--text2);">
          <div style="font-size: 0.65rem; text-transform: uppercase;">Last KV Archive</div>
          <div style="font-size: 0.85rem;">N/A</div>
          <div style="font-size: 0.6rem;">No archive events</div>
        </div>
      `);
    }
    
    return `
      <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1rem; padding: 0.75rem; background: var(--surface2); border-radius: 8px; border: 1px solid var(--surface3);">
        <div style="font-size: 0.7rem; color: var(--text2); text-transform: uppercase; letter-spacing: 0.05em; width: 100%; margin-bottom: 0.5rem;">Health Summary (from recent journal events)</div>
        ${badges.join('')}
      </div>
    `;
  }
  
  function renderJournalTab() {
    // Show journal fetch error if any
    if (journalFetchError) {
      return `
        <div style="text-align: center; color: var(--red); padding: 2rem;">
          <div style="font-weight: 600; margin-bottom: 0.5rem;">⚠ Failed to load journal events</div>
          <div style="font-size: 0.85rem; color: var(--text2);">${he(journalFetchError)}</div>
          <button id="journal-refresh-now" class="btn-secondary" style="margin-top: 1rem; padding: 0.4rem 1rem;">🔄 Retry</button>
        </div>
      `;
    }
    
    if (!journalEvents || journalEvents.length === 0) {
      return `
        <div style="text-align: center; color: var(--text2); padding: 2rem;">
          No journal events available
        </div>
      `;
    }
    
    // Show most recent events first
    const displayEvents = [...journalEvents].reverse().slice(0, 100);
    
    let html = `
      <div style="margin-bottom: 1rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; flex-wrap: wrap; gap: 0.5rem;">
          <span style="font-size: 0.85rem; color: var(--text2);">Showing ${displayEvents.length} of ${journalEvents.length} events</span>
          <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
            <button id="journal-refresh-now" class="btn-secondary" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">🔄 Refresh</button>
            <button id="journal-open-archive" class="btn-primary" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">📂 Open in Archive Browser</button>
          </div>
        </div>
      </div>
    `;
    
    html += `
      <div style="max-height: 60vh; overflow: auto; border: 1px solid var(--surface3); border-radius: 8px; background: var(--surface2);">
        <table style="width: 100%; border-collapse: collapse; font-size: 0.75rem;">
          <thead>
            <tr style="background: var(--surface3); position: sticky; top: 0; z-index: 1;">
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--surface3); white-space: nowrap;">Time</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--surface3); white-space: nowrap;">Type</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--surface3);">Conversation</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--surface3);">Summary</th>
              <th style="padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--surface3);">Details</th>
            </tr>
          </thead>
          <tbody>
    `;
    
    // Helper to format summary parts for display
    function formatSummaryParts(parts) {
      return parts.map(p => {
        let val = p.value;
        if (p.isNum && typeof val === 'number') {
          val = heNum(val);
        } else {
          val = he(String(val));
        }
        if (p.suffix) val += ' ' + p.suffix;
        if (p.pct !== undefined) val += ' (' + p.pct + '%)';
        if (p.label) return `${he(p.label)}: ${val}`;
        return val;
      }).join(', ');
    }
    
    // Helper to format detail parts for display
    function formatDetailParts(parts) {
      return parts.map(p => `${he(p.label)}: ${he(String(p.value))}`).join(' | ');
    }
    
    for (const event of displayEvents) {
      const formatted = formatJournalEvent(event);
      const rowColor = formatted.color;
      
      const summaryStr = formatSummaryParts(formatted.summaryParts);
      const detailsStr = formatDetailParts(formatted.detailParts);
      
      html += `
        <tr style="border-bottom: 1px solid var(--surface3); transition: background 0.1s;">
          <td style="padding: 0.5rem; font-family: monospace; color: var(--text2); white-space: nowrap;">${he(formatted.time)}</td>
          <td style="padding: 0.5rem; white-space: nowrap;">
            <span style="color: ${he(rowColor)}; font-weight: 600;">${he(formatted.icon)} ${he(formatted.type)}</span>
          </td>
          <td style="padding: 0.5rem; font-family: monospace; color: var(--text); max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${he(formatted.conv)}</td>
          <td style="padding: 0.5rem; color: var(--text); max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${summaryStr}</td>
          <td style="padding: 0.5rem; color: var(--text2); font-size: 0.7rem; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${detailsStr}</td>
        </tr>
      `;
    }
    
    html += `
          </tbody>
        </table>
      </div>
    `;
    
    return html;
  }
  
  function renderMetricsTab(metrics, raw) {
    // Key metrics to highlight
    const keyMetrics = [
      { key: 'llamacpp:prompt_tokens_total', label: 'Prompt Tokens Total', format: (v) => v.toLocaleString() },
      { key: 'llamacpp:prompt_tokens_cached_total', label: 'Cached Prompt Tokens', format: (v) => v.toLocaleString() },
      { key: 'llamacpp:tokens_predicted_total', label: 'Predicted Tokens Total', format: (v) => v.toLocaleString() },
      { key: 'llamacpp:prompt_seconds_total', label: 'Prompt Processing Time (s)', format: (v) => v.toFixed(2) },
      { key: 'llamacpp:tokens_predicted_seconds_total', label: 'Generation Time (s)', format: (v) => v.toFixed(2) },
      { key: 'llamacpp:n_decode_total', label: 'Decode Calls', format: (v) => v.toLocaleString() },
      { key: 'llamacpp:n_tokens_max', label: 'Max Sequence Length', format: (v) => v.toLocaleString() },
      { key: 'llamacpp:spec_decode_num_draft_tokens_total', label: 'Speculative Draft Tokens', format: (v) => v.toLocaleString() },
      { key: 'llamacpp:spec_decode_num_accepted_tokens_total', label: 'Speculative Accepted Tokens', format: (v) => v.toLocaleString() },
      { key: 'llamacpp:requests_processing', label: 'Requests Processing', format: (v) => v.toFixed(0) },
      { key: 'llamacpp:requests_deferred', label: 'Requests Deferred', format: (v) => v.toFixed(0) },
      { key: 'llamacpp:n_busy_slots_per_decode', label: 'Busy Slots per Decode', format: (v) => v.toFixed(2) },
    ];
    
    let html = `
      <div style="margin-bottom: 1rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
          <span style="font-size: 0.85rem; color: var(--text2);">Auto-refresh: <span id="metrics-refresh-countdown" style="font-weight: bold;">5s</span></span>
          <button id="proxy-metrics-refresh-now" class="btn-secondary" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">Refresh Now</button>
        </div>
    `;
    
    html += `
        <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.75rem; margin-bottom: 1rem;">
    `;
    
    // Key metrics cards
    for (const km of keyMetrics) {
      const metricData = metrics[km.key];
      let value = 'N/A';
      if (metricData && metricData.length > 0) {
        value = km.format(metricData[0].value);
      }
      html += `
        <div style="background: var(--surface2); border-radius: 8px; padding: 1rem; border: 1px solid var(--surface3);">
          <div style="font-size: 0.75rem; color: var(--text2); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.25rem;">${he(km.label)}</div>
          <div style="font-size: 1.5rem; font-weight: 600; color: var(--text); font-family: monospace;">${he(value)}</div>
        </div>
      `;
    }
    
    html += `
        </div>
        <details style="margin-top: 1rem;">
          <summary style="cursor: pointer; color: var(--text2); font-size: 0.85rem;">Show All Metrics (Raw)</summary>
          <div style="margin-top: 0.5rem; background: var(--surface2); border-radius: 4px; padding: 1rem; font-family: monospace; font-size: 0.7rem; overflow: auto; max-height: 30vh; white-space: pre-wrap;">${he(raw)}</div>
        </details>
      </div>
    `;
    
    return html;
  }
  
  function renderStrataTab() {
    if (!strataContext) {
      return `
        <div style="text-align: center; color: var(--text2); padding: 2rem;">
          Strata context not available - send a chat request to populate
          <button id="strata-reset-conversation" class="btn-secondary" style="margin-top: 1rem; padding: 0.4rem 1rem;">Reset Conversation</button>
        </div>
      `;
    }
    
    const strata = strataContext;
    
    return `
      <div style="margin-bottom: 1rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; flex-wrap: wrap; gap: 0.5rem;">
          <span style="font-size: 0.85rem; color: var(--text2);">Auto-refresh: <span id="metrics-refresh-countdown" style="font-weight: bold;">5s</span></span>
          <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
            <button id="proxy-metrics-refresh-now" class="btn-secondary" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">Refresh Now</button>
            <button id="strata-reset-conversation" class="btn-secondary" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">Reset Conversation</button>
          </div>
        </div>
      </div>
      <div style="background: linear-gradient(135deg, var(--surface2), var(--surface3)); border-radius: 8px; padding: 1rem; margin-bottom: 1rem; border: 1px solid var(--accent);">
        <div style="font-size: 0.75rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
          <span>🧠 Strata Context (Long-Context Engine)</span>
          <span style="font-size: 0.7rem; background: var(--accent); color: var(--surface); padding: 0.1rem 0.4rem; border-radius: 4px;">LIVE</span>
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 0.5rem;">
          <div style="background: var(--surface); border-radius: 6px; padding: 0.75rem; text-align: center; border: 1px solid var(--surface3);">
            <div style="font-size: 0.7rem; color: var(--text2); text-transform: uppercase; margin-bottom: 0.25rem;">Active Tokens</div>
            <div style="font-size: 1.75rem; font-weight: 700; color: var(--text); font-family: monospace;">${he(strata.active_tokens?.toLocaleString() || 'N/A')}</div>
          </div>
          <div style="background: var(--surface); border-radius: 6px; padding: 0.75rem; text-align: center; border: 1px solid var(--surface3);">
            <div style="font-size: 0.7rem; color: var(--text2); text-transform: uppercase; margin-bottom: 0.25rem;">Virtual Tokens</div>
            <div style="font-size: 1.75rem; font-weight: 700; color: var(--accent); font-family: monospace;">${he(strata.virtual_tokens?.toLocaleString() || 'N/A')}</div>
          </div>
          <div style="background: var(--surface); border-radius: 6px; padding: 0.75rem; text-align: center; border: 1px solid var(--surface3);">
            <div style="font-size: 0.7rem; color: var(--text2); text-transform: uppercase; margin-bottom: 0.25rem;">Archived Messages</div>
            <div style="font-size: 1.75rem; font-weight: 700; color: var(--warning); font-family: monospace;">${he(strata.archived?.toLocaleString() || 'N/A')}</div>
          </div>
          <div style="background: var(--surface); border-radius: 6px; padding: 0.75rem; text-align: center; border: 1px solid var(--surface3);">
            <div style="font-size: 0.7rem; color: var(--text2); text-transform: uppercase; margin-bottom: 0.25rem;">Segments</div>
            <div style="font-size: 1.75rem; font-weight: 700; color: var(--info); font-family: monospace;">${he(strata.segments?.toLocaleString() || 'N/A')}</div>
          </div>
          <div style="background: var(--surface); border-radius: 6px; padding: 0.75rem; text-align: center; border: 1px solid var(--surface3);">
            <div style="font-size: 0.7rem; color: var(--text2); text-transform: uppercase; margin-bottom: 0.25rem;">Recalls</div>
            <div style="font-size: 1.75rem; font-weight: 700; color: var(--success); font-family: monospace;">${he(strata.recalls?.toLocaleString() || 'N/A')}</div>
          </div>
          <div style="background: var(--surface); border-radius: 6px; padding: 0.75rem; text-align: center; border: 1px solid var(--surface3);">
            <div style="font-size: 0.7rem; color: var(--text2); text-transform: uppercase; margin-bottom: 0.25rem;">Cached Tokens</div>
            <div style="font-size: 1.75rem; font-weight: 700; color: var(--text); font-family: monospace;">${he(strata.cached_tokens?.toLocaleString() || 'N/A')}</div>
          </div>
          <div style="background: var(--surface); border-radius: 6px; padding: 0.75rem; text-align: center; border: 1px solid var(--surface3);">
            <div style="font-size: 0.7rem; color: var(--text2); text-transform: uppercase; margin-bottom: 0.25rem;">Active Est.</div>
            <div style="font-size: 1.75rem; font-weight: 700; color: var(--text); font-family: monospace;">${he(strata.active_tokens_est?.toLocaleString() || 'N/A')}</div>
          </div>
        </div>
        <div style="margin-top: 0.5rem; font-size: 0.7rem; color: var(--text2);">
          Conversation: ${strata.conversation_id ? `<a href="#" class="strata-conversation-link" data-conversation-id="${he(strata.conversation_id)}" style="color: var(--accent); text-decoration: underline; cursor: pointer;">${he(strata.conversation_id)}</a>` : 'N/A'} | Mode: ${he(strata.mode || 'N/A')} | Segment: ${he(strata.segment || 'N/A')}
        </div>
      </div>
    `;
  }
  
  function renderCurrentTab(metrics, raw) {
    if (!currentModal) return;
    
    const contentEl = document.getElementById('proxy-metrics-content');
    if (!contentEl) return;
    
    // Health badges at top (visible across all tabs)
    let html = renderHealthBadges();
    
    // Tab navigation
    html += `
      <div style="display: flex; gap: 0.5rem; margin-bottom: 1rem; border-bottom: 1px solid var(--surface3); padding-bottom: 0.5rem; flex-wrap: wrap;">
        <button class="tab-btn ${currentTab === 'metrics' ? 'active' : ''}" data-tab="metrics" style="padding: 0.5rem 1rem; border: 1px solid var(--surface3); background: ${currentTab === 'metrics' ? 'var(--accent)' : 'var(--surface2)'}; color: ${currentTab === 'metrics' ? 'var(--surface)' : 'var(--text)'}; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: 600;">📊 Metrics</button>
        <button class="tab-btn ${currentTab === 'strata' ? 'active' : ''}" data-tab="strata" style="padding: 0.5rem 1rem; border: 1px solid var(--surface3); background: ${currentTab === 'strata' ? 'var(--accent)' : 'var(--surface2)'}; color: ${currentTab === 'strata' ? 'var(--surface)' : 'var(--text)'}; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: 600;">🧠 Strata</button>
        <button class="tab-btn ${currentTab === 'journal' ? 'active' : ''}" data-tab="journal" style="padding: 0.5rem 1rem; border: 1px solid var(--surface3); background: ${currentTab === 'journal' ? 'var(--accent)' : 'var(--surface2)'}; color: ${currentTab === 'journal' ? 'var(--surface)' : 'var(--text)'}; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: 600;">📋 Journal & Events</button>
      </div>
    `;
    
    switch (currentTab) {
      case 'metrics':
        html += renderMetricsTab(metrics, raw);
        break;
      case 'strata':
        html += renderStrataTab();
        break;
      case 'journal':
        html += renderJournalTab();
        break;
    }
    
    contentEl.innerHTML = html;
    
    // Update summary badge in modal header
    const summaryBadgeEl = document.getElementById('proxy-metrics-summary-badge');
    if (summaryBadgeEl && healthSummary) {
      // Compute overall badge from health summary
      let badge = PROXY_HEALTH_BADGES.idle;
      if (healthSummary.recentErrors > 0) {
        badge = PROXY_HEALTH_BADGES.error;
      } else if (healthSummary.guardPressure > 0) {
        badge = PROXY_HEALTH_BADGES.guard;
      } else if (healthSummary.maskReuseGap !== null && healthSummary.maskReuseGap > 50) {
        badge = PROXY_HEALTH_BADGES.low_reuse;
      } else if (healthSummary.lastKvArchive && healthSummary.lastKvArchive.success) {
        badge = PROXY_HEALTH_BADGES.archive_ok;
      }
      
      summaryBadgeEl.innerHTML = `${badge.icon} ${badge.label}`;
      summaryBadgeEl.style.background = badge.bg;
      summaryBadgeEl.style.border = `1px solid ${badge.border}`;
      summaryBadgeEl.style.color = badge.color;
      summaryBadgeEl.title = `Proxy health: ${badge.label}`;
    }
    
    // Add tab click listeners
    contentEl.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        currentTab = btn.dataset.tab;
        renderCurrentTab(metrics, raw);
      });
    });
    
    // Add refresh button listener
    const refreshBtn = document.getElementById('proxy-metrics-refresh-now');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', fetchAllData);
    }
    
    // Add journal refresh button listener
    const journalRefreshBtn = document.getElementById('journal-refresh-now');
    if (journalRefreshBtn) {
      journalRefreshBtn.addEventListener('click', fetchAllData);
    }
    
    // Add open archive button listener
    const openArchiveBtn = document.getElementById('journal-open-archive');
    if (openArchiveBtn) {
      openArchiveBtn.addEventListener('click', () => {
        // Open Archive Browser modal/page with current model/conversation context
        if (window.ArchiveBrowser) {
          window.ArchiveBrowser.open(model, conversationId);
        } else {
          // Fallback: navigate to archive page if it exists
          const url = `/archive?model=${encodeURIComponent(model)}${conversationId ? `&conversation=${encodeURIComponent(conversationId)}` : ''}`;
          window.open(url, '_blank');
        }
      });
    }
    
    // Reset conversation button listener
    const resetConvBtn = document.getElementById('strata-reset-conversation');
    if (resetConvBtn) {
      resetConvBtn.addEventListener('click', async () => {
        conversationId = null;
        await fetchAllData();
      });
    }

    // Strata conversation link click handler
    contentEl.querySelectorAll('.strata-conversation-link').forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const convId = link.dataset.conversationId;
        if (convId && window.ArchiveBrowser) {
          window.ArchiveBrowser.open(model, convId);
        }
      });
    });
  }
  
  function startCountdown() {
    let seconds = 5;
    
    const interval = setInterval(() => {
      seconds--;
      // Re-acquire the countdown element each tick since renderCurrentTab() 
      // replaces the content area and destroys the old element
      const countdownEl = document.getElementById('metrics-refresh-countdown');
      if (countdownEl && currentModal) {
        countdownEl.textContent = `${seconds}s`;
      } else {
        clearInterval(interval);
        return;
      }
      // Do NOT call fetchAllData() here - the single setInterval handles fetching
      if (seconds <= 0) {
        seconds = 5;
      }
    }, 1000);
    
    return interval;
  }
  
  // Create modal
  const modalHtml = `
    <div id="proxy-metrics-modal" class="modal-overlay" style="display: flex; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 1500; align-items: center; justify-content: center;" aria-hidden="false">
      <div class="modal-content" style="background: var(--surface); border: 1px solid var(--surface2); border-radius: 12px; max-width: 1100px; width: 95%; max-height: 85vh; overflow: hidden; padding: 1.5rem; box-shadow: 0 8px 32px rgba(0,0,0,0.3); display: flex; flex-direction: column;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; gap: 0.5rem;">
          <div style="display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
            <h3 style="margin: 0; color: var(--text);">Mnemonic-Proxy Metrics: ${he(model)}</h3>
            <span id="proxy-metrics-summary-badge" class="proxy-health-badge" style="display: inline-flex; align-items: center; gap: 0.25rem; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.65rem; font-weight: 600; text-transform: uppercase; white-space: nowrap;">Loading...</span>
          </div>
          <button id="proxy-metrics-close" style="background: none; border: none; color: var(--text2); font-size: 1.5rem; cursor: pointer; line-height: 1; padding: 0;">&times;</button>
        </div>
        <div style="flex: 1; overflow: auto;" id="proxy-metrics-content">
          <div style="text-align: center; color: var(--text2); padding: 2rem;">Loading metrics...</div>
        </div>
        <div style="display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1rem;">
          <button type="button" class="btn-cancel" id="proxy-metrics-close-btn" style="padding: 0.4rem 1rem;">Close</button>
        </div>
      </div>
    </div>
  `;
  
  // Remove any existing modal
  const existing = document.getElementById('proxy-metrics-modal');
  if (existing) existing.remove();
  
  // Add to body
  document.body.insertAdjacentHTML('beforeend', modalHtml);
  currentModal = document.getElementById('proxy-metrics-modal');
  
  // Add event listeners
  const closeModal = () => {
    if (refreshInterval) clearInterval(refreshInterval);
    if (countdownInterval) clearInterval(countdownInterval);
    if (currentModal) currentModal.remove();
    currentModal = null;
  };
  
  document.getElementById('proxy-metrics-close').addEventListener('click', closeModal);
  document.getElementById('proxy-metrics-close-btn').addEventListener('click', closeModal);
  
  // Close on overlay click
  currentModal.addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
      closeModal();
    }
  });
  
  // Initial fetch
  await fetchAllData();
  
  // Start auto-refresh
  refreshInterval = setInterval(fetchAllData, 5000);
  countdownInterval = startCountdown();
  
  // Cleanup on modal removal
  const observer = new MutationObserver(() => {
    if (!document.getElementById('proxy-metrics-modal')) {
      closeModal();
      observer.disconnect();
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

window.showProxyMetrics = showProxyMetrics;
