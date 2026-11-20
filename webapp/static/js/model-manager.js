/**
 * Model management and UI refresh logic
 */

// Cache for model metrics to avoid excessive polling
const modelMetricsCache = new Map();
const METRICS_CACHE_TTL = 5000; // 5 seconds

// Candidate list for "Draft Model Path" select (models flagged as speculative decoding models)
let currentSpecDraftModels = [];

// selectedModel is declared in state.js (loaded before this script)
// Currently selected model (set by selectModel())
// let selectedModel = null;

/**
 * Get the currently selected model name.
 * @returns {string|null} Selected model name or null
 */
function getSelectedModelId() {
  return selectedModel;
}

// Explicitly attach to window for global access (used by toolbar button)
window.getSelectedModelId = getSelectedModelId;

/**
 * Get the list of speculative-decoding model candidates from currentModelRows.
 * Called during refresh so the panel render has fresh options.
 */
function getSpecDraftModels() {
  return currentSpecDraftModels;
}

/**
 * Refresh the speculative-decoding candidate list from currentModelRows.
 * Call this after currentModelRows is updated (e.g., in refresh()).
 */
function updateSpecDraftModels() {
  currentSpecDraftModels = currentModelRows.filter(r => r.speculative_decoding_model);
}

/**
 * Fetch and parse llama.cpp metrics from the model's port
 * @param {string} modelName - Model name
 * @param {number} port - Model port
 * @param {boolean} bypassCache - If true, skip cache and fetch fresh data
 * @returns {Promise<Object>} Parsed metrics
 */
async function fetchModelMetrics(modelName, port, bypassCache = false) {
  const cacheKey = `${modelName}:${port}`;
  const now = Date.now();
  
  // Check cache (unless bypassing)
  if (!bypassCache && modelMetricsCache.has(cacheKey)) {
    const cached = modelMetricsCache.get(cacheKey);
    if (now - cached.timestamp < METRICS_CACHE_TTL) {
      return cached.data;
    }
  }
  
  try {
    const res = await fetch(`http://127.0.0.1:${port}/metrics`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(3000)
    });
    if (!res.ok) return null;
    
    const text = await res.text();
    const metrics = parsePrometheusMetrics(text);
    
    // Calculate derived metrics
    const result = calculateDerivedMetrics(metrics);
    
    // Cache result
    modelMetricsCache.set(cacheKey, { data: result, timestamp: now });
    return result;
  } catch (e) {
    // Silently fail - metrics are optional
    return null;
  }
}

/**
 * Parse Prometheus format metrics text
 * @param {string} text - Raw metrics text
 * @returns {Object} Parsed metrics keyed by name
 */
function parsePrometheusMetrics(text) {
  const metrics = {};
  const lines = text.split('\n');
  
  for (const line of lines) {
    // Skip comments and empty lines
    if (line.startsWith('#') || !line.trim()) continue;
    
    // Parse metric line: name{labels} value
    const match = line.match(/^([^{}\s]+)(?:\{([^}]*)\})?\s+(.+)$/);
    if (match) {
      const name = match[1];
      const value = parseFloat(match[3]);
      if (!isNaN(value)) {
        metrics[name] = value;
      }
    }
  }
  return metrics;
}

/**
 * Calculate derived metrics from raw counters
 * @param {Object} metrics - Raw metrics
 * @returns {Object} Derived metrics
 */
function calculateDerivedMetrics(metrics) {
  const result = { ...metrics };
  
  // Prompt eval time (average) = prompt_seconds_total / prompt_tokens_total
  if (metrics['llamacpp:prompt_seconds_total'] && metrics['llamacpp:prompt_tokens_total']) {
    const promptSec = metrics['llamacpp:prompt_seconds_total'];
    const promptTokens = metrics['llamacpp:prompt_tokens_total'];
    if (promptTokens > 0) {
      result.prompt_eval_time_ms = (promptSec / promptTokens * 1000).toFixed(1);
      // Calculate prompt eval rate from counters (tokens/sec)
      result.prompt_eval_rate = (promptTokens / promptSec).toFixed(1);
    }
  }
  
  // Eval time (average) = tokens_predicted_seconds_total / tokens_predicted_total
  if (metrics['llamacpp:tokens_predicted_seconds_total'] && metrics['llamacpp:tokens_predicted_total']) {
    const genSec = metrics['llamacpp:tokens_predicted_seconds_total'];
    const genTokens = metrics['llamacpp:tokens_predicted_total'];
    if (genTokens > 0) {
      result.eval_time_ms = (genSec / genTokens * 1000).toFixed(1);
      // Calculate gen rate from counters (tokens/sec)
      result.tokens_per_sec = (genTokens / genSec).toFixed(1);
    }
  }
  
  // Speculative decoding metrics
  const draftTokens = metrics['llamacpp:spec_decode_num_draft_tokens_total'] || 0;
  const acceptedTokens = metrics['llamacpp:spec_decode_num_accepted_tokens_total'] || 0;
  const drafts = metrics['llamacpp:spec_decode_num_drafts_total'] || 0;
  
  if (draftTokens > 0) {
    result.spec_draft_tokens_total = draftTokens;
    result.spec_accepted_tokens_total = acceptedTokens;
    result.spec_drafts_total = drafts;
    result.spec_acceptance_rate = (acceptedTokens / draftTokens * 100).toFixed(1);
    result.spec_avg_draft_len = (draftTokens / drafts).toFixed(1);
    // Speedup = 1 / (1 - acceptance_rate) approximately
    // More accurately: tokens generated per decode step = 1 + accepted_per_step
    const acceptedPerStep = acceptedTokens / drafts;
    result.spec_speedup = (1 + acceptedPerStep).toFixed(2);
  }
  
  // Also include raw totals for reference
  result.prompt_tokens_total = metrics['llamacpp:prompt_tokens_total'] || 0;
  result.prompt_seconds_total = metrics['llamacpp:prompt_seconds_total'] || 0;
  result.tokens_predicted_total = metrics['llamacpp:tokens_predicted_total'] || 0;
  result.tokens_predicted_seconds_total = metrics['llamacpp:tokens_predicted_seconds_total'] || 0;
  
  return result;
}

/**
 * Format duration in seconds to mm:ss.SSS format
 * @param {number} seconds - Duration in seconds
 * @returns {string} Formatted duration
 */
function formatDuration(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 1000);
  if (mins > 0) {
    return `${mins}:${secs.toString().padStart(2, '0')}.${ms.toString().padStart(3, '0')}`;
  }
  return `${secs}.${ms.toString().padStart(3, '0')}s`;
}

/**
 * Format metrics for tooltip display
 * @param {Object} metrics - Derived metrics
 * @returns {string} HTML for tooltip
 */
function formatMetricsTooltip(metrics) {
  if (!metrics) return '<div class="metrics-tooltip">No metrics available</div>';
  
  const lines = [];
  
  // Prompt processing metrics
  if (metrics.prompt_seconds_total !== undefined && metrics.prompt_seconds_total > 0) {
    lines.push(`<div><span class="metric-label">Total Prompt Time:</span> <span class="metric-value">${formatDuration(metrics.prompt_seconds_total)}</span></div>`);
  }
  if (metrics.prompt_eval_time_ms) {
    lines.push(`<div><span class="metric-label">Avg Prompt Eval Time:</span> <span class="metric-value">${metrics.prompt_eval_time_ms} ms/token</span></div>`);
  }
  if (metrics.prompt_eval_rate) {
    lines.push(`<div><span class="metric-label">Avg Prompt Eval Rate:</span> <span class="metric-value">${metrics.prompt_eval_rate} tokens/s</span></div>`);
  }
  
  // Generation metrics
  if (metrics.eval_time_ms) {
    lines.push(`<div><span class="metric-label">Avg Gen Eval Time:</span> <span class="metric-value">${metrics.eval_time_ms} ms/token</span></div>`);
  }
  if (metrics.tokens_per_sec) {
    lines.push(`<div><span class="metric-label">Avg Gen Rate:</span> <span class="metric-value">${metrics.tokens_per_sec} tokens/s</span></div>`);
  }
  
  // Add totals
  if (metrics.prompt_tokens_total || metrics.tokens_predicted_total) {
    lines.push('<div class="metric-separator"></div>');
    if (metrics.prompt_tokens_total) {
      lines.push(`<div>Prompt Tokens (total): ${metrics.prompt_tokens_total}</div>`);
    }
    if (metrics.tokens_predicted_total) {
      lines.push(`<div>Generated Tokens (total): ${metrics.tokens_predicted_total}</div>`);
    }
  }
  
  // Speculative decoding metrics
  if (metrics.spec_draft_tokens_total && metrics.spec_draft_tokens_total > 0) {
    lines.push('<div class="metric-separator"></div>');
    lines.push(`<div><span class="metric-label">Speculative Decoding:</span></div>`);
    if (metrics.spec_acceptance_rate !== undefined) {
      lines.push(`<div><span class="metric-label">Acceptance Rate:</span> <span class="metric-value">${metrics.spec_acceptance_rate}%</span></div>`);
    }
    if (metrics.spec_avg_draft_len !== undefined) {
      lines.push(`<div><span class="metric-label">Avg Draft Length:</span> <span class="metric-value">${metrics.spec_avg_draft_len} tokens</span></div>`);
    }
    if (metrics.spec_speedup !== undefined) {
      lines.push(`<div><span class="metric-label">Est. Speedup:</span> <span class="metric-value">${metrics.spec_speedup}x</span></div>`);
    }
    if (metrics.spec_draft_tokens_total) {
      lines.push(`<div>Draft Tokens: ${metrics.spec_draft_tokens_total} | Accepted: ${metrics.spec_accepted_tokens_total}</div>`);
    }
  }
  
  if (lines.length === 0) {
    return '<div class="metrics-tooltip">No metrics available</div>';
  }
  
  return '<div class="metrics-tooltip">' + lines.join('') + '</div>';
}

/**
 * Select a model to show its properties panel
 * @param {string} modelName
 */
function selectModel(modelName) {
  // Normalize the model name for consistent matching
  const normalizedModelName = modelName.trim().toLowerCase();
  
  // Find the model using normalized comparison
  const modelData = currentModelRows.find(r => normalizeModelName(r.model) === normalizedModelName);
  if (!modelData) {
    console.error('Model data not found for', modelName);
    return;
  }

  // Use the original (unnormalized) model name for display
  const displayName = modelData.model;
  selectedModel = displayName;
  
  // Update tile selection visual state
  updateTileSelection(displayName);
  
  const panel = document.getElementById('properties-panel');
  const contentArea = document.getElementById('content-area');
  panel.style.display = 'block';
  contentArea.classList.add('has-panel');
  panel.dataset.modelName = displayName;

  document.getElementById('panel-model-name').textContent = displayName;
  renderPanelProperties(modelData, displayName);
}

// Explicitly attach to window for global access
window.selectModel = selectModel;

/**
 * Update tile selection visual state
 * @param {string} modelName - Model name to mark as selected
 */
function updateTileSelection(modelName) {
  // Remove selected class from all tiles
  document.querySelectorAll('.tile').forEach(tile => {
    tile.classList.remove('selected');
  });
  
  // Add selected class to the clicked tile
  const normalizedModelName = modelName.trim().toLowerCase();
  const selectedTile = document.querySelector(`.tile[data-model="${normalizedModelName}"]`);
  if (selectedTile) {
    selectedTile.classList.add('selected');
  }
}

/**
 * Normalize a model name for consistent comparison
 * @param {string} name - Model name to normalize
 * @returns {string} Lowercase, trimmed model name with spaces/underscores/dots replaced by hyphens
 */
function normalizeModelName(name) {
  return name.trim().toLowerCase().replace(/[\s_.]+/g, '-');
}

/**
 * Close the properties panel
 */
function closePanel() {
  selectedModel = null;
  // Clear tile selection visual state
  document.querySelectorAll('.tile').forEach(tile => {
    tile.classList.remove('selected');
  });
  const panel = document.getElementById('properties-panel');
  const contentArea = document.getElementById('content-area');
  panel.style.display = 'none';
  contentArea.classList.remove('has-panel');
}

/**
 * Refresh model list and status
 * @param {boolean} showLoading
 */
async function refresh(showLoading = true) {
  if (isRefreshing) {
    console.log('Refresh already in progress, skipping');
    return;
  }
  isRefreshing = true;
  console.log('refresh() called, showLoading:', showLoading);
  if (showLoading) {
    const loadingEl = document.getElementById('loading');
    console.log('Loading element found:', !!loadingEl);
    if (loadingEl) loadingEl.style.display = 'block';
  }
  try {
    console.log('Fetching /api/status...');
    // Use Promise.race to enforce timeout even if fetch hangs
    // Use separate AbortController for each fetch to avoid cross-contamination
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000); // Increased to 60 seconds
    const fetchPromise = fetch('/api/status?' + Date.now(), {
      cache: 'no-store',
      signal: controller.signal
    });
    const timeoutPromise = new Promise((_, reject) =>
      setTimeout(() => reject(new Error('Fetch timeout')), 60000)
    );
    let res;
    try {
      res = await Promise.race([fetchPromise, timeoutPromise]);
    } catch (e) {
      clearTimeout(timeoutId);
      // Handle AbortError specifically - don't treat as failure if it's just timeout
      if (e.name === 'AbortError') {
        console.warn('Fetch aborted (likely timeout), retrying...');
        throw new Error('Fetch timeout');
      }
      throw e;
    }
    clearTimeout(timeoutId);
    console.log('Fetched /api/status, status:', res.status);
    if (!res.ok) {
      throw new Error('HTTP ' + res.status + ': ' + res.statusText);
    }
     const text = await res.text();
     console.log('Response text length:', text.length);
     let data;
     try {
       data = JSON.parse(text);
     } catch (e) {
       console.error('Failed to parse /api/status response:', text);
       throw e;
     }
     console.log('Parsed data:', data);
     let rows = data.models;
     console.log('Received', rows.length, 'models');
     
     // Deduplicate models by normalized name on refresh as well
     const seen = new Set();
     const deduplicated = [];
     for (const r of rows) {
       const normalized = normalizeModelName(r.model);
       if (!seen.has(normalized)) {
         seen.add(normalized);
         deduplicated.push(r);
       }
     }
     console.log('After deduplication:', deduplicated.length, 'models');
     
     currentModelRows = deduplicated;
     updateSpecDraftModels();

     // Use separate controller for download-status fetch
     const dlController = new AbortController();
     const dlTimeoutId = setTimeout(() => dlController.abort(), 30000);
     let dlRes;
     try {
       dlRes = await fetch('/api/download-status?' + Date.now(), {
         cache: 'no-store',
         signal: dlController.signal
       });
     } catch (e) {
       clearTimeout(dlTimeoutId);
       if (e.name === 'AbortError') {
         console.warn('Download status fetch aborted (timeout)');
         dlRes = null;
       } else {
         throw e;
       }
     }
     clearTimeout(dlTimeoutId);
     
     const dlMap = {};
     let dlJson = {};
     if (dlRes && dlRes.ok) {
       const dlText = await dlRes.text();
       try {
         dlJson = JSON.parse(dlText);
       } catch (e) {
         console.error('Failed to parse /api/download-status response:', dlText);
       }
     }
     Object.assign(dlMap, dlJson);

      renderTiles(deduplicated, dlMap, document.getElementById('search').value.toLowerCase());

      if (selectedModel) {
        // Re-apply tile selection after re-render
        updateTileSelection(selectedModel);
        
        // Use normalized comparison to find the model
        const normalizedSelected = normalizeModelName(selectedModel);
        const modelData = currentModelRows.find(r => normalizeModelName(r.model) === normalizedSelected);
        if (modelData) {
          // Use the original (unnormalized) model name for display
          const displayName = modelData.model;
          renderPanelProperties(modelData, displayName);
        } else {
          console.warn('Model not found after refresh:', selectedModel);
          closePanel();
        }
      }
  } catch (e) {
    console.error('Refresh failed:', e);
  } finally {
    const loadingEl = document.getElementById('loading');
    if (loadingEl) loadingEl.style.display = 'none';
    isRefreshing = false;
  }
}

// Explicitly attach to window for global access (needed by init.js)
window.refresh = refresh;

/**
 * Perform a model action (start/stop)
 * @param {string} model
 * @param {string} verb
 */
async function act(model, verb) {
  const btn = event.target;
  console.log('act() called with model:', model, 'verb:', verb, 'btn:', btn);
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = verb === 'start' ? 'Starting…' : 'Stopping…';
  try {
    const res = await fetch(`/api/${verb}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model })
    });
    console.log('Fetch response:', res.status, res.statusText);
    const text = await res.text();
    console.log('Response text:', text);
    let data;
    try {
      data = JSON.parse(text);
    } catch (parseError) {
      logHtml(`<b>${prev} error:</b> Server returned non-JSON response`, 'red');
      if (typeof showError === 'function') showError('Server returned invalid response', 'Start Failed');
      console.error('Failed to parse response:', text);
      return;
    }
    console.log('Parsed data:', data);
    if (data.error) {
      logHtml('<b>' + prev + ' error:</b> ' + data.error, 'red');
      // Show prominent error for startup failures
      if (verb === 'start' && typeof showError === 'function') {
        showError(data.error, `Failed to Start: ${model}`);
      }
    } else {
      logHtml(`<b>${prev}:</b> ${model} → ${data.status}${data.pid ? ' (pid ' + data.pid + ')' : ''}`, 'green');
    }
    const delay = verb === 'stop' ? 1200 : 800;
    console.log('Calling refresh after', delay, 'ms');
    const capturedBtn = btn;
    const capturedPrev = prev;
    setTimeout(() => {
      capturedBtn.disabled = false;
      capturedBtn.textContent = capturedPrev;
      console.log('Calling refresh()');
      refresh(true);
      if (verb === 'start') {
        if (startingIntervals[model]) {
          clearInterval(startingIntervals[model]);
        }
        const intervalId = setInterval(async () => {
          await refresh(false);
          const modelData = currentModelRows.find(r => r.model === model);
          // Continue polling until model is running AND proxy is running (if proxy enabled)
          // AND MCP is running (if MCP enabled)
          const proxyEnabled = modelData?.mnemonic_proxy?.enabled;
          const proxyRunning = modelData?.mnemonic_proxy?.running;
          const mcpEnabled = modelData?.mnemonic_proxy?.mcp_enabled;
          const mcpRunning = modelData?.mnemonic_proxy?.mcp?.running;
          const modelRunning = modelData && modelData.status !== 'starting';
          if (modelRunning && (!proxyEnabled || proxyRunning) && (!mcpEnabled || mcpRunning)) {
            clearInterval(intervalId);
            delete startingIntervals[model];
          }
        }, 2000);
        startingIntervals[model] = intervalId;
      }
    }, delay);
  } catch (e) {
    console.error('act() error:', e);
    logHtml('<b>Error:</b> ' + e, 'red');
    if (typeof showError === 'function') showError(String(e), 'Request Failed');
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = prev;
    }, 800);
  }
}

// Expose metrics functions and act globally for tooltip and panel use
window.fetchModelMetrics = fetchModelMetrics;
window.formatMetricsTooltip = formatMetricsTooltip;
window.act = act;

