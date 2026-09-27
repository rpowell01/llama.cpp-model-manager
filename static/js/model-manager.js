/**
 * Model management and UI refresh logic
 */

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

