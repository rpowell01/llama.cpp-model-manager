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
  const grid = document.getElementById("tile-grid");
  if (!grid) return;

  // Normalize model names to consistent casing for deduplication
  const normalizeModelName = (name) => {
    // Remove extra whitespace, convert to consistent case
    return name.trim().toLowerCase();
  };

  // Deduplicate: keep the first occurrence of each normalized model name
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
    const q = query.toLowerCase();
    return r.model.toLowerCase().includes(q) || String(r.port).includes(q);
  });

  // Sort by normalized model name, then by filename
  filtered.sort((a, b) => {
    const aNorm = normalizeModelName(a.model);
    const bNorm = normalizeModelName(b.model);
    const modelCompare = aNorm.localeCompare(bNorm);
    if (modelCompare !== 0) return modelCompare;
    const aFilename =
      a.filename || (a.model_path ? a.model_path.split("/").pop() : a.model);
    const bFilename =
      b.filename || (b.model_path ? b.model_path.split("/").pop() : b.model);
    return aFilename.localeCompare(bFilename);
  });

  const html = filtered
    .map((r) => {
      const running = r.status && r.status.startsWith("running");
      const starting = r.status === "starting";
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
          ')">▶ Resume</button>';
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

      return (
        '<div class="tile ' +
        (running ? "running" : "") +
        '" data-model="' +
        jsEscape(r.model) +
        '" onclick="selectModel(\'' +
        jsEscape(r.model) +
        "\')">" +
        '<div class="tile-header"><b>' +
        jsEscape(r.model) +
        "</b>" +
        statusChip +
        "</div>" +
        '<div class="tile-body"><code title="' +
        jsEscape(r.model_path) +
        '">' +
        jsEscape(r.model_path ? r.model_path.split("/").pop() : r.filename) +
        "</code>" +
        "<div>" +
        fmtGb(r.file_size_gb) +
        " · " +
        (r.status || "-") +
        (r.pid ? " · pid " + r.pid : "") +
        "</div>" +
        "<div>" +
        portCell +
        "</div>" +
        "</div>" +
        (r.comment ? `<div class="tile-rating">
          <div class="rating-row speed">
            <span class="rating-label">Speed</span>
            <span class="rating-stars">
              <span class="star ${r.rating_speed >= 1 ? 'filled' : ''}">★</span>
              <span class="star ${r.rating_speed >= 2 ? 'filled' : ''}">★</span>
              <span class="star ${r.rating_speed >= 3 ? 'filled' : ''}">★</span>
              <span class="star ${r.rating_speed >= 4 ? 'filled' : ''}">★</span>
              <span class="star ${r.rating_speed >= 5 ? 'filled' : ''}">★</span>
            </span>
          </div>
          <div class="rating-row coding">
            <span class="rating-label">Coding</span>
            <span class="rating-stars">
              <span class="star ${r.rating_coding >= 1 ? 'filled' : ''}">★</span>
              <span class="star ${r.rating_coding >= 2 ? 'filled' : ''}">★</span>
              <span class="star ${r.rating_coding >= 3 ? 'filled' : ''}">★</span>
              <span class="star ${r.rating_coding >= 4 ? 'filled' : ''}">★</span>
              <span class="star ${r.rating_coding >= 5 ? 'filled' : ''}">★</span>
            </span>
          </div>
        </div>` : "") +
        '<div class="tile-actions actions">' +
        dlHtml +
        ' <button class="run" onclick="act(\'' +
        jsEscape(r.model) +
        "', 'start')\" " +
        (running || starting || !r.file_exists ? "disabled" : "") +
        ">▶ Start</button>" +
        ' <button class="stop" onclick="act(\'' +
        jsEscape(r.model) +
        "', 'stop')\" " +
        (!running && !starting ? "disabled" : "") +
        ">■ Stop</button>" +
        ' <button class="add" onclick="downloadModel(\'' +
        jsEscape(r.model) +
        "', true)\">＋ Add Variant</button>" +
        ' <button class="del" onclick="promptDeleteModel(\'' +
        jsEscape(r.model) +
        "')\">✕ Delete</button>" +
        (running
          ? ' <button class="chat" onclick="event.stopPropagation(); openChatModal(\'' +
            jsEscape(r.model) +
            "')\">💬 Chat</button>"
          : "") +
        "</div>" +
        progressHtml +
        "</div>"
      );
    })
    .join("");

  grid.innerHTML = html;
}

/**
 * Render the properties panel for a model
 * @param {Object} data - Model data row
 * @param {string} modelName - Model name
 */
async function renderPanelProperties(data, modelName) {
  const content = document.getElementById("panel-content");
  if (!content) return;

  const running = data.status && data.status.startsWith("running");
  const modelSafeId = safeId(modelName);

  // Store original values for cancel functionality
  window.originalModelMeta = { ...data };

  let html = "";

  // Action buttons (Start/Stop/Optimize) at the top
  html += `<div class="panel-section">
    <div class="panel-item full-width" style="display: flex; justify-content: flex-end; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap;">
      <button type="button" class="btn-primary" id="start-model-${modelSafeId}" style="padding: 0.5rem 1rem;" ${running || data.status === "starting" ? "disabled" : ""}>
        ▶ Start
      </button>
      <button type="button" class="btn-danger" id="stop-model-${modelSafeId}" style="padding: 0.5rem 1rem;" ${!running ? "disabled" : ""}>
        ■ Stop
      </button>
      <button type="button" class="btn-primary" id="optimize-settings-${modelSafeId}" style="padding: 0.5rem 1rem;">
        🔧 Optimize Settings for Hardware
      </button>
      <span id="optimize-status-${modelSafeId}" style="align-self: center; font-size: 0.85rem; color: var(--muted);"></span>
    </div>
  </div>`;

// Basic Configuration
  html += `<div class="panel-section">
    <h3>Basic Configuration</h3>
    <div class="panel-grid">`;
    
    const addItem = (label, value, isCode = false, isHtml = false) => {
      const escapedLabel = he(label);
      let escapedValue;
      if (isHtml) {
        escapedValue = value;
      } else if (isCode) {
        escapedValue = `<code>${he(value)}</code>`;
      } else {
        escapedValue = he(value);
      }
      html += `<div class="panel-item">
        <span class="label">${escapedLabel}</span>
        <span class="value">${escapedValue}</span>
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
    ) => {
      let input;
      const escapedLabel = he(label);
      const escapedValue = he(String(value));
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
        <span class="label">${escapedLabel}</span>
        <span class="value">${input}</span>
      </div>`;
    };

    addItem("Model Name", modelName);
    addEditable("Port", "port", data.port, "number", 1, 0);
    // Note: Hugging Face Repo, Filename, Expected Size are typically static/model info
    // These would come from data but aren't in the standard model metadata API
    // For now, we'll show them as static info if available
    if (data.repo) {
      addItem("Hugging Face Repo", data.repo);
    }
    if (data.filename) {
      addItem("Filename", data.filename);
    }
    if (data.file_size_gb) {
      addItem("Expected Size (GB)", fmtGb(data.file_size_gb));
    }
    
    html += `</div></div>`;

  // Performance & Threading
  html += `<div class="panel-section">
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
  html += `<div class="panel-section">
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
  html += `<div class="panel-section">
    <h3>GPU & Acceleration</h3>
    <div class="panel-grid">`;
    
    addEditable("GPU Layers (0=CPU only)", "gpu_layers", data.gpu_layers, "number", 1, 0);
    
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
    
    addEditable("SYCL (Intel GPU)", "sycl", data.sycl, "checkbox");
    addEditable("Group Attention", "grp_attn", data.grp_attn, "checkbox");
    addEditable("Group Attention N", "grp_attn_n", data.grp_attn_n, "number", 1, 1);
    
    html += `</div></div>`;

  // Context & RoPE/YaRN
  html += `<div class="panel-section">
    <h3>Context & RoPE / YaRN</h3>
    <div class="panel-grid">`;
    
    addEditable("Context Size", "ctx_size", data.ctx_size, "number", 1, 1);
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
    addEditable("YaRN Orig Context", "yarn_orig_ctx", data.yarn_orig_ctx, "number", 1, 0);
    addEditable("YaRN Ext Factor", "yarn_ext_factor", data.yarn_ext_factor, "number", 0.1);
    addEditable("YaRN Attn Factor", "yarn_attn_factor", data.yarn_attn_factor, "number", 0.1, 0.1);
    addEditable("YaRN Beta Slow", "yarn_beta_slow", data.yarn_beta_slow, "number", 0.1, 0.1);
    addEditable("YaRN Beta Fast", "yarn_beta_fast", data.yarn_beta_fast, "number", 0.1, 0.1);
    
    html += `</div></div>`;

  // KV Cache
  html += `<div class="panel-section">
    <h3>KV Cache</h3>
    <div class="panel-grid">`;
    
    // KV Cache Type K dropdown
    const kvCacheKVal = data.cache_type_k == null ? "fp16" : data.cache_type_k;
    let kvCacheKSelect = `<select class="meta-input" data-field="cache_type_k">`;
    ["fp16", "fp8", "q4_0", "q4_1", "q5_0", "q5_1", "q8_0", "dq1_5", "dq2_5", "iq2_XXS", "iq2_xs", "iq3_xl", "xq4_0", "xq8_0", "xxq3_2", "xxq4_0", "yyxq3_2", "xxq4_0_sfp"].forEach((val) => {
      kvCacheKSelect += `<option value="${val}" ${kvCacheKVal === val ? "selected" : ""}>${val}</option>`;
    });
    kvCacheKSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">KV Cache Type K</span>
      <span class="value">${kvCacheKSelect}</span>
    </div>`;
    
    // KV Cache Type V dropdown
    const kvCacheVVal = data.cache_type_v == null ? "fp16" : data.cache_type_v;
    let kvCacheVSelect = `<select class="meta-input" data-field="cache_type_v">`;
    ["fp16", "fp8", "q4_0", "q4_1", "q5_0", "q5_1", "q8_0", "dq1_5", "dq2_5", "iq2_XXS", "iq2_xs", "iq3_xl", "xq4_0", "xq8_0", "xxq3_2", "xxq4_0", "yyxq3_2", "xxq4_0_sfp"].forEach((val) => {
      kvCacheVSelect += `<option value="${val}" ${kvCacheVVal === val ? "selected" : ""}>${val}</option>`;
    });
    kvCacheVSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">KV Cache Type V</span>
      <span class="value">${kvCacheVSelect}</span>
    </div>`;
    
    html += `</div></div>`;

  // Speculative Decoding
  html += `<div class="panel-section">
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
    
    addEditable("Draft Model Path", "spec_draft_model", data.spec_draft_model, "text");
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
    
    // Warmup settings
    addEditable("Enable Warmup", "warmup_enabled", data.warmup_enabled, "checkbox");
    addEditable("Warmup Message", "warmup_message", data.warmup_message, "text");
    addEditable("Warmup Max Tokens", "warmup_max_tokens", data.warmup_max_tokens, "number", 1, 1);
    
    html += `</div></div>`;

  // Agent/Tool Parameters
  html += `<div class="panel-section">
    <h3>Agent/Tool Parameters</h3>
    <div class="panel-grid">`;
    
    addEditable("Jinja Tool Parsing", "jinja", data.jinja, "checkbox");
    addEditable("Reasoning Preserve", "reasoning_preserve", data.reasoning_preserve, "checkbox");
    addEditable("Auto-download", "auto_download", data.auto_download, "checkbox");
    
    html += `</div></div>`;

// Save/Cancel buttons
   html += `<div class="panel-item full-width" style="display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--surface2);">
     <button type="button" class="btn-cancel" id="meta-cancel-${modelSafeId}" style="padding: 0.5rem 1rem;">Cancel</button>
     <button type="button" class="btn-primary" id="meta-save-${modelSafeId}" style="padding: 0.5rem 1rem;">Save</button>
   </div>`;

  html += `</div></div>`;

  // Running Statistics section
  if (running) {
    html += `<div class="panel-section">
      <h3>Running Statistics</h3>
      <div class="panel-grid">`;
    addItem("Status", '<span class="chip ok">● running</span>', false, true);
    if (data.pid) addItem("PID", `<code>${data.pid}</code>`, false, true);
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

  // User Ratings & Comments section
  html += `<div class="panel-section">
    <h3>User Ratings & Comments</h3>
    <div class="panel-grid">`;
  
  // Speed rating (1-5 stars) with editable input
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
    <input type="number" min="1" max="5" value="${speedVal || 0}" 
      class="meta-input" data-field="rating_speed" 
      style="width: 50px; text-align: center;" title="Speed rating">
  </div>`;
  
  // Coding rating (1-5 stars) with editable input
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
    <input type="number" min="1" max="5" value="${codingVal || 0}" 
      class="meta-input" data-field="rating_coding" 
      style="width: 50px; text-align: center;" title="Coding rating">
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
     <h3>Kilo Code Integration</h3>
     <div class="panel-item full-width">
       <button id="generate-config-${modelSafeId}" class="btn-primary">📋 Generate Kilo Code Config</button>
       <p style="margin-top: 0.5rem; font-size: 0.85rem; color: var(--text2);">
         Generate a provider configuration for Kilo Code based on this model's settings.
       </p>
     </div>
   </div>`;

  content.innerHTML = html;

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
          "warmup_max_tokens"
        ];
        if (numericFields.includes(field)) {
          value = Number(value);
        }

        // Only include changed values
        const originalValue = window.originalModelMeta?.[field];
        // For parallel field, compare as strings since it can be "auto" or numeric
        const isChanged = field === "parallel" 
          ? String(value) !== String(originalValue)
          : value !== originalValue;
        if (isChanged) {
          payload[field] = value;
          hasChanges = true;
        }
      });

      if (!hasChanges) {
        alert("No changes to save");
        return;
      }

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
          // Update original values
          Object.assign(window.originalModelMeta, payload);
          alert("Changes saved successfully");
        }
      } catch (err) {
        console.error("Failed to update meta", err);
        alert("Network error while saving");
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = "Save";
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

  // Attach generate config button listener
  const configBtn = document.getElementById(`generate-config-${modelSafeId}`);
  if (configBtn) {
    configBtn.addEventListener("click", () => {
      openKiloCodeModal(data, modelName);
    });
  }

  // Attach optimize settings button listener
  const optimizeBtn = document.getElementById(`optimize-settings-${modelSafeId}`);
  if (optimizeBtn) {
    optimizeBtn.addEventListener("click", async () => {
      const statusEl = document.getElementById(`optimize-status-${modelSafeId}`);
      optimizeBtn.disabled = true;
      optimizeBtn.textContent = "🔧 Optimizing…";
      if (statusEl) {
        statusEl.textContent = "Detecting hardware and computing optimal settings…";
        statusEl.style.color = "var(--blue)";
      }

      try {
        const resp = await fetch("/api/optimize-settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: modelName }),
        });
        const result = await resp.json();
        if (!resp.ok) {
          throw new Error(result.error || "Failed to optimize settings");
        }

        const settings = result.optimal_settings;
        const hardware = result.hardware;

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

        if (statusEl) {
          statusEl.textContent = `Applied ${updatedCount} optimal settings based on: ${hardware.cpu_count_logical} CPUs, ${hardware.memory_total_gb}GB RAM${hardware.total_gpu_vram_gb ? `, ${hardware.total_gpu_vram_gb}GB GPU VRAM` : ""}${hardware.has_nvidia_gpu ? " (NVIDIA)" : ""}${hardware.has_amd_gpu ? " (AMD)" : ""}`;
          statusEl.style.color = "var(--green)";
        }
        alert(`Optimization complete! Applied ${updatedCount} settings. Review and click Save to persist.`);
      } catch (err) {
        console.error("Optimization failed", err);
        if (statusEl) {
          statusEl.textContent = `Error: ${err.message}`;
          statusEl.style.color = "var(--red)";
        }
        alert(`Optimization failed: ${err.message}`);
      } finally {
        optimizeBtn.disabled = false;
        optimizeBtn.textContent = "🔧 Optimize Settings for Hardware";
      }
    });
  }

  // Attach start model button listener
  const startBtn = document.getElementById(`start-model-${modelSafeId}`);
  if (startBtn) {
    startBtn.addEventListener("click", async () => {
      startBtn.disabled = true;
      startBtn.textContent = "▶ Starting…";
      
      try {
        const resp = await fetch("/api/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: modelName }),
        });
        const result = await resp.json();
        if (!resp.ok) {
          throw new Error(result.error || "Failed to start model");
        }
        
        // Refresh the panel after starting
        selectModel(modelName);
      } catch (err) {
        console.error("Start failed", err);
        alert(`Start failed: ${err.message}`);
      } finally {
        startBtn.disabled = false;
        startBtn.textContent = "▶ Start";
      }
    });
  }

  // Attach stop model button listener
  const stopBtn = document.getElementById(`stop-model-${modelSafeId}`);
  if (stopBtn) {
    stopBtn.addEventListener("click", async () => {
      if (!confirm(`Stop model "${modelName}"?`)) return;
      
      stopBtn.disabled = true;
      stopBtn.textContent = "■ Stopping…";
      
      try {
        const resp = await fetch("/api/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: modelName }),
        });
        const result = await resp.json();
        if (!resp.ok) {
          throw new Error(result.error || "Failed to stop model");
        }
        
        // Refresh the panel after stopping
        selectModel(modelName);
      } catch (err) {
        console.error("Stop failed", err);
        alert(`Stop failed: ${err.message}`);
      } finally {
        stopBtn.disabled = false;
        stopBtn.textContent = "■ Stop";
      }
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
