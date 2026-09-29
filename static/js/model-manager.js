/**
 * Model management and UI refresh logic
 */

// Cache for model metrics to avoid excessive polling
const modelMetricsCache = new Map();
const METRICS_CACHE_TTL = 5000; // 5 seconds

/**
 * Fetch and parse llama.cpp metrics from the model's port
 * @param {string} modelName - Model name
 * @param {number} port - Model port
 * @returns {Promise<Object>} Parsed metrics
 */
async function fetchModelMetrics(modelName, port) {
  const cacheKey = `${modelName}:${port}`;
  const now = Date.now();
  
  // Check cache
  if (modelMetricsCache.has(cacheKey)) {
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
    }
  }
  
  // Prompt eval rate = prompt_tokens_seconds (gauge, already tokens/sec)
  if (metrics['llamacpp:prompt_tokens_seconds']) {
    result.prompt_eval_rate = metrics['llamacpp:prompt_tokens_seconds'].toFixed(1);
  }
  
  // Eval time (average) = tokens_predicted_seconds_total / tokens_predicted_total
  if (metrics['llamacpp:tokens_predicted_seconds_total'] && metrics['llamacpp:tokens_predicted_total']) {
    const genSec = metrics['llamacpp:tokens_predicted_seconds_total'];
    const genTokens = metrics['llamacpp:tokens_predicted_total'];
    if (genTokens > 0) {
      result.eval_time_ms = (genSec / genTokens * 1000).toFixed(1);
    }
  }
  
  // Tokens/sec = predicted_tokens_seconds (gauge, already tokens/sec)
  if (metrics['llamacpp:predicted_tokens_seconds']) {
    result.tokens_per_sec = metrics['llamacpp:predicted_tokens_seconds'].toFixed(1);
  }
  
  // Also include raw totals for reference
  result.prompt_tokens_total = metrics['llamacpp:prompt_tokens_total'] || 0;
  result.prompt_seconds_total = metrics['llamacpp:prompt_seconds_total'] || 0;
  result.tokens_predicted_total = metrics['llamacpp:tokens_predicted_total'] || 0;
  result.tokens_predicted_seconds_total = metrics['llamacpp:tokens_predicted_seconds_total'] || 0;
  
  return result;
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
  
  const panel = document.getElementById('properties-panel');
  const contentArea = document.getElementById('content-area');
  panel.style.display = 'block';
  contentArea.classList.add('has-panel');
  panel.dataset.modelName = displayName;

  document.getElementById('panel-model-name').textContent = displayName;
  renderPanelProperties(modelData, displayName);
}

/**
 * Normalize a model name for consistent comparison
 * @param {string} name - Model name to normalize
 * @returns {string} Lowercase, trimmed model name
 */
function normalizeModelName(name) {
  return name.trim().toLowerCase();
}

/**
 * Close the properties panel
 */
function closePanel() {
  selectedModel = null;
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
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);
    const fetchPromise = fetch('/api/status?' + Date.now(), {
      cache: 'no-store',
      signal: controller.signal
    });
    const timeoutPromise = new Promise((_, reject) =>
      setTimeout(() => reject(new Error('Fetch timeout')), 30000)
    );
    const res = await Promise.race([fetchPromise, timeoutPromise]);
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

     const dlRes = await fetch('/api/download-status?' + Date.now(), {
       cache: 'no-store',
       signal: controller.signal
     });
     const dlText = await dlRes.text();
     const dlMap = {};
     let dlJson;
     try {
       dlJson = JSON.parse(dlText);
     } catch (e) {
       console.error('Failed to parse /api/download-status response:', dlText);
       dlJson = {};
     }
     Object.assign(dlMap, dlJson);

     renderTiles(deduplicated, dlMap, document.getElementById('search').value.toLowerCase());

     if (selectedModel) {
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
      log(`<b>${prev} error:</b> Server returned non-JSON response`, 'red');
      console.error('Failed to parse response:', text);
      return;
    }
    console.log('Parsed data:', data);
    if (data.error) {
      log('<b>' + prev + ' error:</b> ' + data.error, 'red');
    } else {
      log(`<b>${prev}:</b> ${model} → ${data.status}${data.pid ? ' (pid ' + data.pid + ')' : ''}`, 'green');
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
          if (modelData && modelData.status !== 'starting') {
            clearInterval(intervalId);
            delete startingIntervals[model];
          }
        }, 2000);
        startingIntervals[model] = intervalId;
      }
    }, delay);
  } catch (e) {
    console.error('act() error:', e);
    log('<b>Error:</b> ' + e, 'red');
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = prev;
    }, 800);
  }
}

// Expose metrics functions globally for tooltip use
window.fetchModelMetrics = fetchModelMetrics;
window.formatMetricsTooltip = formatMetricsTooltip;

