/**
 * Add Model form handling and model search
 */

/**
 * Generate a unique model name from filename
 * @param {string} filename - The GGUF filename
 */
function generateModelNameFromFilename(filename) {
  // Remove .gguf extension and convert to display name
  let baseName = filename;
  if (baseName.toLowerCase().endsWith('.gguf')) {
    baseName = baseName.slice(0, -5);
  }
  // Convert underscores/hyphens to spaces for readability
  let modelName = baseName.replace(/[_-]/g, ' ');
  
  const nameInput = document.getElementById('modelName');
  if (nameInput) {
    nameInput.value = modelName;
  }
}

/**
 * Compute and set an available port in the port input field
 */
async function computeAndSetPort() {
  const portInput = document.querySelector('input[name="port"]');
  if (!portInput) return;
  
  try {
    const response = await fetch('/api/next-port');
    const data = await response.json();
    portInput.value = data.port;
  } catch (err) {
    console.error('Failed to get next port:', err);
    // Default to 11000 if fetch fails
    portInput.value = 11000;
  }
}

/**
 * Validate that the port is not already in use
 * @param {number} port - Port number to validate
 * @returns {Promise<boolean>} - True if port is available
 */
async function validatePort(port) {
  const data = await (await fetch('/api/status')).json();
  const models = data.models;
  const usedPorts = models.map(m => parseInt(m.port, 10));
  return !usedPorts.includes(port);
}

/**
 * Open the Add Model modal dialog
 */
function openAddModelModal() {
  ModalManager.open('add-model-modal');
  computeAndSetPort();
}

/**
 * Prompt to add a model
 * @param {string} existingModel - Optional pre-filled model name
 */
async function promptAddModel(existingModel) {
  openAddModelModal();
  computeAndSetPort();
}

/**
 * Prompt to delete a model
 * @param {string} model - Model name to delete
 */
async function promptDeleteModel(model) {
  if (!confirm(`Delete model "${model}"? This will remove it from the list.\nCheck "Delete files" to also remove the GQUF file.`)) {
    return;
  }
  // Clear any starting interval for this model to prevent memory leaks
  if (startingIntervals[model]) {
    clearInterval(startingIntervals[model]);
    delete startingIntervals[model];
  }
  const deleteFiles = confirm(`Also delete the GQUF file on disk?`);
  try {
    const res = await fetch('/api/delete-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, delete_files: deleteFiles })
    });
    const data = await res.json();
    if (data.error) {
      alert('Error: ' + data.error);
    } else {
      log(`<b>Delete:</b> ${model} → ${data.status}`, 'green');
      refresh();
    }
  } catch (e) {
    log('<b>Error:</b> ' + e, 'red');
  }
}

/**
 * Edit port inline from tile
 * @param {string} modelName - Model name
 * @param {number} currentPort - Current port number
 */
function editPortInline(modelName, currentPort) {
  const newPort = prompt('Enter port for ' + modelName + ':', currentPort);
  if (newPort === null) return; // Cancelled
  const portNum = parseInt(newPort, 10);
  if (isNaN(portNum) || portNum < 1 || portNum > 65535) {
    alert('Invalid port number. Must be between 1 and 65535.');
    return;
  }
  fetch('/api/update-model-meta', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: modelName, port: portNum })
  }).then(() => refresh());
}

// Add form submission handler
document.addEventListener('DOMContentLoaded', function () {
  const addForm = document.getElementById('addForm');
  if (addForm) {
    addForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      
      // Validate port
      const portInput = document.querySelector('input[name="port"]');
      const port = parseInt(portInput.value, 10);
      if (isNaN(port) || port < 1 || port > 65535) {
        alert('Invalid port number. Must be between 1 and 65535.');
        return;
      }
      if (!await validatePort(port)) {
        alert(`Port ${port} is already in use by another model. Please choose a different port.`);
        return;
      }
      
const formData = new FormData(e.target);
        const data = {};
        formData.forEach((value, key) => {
          if (key === 'mlock' || key === 'no_mmap' || key === 'auto_download' || key === 'numa' || key === 'cpu_strict' || key === 'swa_full') {
            data[key] = formData.get(key) === 'on';
          } else if (['expected_size', 'port', 'threads', 'threads_batch', 'timeout', 'parallel', 'ubatch_size', 'gpu_layers', 'batch_size', 'ctx_size', 'cache_reuse'].includes(key)) {
            data[key] = parseInt(value, 10);
          } else if (['rope_freq_base', 'rope_freq_scale'].includes(key)) {
            data[key] = parseFloat(value);
          } else if (key === 'spec_type') {
            // Handle multi-select for spec_type - get all selected values and join with commas
            const values = formData.getAll(key);
            data[key] = values.length > 0 ? values.join(',') : 'none';
          } else {
            data[key] = value;
          }
        });
      const submitBtn = e.target.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      submitBtn.textContent = 'Adding…';
      try {
        const res = await fetch('/api/add-model', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data)
        });
        const result = await res.json();
        if (result.error) {
          log('<b>Add error:</b> ' + result.error, 'red');
          alert('Error: ' + result.error);
        } else {
          log(`<b>Add:</b> ${data.name} → ${result.status}`, 'green');
          // Auto-download the model after successful addition if checkbox is checked
          if (data.auto_download) {
            const downloadUrl = `https://huggingface.co/${data.repo}/resolve/main/${data.filename}`;
            _triggerDownload(data.name, downloadUrl, data.expected_size, false);
          }
          ModalManager.close('add-model-modal');
          e.target.reset();
          refresh();
        }
      } catch (err) {
        log('<b>Error:</b> ' + err, 'red');
        alert('Error: ' + err);
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Add Model';
      }
    });
  }

  // Register cleanup handler for modal close
  ModalManager.register('add-model-modal', () => {
    // Reset form when modal closes
    const form = document.getElementById('addForm');
    if (form) {
      form.reset();
    }
  });

  // Search button handler
  const searchBtn = document.getElementById('searchBtn');
  if (searchBtn) {
    searchBtn.addEventListener('click', async function () {
      const query = document.getElementById('modelName').value.trim();
      if (!query) {
        alert('Please enter a model name to search');
        return;
      }
      const btn = this;
      btn.disabled = true;
      btn.textContent = 'Searching…';
      try {
        const res = await fetch('/api/search-models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query })
        });
        const results = await res.json();
        const resultsDiv = document.getElementById('search-results');
        if (results.length === 0) {
          resultsDiv.innerHTML = '<div style="padding: 0.5rem; color: var(--text2);">No models found</div>';
          resultsDiv.style.display = 'block';
          return;
        }
         resultsDiv.innerHTML = results.map(r => `
           <div data-model="${he(r.modelId)}" data-filename="${he(r.filename)}" data-size="${r.size}">
             <strong>${he(r.modelId)}</strong><br>
             <small>Downloads: ${r.downloads.toLocaleString()} • Likes: ${r.likes.toLocaleString()}</small>
           </div>
          `).join('');
          resultsDiv.style.display = 'block';

        // Add click handlers to results
        resultsDiv.querySelectorAll('div').forEach(div => {
          div.addEventListener('click', async function () {
            const modelId = this.getAttribute('data-model');
            // Hide results and reset search button state
            resultsDiv.style.display = 'none';
            btn.disabled = false;
            btn.textContent = '🔍 Search';

            // Fill model name and repo
            document.getElementById('modelName').value = modelId.split('/').pop() || modelId;
            document.querySelector('input[name="repo"]').value = modelId;

            // Suggest a port (find next available port starting from 11000)
            try {
              const portResp = await fetch('/api/next-port');
              const portData = await portResp.json();
              document.querySelector('input[name="port"]').value = portData.port;
            } catch (portErr) {
              console.error('Failed to get next port:', portErr);
              // Default to 11000 if fetch fails
              document.querySelector('input[name="port"]').value = 11000;
            }

            try {
              // Fetch all GQUF files for the selected repo
              const resp = await fetch('/api/hf-repo-files', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo: modelId })
              });
              const data = await resp.json();
              if (data.error || !data.files || data.files.length === 0) {
                alert('No GQUF files found for this repository');
                return;
              }

              // If only one variant, select it automatically
              if (data.files.length === 1) {
                const file = data.files[0];
                document.querySelector('input[name="filename"]').value = file.filename;
                document.querySelector('input[name="expected_size"]').value = file.size;
                // Generate model name from variant filename
                generateModelNameFromFilename(file.filename);
              } else {
                // Show variant picker modal
                showDownloadModal(modelId, data.files, false, function (url, size, filename) {
                  document.querySelector('input[name="filename"]').value = filename;
                  document.querySelector('input[name="expected_size"]').value = size;
                  // Generate unique model name from variant filename
                  generateModelNameFromFilename(filename);
                }, `Select variant for ${modelId.split('/').pop() || modelId}`);
              }
            } catch (err) {
              alert('Error fetching variants: ' + err);
            }
          });
        });
      } catch (err) {
        log('<b>Search error:</b> ' + err, 'red');
        alert('Search failed: ' + err);
      } finally {
        btn.disabled = false;
        btn.textContent = '🔍 Search';
      }
    });
  }

  // Debounce search input to avoid excessive requests
  const searchInput = document.getElementById('search');
  if (searchInput) {
    let searchTimeout;
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => refresh(false), 300);
    });
  }
});
