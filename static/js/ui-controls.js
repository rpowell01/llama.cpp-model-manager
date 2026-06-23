/**
 * UI rendering - tiles and panels
 */

/**
 * Render all model tiles
 * @param {Array} rows - Model data rows from API
 * @param {Object} dlMap - Download status map
 * @param {string} query - Search query for filtering
 */
function renderTiles(rows, dlMap, query) {
  const grid = document.getElementById('tile-grid');
  if (!grid) return;

  const filtered = rows.filter(r => {
    const q = query.toLowerCase();
    return r.model.toLowerCase().includes(q) || String(r.port).includes(q);
  });

  // Sort by model name, then by filename
  filtered.sort((a, b) => {
    const modelCompare = a.model.localeCompare(b.model);
    if (modelCompare !== 0) return modelCompare;
    const aFilename = a.filename || (a.model_path ? a.model_path.split('/').pop() : a.model);
    const bFilename = b.filename || (b.model_path ? b.model_path.split('/').pop() : b.model);
    return aFilename.localeCompare(bFilename);
  });

  const html = filtered.map((r) => {
    const running = r.status && r.status.startsWith('running');
    const starting = r.status === 'starting';
    const statusChip = running
      ? '<span class="chip ok">● running</span>'
      : starting
      ? '<span class="chip warn">● starting</span>'
      : r.file_exists
      ? '<span class="chip ok">● stopped</span>'
      : '<span class="chip bad">✕ missing</span>';

    const dl = dlMap[r.model] || {};
    const partial = r.partial_download || 0;

    // Check if there's an active download - use dl from API or stored progress state
    const apiProgress = dlMap[r.model];
    let dlProgress = (apiProgress && apiProgress.status) ? apiProgress : {};
    // Prefer client-side progress state for any active or recent download status
    if (downloadProgressState[r.model] && ['downloading', 'complete', 'error'].includes(downloadProgressState[r.model].status)) {
      dlProgress = downloadProgressState[r.model];
    }

    // Use dlProgress (which includes client-side state) for button display
    let dlHtml = '';
    if (dlProgress.status === 'downloading') {
      dlHtml = '<button class="download" disabled>⬇ ' + (dlProgress.progress || 0) + '%</button>';
    } else if (dlProgress.status === 'complete') {
      // File is now downloaded, show Re-download button
      const force = r.file_exists ? 'true' : 'false';
      dlHtml = '<button class="download" onclick="downloadWithResume(\'' + jsEscape(r.model) + '\', false, 0, ' + force + ')">⬇ Re-download</button>';
    } else if (dlProgress.status === 'error') {
      dlHtml = '<button class="download" onclick="retryDownload(\'' + jsEscape(r.model) + '\', true)">🔄 Retry</button>';
    } else if (partial > 0) {
      dlHtml = '<button class="download" onclick="resumeDownload(\'' + jsEscape(r.model) + '\', ' + partial + ')">▶ Resume</button>';
    } else {
      const force = r.file_exists ? 'true' : 'false';
      dlHtml = '<button class="download" onclick="downloadWithResume(\'' + jsEscape(r.model) + '\', false, 0, ' + force + ')">⬇ ' + (r.file_exists ? 'Re-download' : 'Download') + '</button>';
    }

    const portCell = r.file_exists
      ? '<span class="port-cell">'
        + '<a class="actions btn-goto" style="padding:.3rem .7rem;border-radius:4px;font-size:.8rem;color:#fff;background:var(--blue);text-decoration:none" href="' + portUrl(r.port) + '" target="_blank">:' + r.port + '</a>'
         + '<button class="btn-edit-port" data-model="' + he(r.model) + '" onclick="event.stopPropagation(); editPortInline(this.dataset.model, ' + r.port + ')" title="Edit port">✏</button>'
        + '</span>'
      : '<span class="chip bad">-</span>';

    const showProgress = dlProgress.status === 'downloading' || (dlProgress.progress && dlProgress.progress > 0);

    const progressHtml = showProgress
      ? '<div id="progress-' + safeId(r.model) + '" class="download-progress" style="margin-top: .5rem; padding: .5rem; background: var(--surface2); border-radius: 4px;">'
        + '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: .25rem;">'
        + '<strong style="font-size: .85rem;">Download Progress</strong>'
        + '<span id="progress-text-' + safeId(r.model) + '" style="font-size: .75rem;">' + (dlProgress.progress || 0) + '%</span>'
        + '</div>'
        + '<div style="background: var(--surface); border-radius: 4px; height: 6px; overflow: hidden;">'
        + '<div id="progress-bar-' + safeId(r.model) + '" style="background: var(--btn-download); height: 100%; width: ' + (dlProgress.progress || 0) + '%; transition: width 0.2s;"></div>'
        + '</div>'
        + '<div id="progress-details-' + safeId(r.model) + '" style="font-size: .7rem; color: var(--text2); margin-top: .25rem;">' + (dlProgress.speed_mbps ? dlProgress.speed_mbps + ' MB/s' : '') + '</div>'
        + '</div>'
        : '';

     return '<div class="tile ' + (running ? 'running' : '') + '" data-model="' + jsEscape(r.model) + '" onclick="selectModel(\'' + jsEscape(r.model) + '\')">'
      + '<div class="tile-header"><b>' + jsEscape(r.model) + '</b>' + statusChip + '</div>'
      + '<div class="tile-body"><code title="' + jsEscape(r.model_path) + '">' + jsEscape(r.model_path ? r.model_path.split('/').pop() : r.filename) + '</code>'
      + '<div>' + fmtGb(r.file_size_gb) + ' · ' + (r.status || '-') + (r.pid ? ' · pid ' + r.pid : '') + '</div>'
      + '<div>' + portCell + '</div>'
      + '</div>'
      + '<div class="tile-actions actions">'
      + dlHtml
      + ' <button class="run" onclick="act(\'' + jsEscape(r.model) + '\', \'start\')" ' + (running || starting || !r.file_exists ? 'disabled' : '') + '>▶ Start</button>'
      + ' <button class="stop" onclick="act(\'' + jsEscape(r.model) + '\', \'stop\')" ' + (!running && !starting ? 'disabled' : '') + '>■ Stop</button>'
      + ' <button class="add" onclick="downloadModel(\'' + jsEscape(r.model) + '\', true)">＋ Add Variant</button>'
      + ' <button class="del" onclick="promptDeleteModel(\'' + jsEscape(r.model) + '\')">✕ Delete</button>'
      + (running ? ' <button class="chat" onclick="event.stopPropagation(); openChatModal(\'' + jsEscape(r.model) + '\')">💬 Chat</button>' : '')
      + '</div>'
      + progressHtml
      + '</div>';
  }).join('');

  grid.innerHTML = html;
}

/**
 * Render the properties panel for a model
 * @param {Object} data - Model data row
 * @param {string} modelName - Model name
 */
async function renderPanelProperties(data, modelName) {
  const content = document.getElementById('panel-content');
  if (!content) return;

   const running = data.status && data.status.startsWith('running');
   const modelSafeId = safeId(modelName);

  let html = '';

  // Static Properties section
  html += `<div class="panel-section">
    <h3>Properties</h3>
    <div class="panel-grid">`;

  const addItem = (label, value, isCode = false) => {
    const escapedLabel = he(label);
    const escapedValue = isCode ? `<code>${he(value)}</code>` : he(value);
    html += `<div class="panel-item">
      <span class="label">${escapedLabel}</span>
      <span class="value">${escapedValue}</span>
    </div>`;
  };

  const addEditable = (label, field, value, inputType = 'number', step = 1, min, max) => {
    let input;
    const escapedLabel = he(label);
    const escapedValue = he(String(value));
    if (inputType === 'checkbox') {
      input = `<input type="checkbox" class="meta-input" data-field="${he(field)}" ${value ? 'checked' : ''}>`;
    } else if (inputType === 'text') {
      input = `<input type="text" class="meta-input" data-field="${he(field)}" value="${escapedValue}">`;
    } else {
      const minAttr = min !== undefined ? `min="${he(String(min))}"` : '';
      const maxAttr = max !== undefined ? `max="${he(String(max))}"` : '';
      input = `<input type="number" class="meta-input" data-field="${he(field)}" value="${escapedValue}" step="${step}" ${minAttr} ${maxAttr}>`;
    }
    html += `<div class="panel-item">
      <span class="label">${escapedLabel}</span>
      <span class="value">${input}</span>
    </div>`;
  };

  addItem('File', data.model_path ? data.model_path.split('/').pop() : (data.filename || '-'));
  addItem('Size', fmtGb(data.file_size_gb));
  addItem('Port', data.port ? `:${data.port}` : '-');
  addEditable('Cache Size', 'cache_size', data.cache_size, 'number', 1, 0);
  addEditable('Threads', 'threads', data.threads, 'number', 1, 0);
  addEditable('Threads Batch', 'threads_batch', data.threads_batch, 'number', 1, 0);
  addEditable('Timeout', 'timeout', data.timeout, 'number', 1, 0);
  addEditable('MLock', 'mlock', data.mlock, 'checkbox');

  // Flash Attn dropdown
  const flashVal = data.flash_attn || 'off';
  const isFlashSelected = (val) => flashVal === val || (val === 'no' && flashVal === 'off') || (val === 'yes' && flashVal === 'on');
  let flashSelect = `<select class="meta-input" data-field="flash_attn">`;
  ['no', 'yes', 'auto'].forEach(val => {
    flashSelect += `<option value="${val}" ${isFlashSelected(val) ? 'selected' : ''}>${val}</option>`;
  });
  flashSelect += `</select>`;
  html += `<div class="panel-item">
    <span class="label">Flash Attn</span>
    <span class="value">${flashSelect}</span>
  </div>`;

  addEditable('No MMAP', 'no_mmap', data.no_mmap, 'checkbox');
  addEditable('Parallel', 'parallel', data.parallel, 'number', 1, 1);
  addEditable('UBatch Size', 'ubatch_size', data.ubatch_size, 'number', 1, 1);

  html += `</div></div>`;

  // Running Statistics section
  if (running) {
    html += `<div class="panel-section">
      <h3>Running Statistics</h3>
      <div class="panel-grid">`;
    addItem('Status', '<span class="chip running">● running</span>');
    if (data.pid) addItem('PID', `<code>${data.pid}</code>`);
    html += `</div></div>`;
  }

  // Hugging Face Metadata section
  html += `<div class="panel-section">
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

   // Kilo Code Configuration section
   html += `<div class="panel-section">
     <h3>Killo Code Integration</h3>
     <div class="panel-item full-width">
       <button id="generate-config-${modelSafeId}" class="btn-primary">📋 Generate Kilo Code Config</button>
       <p style="margin-top: 0.5rem; font-size: 0.85rem; color: var(--text2);">
         Generate a provider configuration for Kilo Code based on this model's settings.
       </p>
     </div>
   </div>`;

  content.innerHTML = html;

  // Attach meta input listeners for auto-save
  content.closest('.properties-panel').dataset.modelName = modelName;
  content.querySelectorAll('.meta-input').forEach(input => {
    input.addEventListener('change', async (e) => {
      const field = e.target.dataset.field;
      let value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
      const numericFields = ['cache_size', 'threads', 'threads_batch', 'timeout', 'parallel', 'ubatch_size'];
      if (numericFields.includes(field)) {
        value = Number(value);
      }
      const panel = document.getElementById('properties-panel');
      const model = panel.dataset.modelName;
      if (!model) return;
      try {
        const payload = { model };
        payload[field] = value;
        const resp = await fetch('/api/update-model-meta', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          alert(`Error updating ${field}: ${err.error || 'unknown'}`);
        }
      } catch (err) {
        console.error('Failed to update meta', err);
        alert('Network error while updating');
      }
    });
  });

   // Attach generate config button listener
   const configBtn = document.getElementById(`generate-config-${modelSafeId}`);
   if (configBtn) {
     configBtn.addEventListener('click', () => {
       openKilloConfigModal(data, modelName);
     });
   }

   // Auto-search for HF repo if not configured and not already attempted
   if (!data.repo && !autoSearchAttempted.has(modelName)) {
     autoSearchAttempted.add(modelName);
     const statusEl = document.getElementById(`hf-search-status-${modelSafeId}`);
     if (statusEl) {
       statusEl.textContent = 'Automatically searching Hugging Face...';
       statusEl.style.display = 'block';
     }
     autoSearchAndSelectRepo(modelName);
   }

  // If no repo after attempting auto-search, skip metadata fetch
  if (!data.repo) {
    return;
  }

  // Fetch HF metadata asynchronously
  try {
    const resp = await fetch(`/api/hf-metadata?model=${encodeURIComponent(modelName)}`);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const msg = err.error || 'Failed to load Hugging Face metadata';
      document.getElementById('hf-metadata-error').textContent = msg;
      document.getElementById('hf-metadata-error').style.display = 'block';
      document.getElementById('hf-metadata-loading').style.display = 'none';
      return;
    }
    const meta = await resp.json();
    document.getElementById('hf-metadata-loading').style.display = 'none';

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
       meta.tags.slice(0, 8).forEach(tag => {
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
       const desc = meta.description.length > maxLen
         ? meta.description.substring(0, maxLen) + '...'
         : meta.description;
       metaHtml += `<div class="panel-item full-width"><span class="label">Description</span><span class="value" style="white-space: pre-wrap;">${he(desc)}</span></div>`;
     }
     if (meta.repo) {
       const repoUrl = `https://huggingface.co/${meta.repo}`;
       metaHtml += `<div class="panel-item full-width"><a href="${he(repoUrl)}" target="_blank" rel="noopener">View on Hugging Face ↗</a></div>`;
     }

    metaHtml += `</div>`;
    document.getElementById('hf-metadata-content').innerHTML = metaHtml;
    document.getElementById('hf-metadata-content').style.display = 'block';
  } catch (e) {
    document.getElementById('hf-metadata-loading').style.display = 'none';
    document.getElementById('hf-metadata-error').textContent = 'Network error while fetching metadata';
    document.getElementById('hf-metadata-error').style.display = 'block';
  }
}
