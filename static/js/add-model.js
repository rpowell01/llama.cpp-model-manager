/**
 * Add Model form handling and model search
 */

/**
 * Toggle add model form visibility
 */
function toggleAddForm() {
  const form = document.getElementById('add-model-form');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
  if (form.style.display === 'block') {
    document.getElementById('modelName').focus();
  }
}

/**
 * Show add model form
 */
function showAddForm() {
  const form = document.getElementById('add-model-form');
  form.style.display = 'block';
  document.getElementById('modelName').focus();
}

/**
 * Prompt to add a model
 * @param {string} existingModel - Optional pre-filled model name
 */
async function promptAddModel(existingModel) {
  showAddForm();
}

/**
 * Prompt to delete a model
 * @param {string} model - Model name to delete
 */
async function promptDeleteModel(model) {
  if (!confirm(`Delete model "${model}"? This will remove it from the list.\nCheck "Delete files" to also remove the GQUF file.`)) {
    return;
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
      const formData = new FormData(e.target);
      const data = {};
      formData.forEach((value, key) => {
        if (key === 'mlock' || key === 'no_mmap' || key === 'auto_download') {
          data[key] = formData.get(key) === 'on';
        } else if (['expected_size', 'port', 'cache_size', 'threads', 'threads_batch', 'timeout', 'parallel', 'ubatch_size'].includes(key)) {
          data[key] = parseInt(value, 10);
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
          toggleAddForm();
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
          <div data-model="${r.modelId}" data-filename="${r.filename}" data-size="${r.size}">
            <strong>${r.modelId}</strong><br>
            <small>Downloads: ${r.downloads.toLocaleString()} • Likes: ${r.likes}</small>
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
              } else {
                // Show variant picker modal
                showDownloadModal(modelId, data.files, false, function (url, size, filename) {
                  document.querySelector('input[name="filename"]').value = filename;
                  document.querySelector('input[name="expected_size"]').value = size;
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
