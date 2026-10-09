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
        "\')\"" +
        (running && r.port ? 
          ' onmouseenter="showMetricsTooltip(this, \'' + jsEscape(r.model) + '\', ' + r.port + ')"' +
          ' onmouseleave="hideMetricsTooltip(this)"' : '') +
        '>' +
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
        `<div class="tile-rating">
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
        </div>` +
        (r.comment ? `<div class="tile-comment">${he(r.comment)}</div>` : "") +
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
            "')\">💬 Chat</button>" +
            ' <button class="export" onclick="event.stopPropagation(); exportMetrics(\'' +
            jsEscape(r.model) +
            '\', ' + r.port + ')\" title="Export metrics to CSV">📊 Export</button>'
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
        🔧 Optimize Settings (Empirical Benchmarks)
      </button>
      <button type="button" class="btn-secondary" id="quick-optimize-${modelSafeId}" style="padding: 0.5rem 1rem;">
        ⚡ Quick Optimize (Heuristics)
      </button>
      <button type="button" class="btn-secondary" id="view-history-${modelSafeId}" style="padding: 0.5rem 1rem;">
        📊 View Historical Benchmark Results
      </button>
    </div>
    <div class="panel-item full-width" style="margin-top: 0.5rem;">
      <div style="display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; font-size: 0.85rem;">
        <label style="display: flex; align-items: center; gap: 0.35rem; cursor: pointer;">
          <span>Max benchmark duration:</span>
          <input type="number" id="max-duration-${modelSafeId}" value="300" min="30" max="3600" step="10" style="width: 70px;">
          <span>seconds</span>
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
    </div>`;

// Basic Configuration
  html += `<div class="panel-section">
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

    /**
     * Add an editable field with a browse button for file/directory selection
     * @param {string} label - Field label
     * @param {string} field - Field name
     * @param {string} value - Current value
     * @param {string} browseType - Type of browse: 'file', 'directory', 'model-file'
     */
    const addEditableWithBrowse = (
      label,
      field,
      value,
      browseType = "file"
    ) => {
      const escapedLabel = he(label);
      const escapedValue = he(String(value));
      let browseBtn;
      
      if (browseType === "file") {
        browseBtn = `<button type="button" class="browse-btn" data-field="${he(field)}" data-browse-type="file">📁</button>`;
      } else if (browseType === "directory") {
        browseBtn = `<button type="button" class="browse-btn" data-field="${he(field)}" data-browse-type="directory">📁</button>`;
      } else if (browseType === "model-file") {
        browseBtn = `<button type="button" class="browse-btn" data-field="${he(field)}" data-browse-type="model-file">📁</button>`;
      }
      
      html += `<div class="panel-item">
        <span class="label">${he(label)}</span>
        <span class="value" style="display: flex; gap: 0.25rem; align-items: center;">
          <input type="text" class="meta-input" data-field="${he(field)}" value="${escapedValue}" style="flex: 1;">
          ${browseBtn}
        </span>
      </div>`;
    };

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
            } 
            // Fallback to File System Access API (modern browsers)
            // Note: This API only provides file handles, not full paths due to security restrictions
            else if ('showDirectoryPicker' in window || 'showOpenFilePicker' in window) {
              if (browseType === "directory" && 'showDirectoryPicker' in window) {
                const dirHandle = await window.showDirectoryPicker();
                result = { canceled: false, filePaths: [dirHandle.name] }; // Note: only name available
              } else if ((browseType === "file" || browseType === "model-file") && 'showOpenFilePicker' in window) {
                const options = browseType === "model-file" 
                  ? { types: [{ description: "GGUF Files", accept: { "model/gguf": [".gguf"] } }] }
                  : { types: [{ description: "All Files", accept: { "*/*": ["*"] } }] };
                const fileHandles = await window.showOpenFilePicker(options);
                if (fileHandles.length > 0) {
                  const file = await fileHandles[0].getFile();
                  result = { canceled: false, filePaths: [file.name] }; // Note: only name available
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
              }
            }
            
            if (result && !result.canceled && result.filePaths && result.filePaths.length > 0) {
              input.value = result.filePaths[0];
            }
          } catch (err) {
            console.error("Browse error:", err);
            log('<b>Browse error:</b> ' + err, 'red');
          }
        });
      });
    }

    addItem("Model Name", modelName);
    addEditable("Port", "port", data.port, "number", 1, 0);
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
    
addEditableWithBrowse("Slot Save Path", "slot_save_path", data.slot_save_path, "directory");
    addEditable("Cache Reuse (tokens)", "cache_reuse", data.cache_reuse, "number", 1, 1);
    addEditable("SWA Full (for SWA models)", "swa_full", data.swa_full, "checkbox");
    
    // Chat template file with browse button
    addEditableWithBrowse("Chat Template File", "chat_template_file", data.chat_template_file, "file");
    
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
    
    // Ngram-mod settings
    addEditable("Ngram-mod N Match", "spec_ngram_mod_n_match", data.spec_ngram_mod_n_match, "number", 1, 0);
    addEditable("Ngram-mod N Min", "spec_ngram_mod_n_min", data.spec_ngram_mod_n_min, "number", 1, 0);
    addEditable("Ngram-mod N Max", "spec_ngram_mod_n_max", data.spec_ngram_mod_n_max, "number", 1, 0);
    
    // Draft model KV cache types
    const draftKCacheVal = data.spec_draft_type_k == null ? "fp16" : data.spec_draft_type_k;
    let draftKCacheSelect = `<select class="meta-input" data-field="spec_draft_type_k">`;
    ["fp16", "q4_0", "q8_0"].forEach((val) => {
      draftKCacheSelect += `<option value="${val}" ${draftKCacheVal === val ? "selected" : ""}>${val}</option>`;
    });
    draftKCacheSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">Draft KV Cache Type K</span>
      <span class="value">${draftKCacheSelect}</span>
    </div>`;
    
    const draftVCacheVal = data.spec_draft_type_v == null ? "fp16" : data.spec_draft_type_v;
    let draftVCacheSelect = `<select class="meta-input" data-field="spec_draft_type_v">`;
    ["fp16", "q4_0", "q8_0"].forEach((val) => {
      draftVCacheSelect += `<option value="${val}" ${draftVCacheVal === val ? "selected" : ""}>${val}</option>`;
    });
    draftVCacheSelect += `</select>`;
    html += `<div class="panel-item">
      <span class="label">Draft KV Cache Type V</span>
      <span class="value">${draftVCacheSelect}</span>
    </div>`;
    
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
    
    // Custom HTTP Header for Kilo Code Config
    addEditable("Custom Header Key", "custom_header_key", data.custom_header_key || "", "text");
    addEditable("Custom Header Value", "custom_header_value", data.custom_header_value || "", "text");
    
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

  // Initialize browse buttons
  initBrowseButtons(content);

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
          // Update original values
          Object.assign(window.originalModelMeta, payload);
          
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

  // Attach generate config button listener
  const configBtn = document.getElementById(`generate-config-${modelSafeId}`);
  if (configBtn) {
    configBtn.addEventListener("click", () => {
      openKiloCodeModal(data, modelName);
    });
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
    btn.textContent = useEmpirical ? "🔧 Benchmarking…" : "⚡ Computing…";
    
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
      cancelBtn.onclick = () => {
        isCancelled = true;
        abortController.abort();
        progressText.textContent = "Cancelling...";
        cancelBtn.disabled = true;
        cancelBtn.textContent = "Cancelling...";
      };
    }

    try {
      // Determine which endpoint to call
      const endpoint = useEmpirical ? "/api/benchmark-optimize" : "/api/optimize-settings";
      
      // Build request body
      const requestBody = { model: modelName };
      if (useEmpirical) {
        requestBody.max_duration_sec = parseInt(maxDurationInput.value, 10) || 60;
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
            // Full optimization sweep - simulate 12 configs
            const configNum = Math.min(12, Math.max(1, Math.floor(progressPct / 8) + 1));
            const gpuLayers = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 99, 99][configNum - 1] || 99;
            const batchSize = [512, 512, 1024, 1024, 2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048][configNum - 1] || 2048;
            
            progressText.textContent = `Running benchmarks... (${Math.floor(elapsed)}s / ~${estimatedTotal}s)`;
            detailsEl.textContent = `Testing config ${configNum}/12: gpu_layers=${gpuLayers}, batch=${batchSize}...`;
            
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
      btn.textContent = useEmpirical ? "🔧 Optimize Settings (Empirical Benchmarks)" : "⚡ Quick Optimize (Heuristics)";
      
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
      btn.textContent = "📊 Loading…";
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
        btn.textContent = "📊 View Historical Benchmark Results";
      }
    }
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
    if (metricsTooltip && metricsTooltipContext) {
      await updateTooltipContent(metricsTooltip, metricsTooltipContext.modelName, metricsTooltipContext.port, true);
    }
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
 * Export model metrics to CSV
 * @param {string} modelName - Model name
 * @param {number} port - Model port
 */
async function exportMetrics(modelName, port) {
  try {
    const btn = event.target;
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Exporting...';
    
    const res = await fetch('/api/export-metrics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelName })
    });
    
    const data = await res.json();
    if (data.error) {
      log('<b>Export error:</b> ' + data.error, 'red');
    } else {
      log(`<b>Export:</b> ${modelName} → ${data.csv_path}`, 'green');
    }
    
    btn.disabled = false;
    btn.textContent = originalText;
  } catch (e) {
    log('<b>Export error:</b> ' + e, 'red');
    const btn = event.target;
    if (btn) {
      btn.disabled = false;
      btn.textContent = '📊 Export';
    }
  }
}

// Expose functions globally for use in model-manager.js
window.showMetricsTooltip = showMetricsTooltip;
window.hideMetricsTooltip = hideMetricsTooltip;
window.exportMetrics = exportMetrics;
