/**
 * Download management - model downloads, progress tracking, modals
 */

/**
 * Show download selection modal
 * @param {string} model - Model name
 * @param {Array} results - Download results
 * @param {boolean} force - Force download
 * @param {Function} onSelect - Optional callback on selection
 * @param {string} customTitle - Optional custom title
 */
function showDownloadModal(model, results, force = false, onSelect = null, customTitle = '') {
  const modal = document.getElementById('download-modal');
  const modelNameText = document.getElementById('dl-modal-model-name');
  const resultsDiv = document.getElementById('dl-search-results');

  modelNameText.textContent = customTitle || 'Select a file to download:';
  resultsDiv.innerHTML = results.map(r => `
    <div data-url="${he(r.url)}" data-size="${r.size}" data-filename="${he(r.filename)}" style="padding:.5rem; cursor:pointer; border-bottom:1px solid var(--surface2);">
      <strong>${he(r.filename)}</strong><br>
      <small>Size: ${fmtSize(r.size)} • Downloads: ${r.downloads.toLocaleString()} • Likes: ${r.likes.toLocaleString()}</small>
    </div>
  `).join('');
  resultsDiv.style.display = 'block';

  // Click handler on results
  resultsDiv.querySelectorAll('div').forEach(div => {
    div.addEventListener('click', function () {
      const url = this.getAttribute('data-url');
      const size = parseInt(this.getAttribute('data-size')) || 0;
      const filename = this.getAttribute('data-filename') || '';
      ModalManager.close('download-modal');
      if (typeof onSelect === 'function') {
        onSelect(url, size, filename);
      } else {
        _triggerDownload(model, url, size, force);
      }
    });
  });

  // Cancel handler
  const cancelBtn = document.getElementById('dl-modal-cancel');
  if (cancelBtn) {
    cancelBtn.onclick = () => {
      ModalManager.close('download-modal');
    };
  }

  // Open modal via ModalManager
  ModalManager.open('download-modal');
}

/**
 * Internal trigger download function
 * @param {string} model - Model name
 * @param {string} url - Download URL
 * @param {number} size - Expected size
 * @param {boolean} force - Force download
 */
async function _triggerDownload(model, url, size, force = false) {
  console.log('_triggerDownload called:', { model, url, size, force });

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, url, expected_size: size || 0, force })
    });
    const data = await res.json();
    console.log('download response:', data);

    if (data.error) {
      log('<b>Download error:</b> ' + data.error, 'red');
      return;
    }

    // Show appropriate message for variant vs regular download
    if (data.is_variant) {
      log(`<b>New variant created:</b> ${data.model}`, 'green');
    } else {
      log(`<b>Download:</b> ${model} starting...`, 'green');
    }

    // Show progress bar for active downloads
    showProgressBar(data.model, size || 0);

    // Inject progress bar HTML into tile DOM
     const safeModelId = safeId(data.model);
     const tile = document.querySelector('[data-model="' + data.model + '"]');
     if (tile) {
       const progressId = 'progress-' + safeModelId;
       if (!document.getElementById(progressId)) {
         const progressHtml = '<div id="' + progressId + '" class="download-progress" style="margin-top: .5rem; padding: .5rem; background: var(--surface2); border-radius: 4px;">'
           + '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: .25rem;">'
           + '<strong style="font-size: .85rem;">Download Progress</strong>'
           + '<span id="progress-text-' + safeModelId + '" style="font-size: .75rem;">0%</span>'
           + '</div>'
           + '<div style="background: var(--surface); border-radius: 4px; height: 6px; overflow: hidden;">'
           + '<div id="progress-bar-' + safeModelId + '" style="background: var(--btn-download); height: 100%; width: 0%; transition: width 0.2s;"></div>'
           + '</div>'
           + '<div id="progress-details-' + safeModelId + '" style="font-size: .7rem; color: var(--text2); margin-top: .25rem;"></div>'
           + '</div>';
         tile.insertAdjacentHTML('beforeend', progressHtml);
       }
     }

     if (data.status === 'complete') {
       log(`<b>${model}:</b> already downloaded (${fmtSize(data.size)})`, 'green');
       // Update progress bar to show complete
       setTimeout(() => {
         const safeModelId = safeId(data.model);
         const bar = document.getElementById('progress-bar-' + safeModelId);
         const text = document.getElementById('progress-text-' + safeModelId);
         const details = document.getElementById('progress-details-' + safeModelId);
         if (bar) bar.style.width = '100%';
         if (text) text.textContent = '100% (already downloaded)';
         if (details) details.textContent = 'Complete!';
       }, 100);
       return;
     }

    // Small delay to ensure progress bar is rendered before SSE starts
    await new Promise(resolve => setTimeout(resolve, 50));

    // Use data.model for progress listener (handles variant case)
    startProgressListener(data.model);
    refresh();
  } catch (e) {
    log('<b>Error:</b> ' + e, 'red');
  }
}

/**
 * Download a model (triggers HF search)
 * @param {string} model - Model name
 * @param {boolean} force - Force download
 */
async function downloadModel(model, force = false) {
  console.log('downloadModel called for:', model, 'force:', force);
  // First, search HF for the model using the known name
  try {
    const res = await fetch('/api/download-search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, force })
    });
    const data = await res.json();
    console.log('download-search response:', data);

    if (data.error) {
      log('<b>Download error:</b> ' + data.error, 'red');
      return;
    }

    // If already complete, show message
    if (data.status === 'complete') {
      log(`<b>${model}:</b> already downloaded (${fmtSize(data.size)})`, 'green');
      return;
    }

       // If download started automatically (single result), show progress
       if (data.status === 'download started') {
         log(`<b>Download:</b> ${model} started`, 'green');
         showProgressBar(model, data.results?.[0]?.size || 0);
         // Inject progress bar HTML into tile DOM
         const safeModelId = safeId(model);
         const tile = document.querySelector('[data-model="' + model + '"]');
         if (tile) {
           const progressId = 'progress-' + safeModelId;
           if (!document.getElementById(progressId)) {
             const progressHtml = '<div id="' + progressId + '" class="download-progress" style="margin-top: .5rem; padding: .5rem; background: var(--surface2); border-radius: 4px;">'
               + '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: .25rem;">'
               + '<strong style="font-size: .85rem;">Download Progress</strong>'
               + '<span id="progress-text-' + safeModelId + '" style="font-size: .75rem;">0%</span>'
               + '</div>'
               + '<div style="background: var(--surface); border-radius: 4px; height: 6px; overflow: hidden;">'
               + '<div id="progress-bar-' + safeModelId + '" style="background: var(--btn-download); height: 100%; width: 0%; transition: width 0.2s;"></div>'
               + '</div>'
               + '<div id="progress-details-' + safeModelId + '" style="font-size: .7rem; color: var(--text2); margin-top: .25rem;"></div>'
               + '</div>';
             tile.insertAdjacentHTML('beforeend', progressHtml);
           }
         }
      // Small delay to ensure progress bar is rendered before SSE starts
      setTimeout(() => {
        startProgressListener(model);
      }, 50);
      return;
    }

    // If multiple results, show modal for selection
    if (data.status === 'multiple_results') {
      console.log('Showing modal with', data.results.length, 'results');
      showDownloadModal(model, data.results, force);
    }
  } catch (err) {
    log('<b>Error:</b> ' + err, 'red');
  }
}

/**
 * Download by filename (for add model search results)
 * @param {string} model - Model name
 * @param {string} filename - Filename
 * @param {boolean} force - Force download
 */
async function downloadByFilename(model, filename, force = false) {
  console.log('downloadByFilename called for:', model, 'filename:', filename, 'force:', force);
  // Search HF for the specific filename
  try {
    const res = await fetch('/api/download-search-filename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, force })
    });
    const data = await res.json();
    console.log('download-search-filename response:', data);

    if (data.error) {
      log('<b>Download error:</b> ' + data.error, 'red');
      return;
    }

    // If single exact match, trigger download automatically
    if (data.status === 'single_result') {
      console.log('Single result found, starting download automatically');
      const result = data.results[0];
      _triggerDownload(model, result.url, result.size, force);
      return;
    }

    // If multiple results, show modal for selection
    if (data.status === 'multiple_results') {
      console.log('Showing modal with', data.results.length, 'results for filename:', filename);
      // Pass the model name (not filename) to the modal so the download uses the correct entry
      showDownloadModal(model, data.results, force);
    }
  } catch (err) {
    log('<b>Error:</b> ' + err, 'red');
  }
}

/**
 * Retry a failed download
 * @param {string} model - Model name
 * @param {boolean} resume - Whether to resume if possible
 */
async function retryDownload(model, resume = false) {
  // Check if there's an existing partial download
  const statusRes = await fetch('/api/download-status');
  const status = await statusRes.json();
  const dlStatus = status[model] || {};

  if (dlStatus.status === 'error' && resume && confirm(`Resume interrupted download for ${model}?`)) {
    downloadWithResume(model, true, dlStatus.downloaded || 0);
  } else if (dlStatus.status === 'error' && confirm(`Retry failed download for ${model}?`)) {
    downloadWithResume(model, false, 0, true); // Use force for retry
  } else {
    downloadWithResume(model, false, 0, true); // Use force for retry
  }
}

/**
 * Resume a partial download
 * @param {string} model - Model name
 * @param {number} partialBytes - Already downloaded bytes
 */
async function resumeDownload(model, partialBytes) {
  if (!confirm(`Resume download for ${model}? (${fmtSize(partialBytes)} already downloaded)`)) {
    return;
  }
  downloadWithResume(model, true, partialBytes);
}

/**
 * Download with resume capability
 * @param {string} model - Model name
 * @param {boolean} resume - Whether to resume
 * @param {number} partialBytes - Already downloaded bytes
 * @param {boolean} force - Force download
 */
async function downloadWithResume(model, resume, partialBytes = 0, force = false) {
  // Get the download URL
  const fileRes = await fetch('/api/model-file', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model })
  });
  const fileInfo = await fileRes.json();

  if (fileInfo.error) {
    log('<b>Error:</b> ' + fileInfo.error, 'red');
    return;
  }

  const response = await fetch('/api/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      url: fileInfo.url,
      expected_size: fileInfo.size || 0,
      resume: resume,
      force: force
    })
  });
  const data = await response.json();

  if (data.error) {
    log('<b>Resume error:</b> ' + data.error, 'red');
  } else {
    log(`<b>Download:</b> ${model} resumed`, 'green');
     showProgressBar(model, fileInfo.size || 0);
     // Inject progress bar HTML into tile DOM (fix for re-download visibility)
     const safeModelId = safeId(model);
     const tile = document.querySelector('[data-model="' + model + '"]');
     if (tile) {
       const progressId = 'progress-' + safeModelId;
       if (!document.getElementById(progressId)) {
         const progressHtml = '<div id="' + progressId + '" class="download-progress" style="margin-top: .5rem; padding: .5rem; background: var(--surface2); border-radius: 4px;">'
           + '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: .25rem;">'
           + '<strong style="font-size: .85rem;">Download Progress</strong>'
           + '<span id="progress-text-' + safeModelId + '" style="font-size: .75rem;">0%</span>'
           + '</div>'
           + '<div style="background: var(--surface); border-radius: 4px; height: 6px; overflow: hidden;">'
           + '<div id="progress-bar-' + safeModelId + '" style="background: var(--btn-download); height: 100%; width: 0%; transition: width 0.2s;"></div>'
           + '</div>'
           + '<div id="progress-details-' + safeModelId + '" style="font-size: .7rem; color: var(--text2); margin-top: .25rem;"></div>'
           + '</div>';
         tile.insertAdjacentHTML('beforeend', progressHtml);
       }
     }
    // Small delay to ensure progress bar is rendered before SSE starts
    await new Promise(resolve => setTimeout(resolve, 50));
    startProgressListener(model);
  }
}

/**
 * Show progress bar for a download
 * @param {string} model - Model name
 * @param {number} expectedSize - Expected total size
 */
function showProgressBar(model, expectedSize) {
  console.log('showProgressBar called for:', model, 'size:', expectedSize);

  // Initialize progress state for this model
  downloadProgressState[model] = {
    status: 'downloading',
    progress: 0,
    downloaded: 0,
    size: expectedSize || 0,
    speed_mbps: 0
  };
  console.log('Progress state initialized for', model);
}

/**
 * Start SSE listener for download progress
 * @param {string} model - Model name
 */
function startProgressListener(model) {
  console.log('startProgressListener called for:', model);
  const eventSource = new EventSource(`/api/download-stream/${encodeURIComponent(model)}`);

  eventSource.onopen = function (e) {
    console.log('SSE connection opened for', model);
  };

  eventSource.onmessage = function (e) {
    console.log('SSE message received:', e.data);
    try {
      const data = JSON.parse(e.data);
      const progress = data.progress || 0;
      const downloaded = data.downloaded || 0;
      const size = data.size || 0;
      const speed = data.speed_mbps || 0;

      // Initialize or update the stored state for persistence across refreshes
      if (!downloadProgressState[model]) {
        downloadProgressState[model] = { status: 'downloading', progress: 0, downloaded: 0, size: 0, speed_mbps: 0 };
      }
      // Keep status as 'downloading' if the download is still in progress (resumed, error, etc.)
      // Only change to 'complete' or 'error' when truly finished
      const isActiveStatus = ['downloading', 'resumed', 'error', 'starting'].includes(data.status);
      downloadProgressState[model].status = isActiveStatus ? 'downloading' : (data.status || 'downloading');
      downloadProgressState[model].progress = progress;
      downloadProgressState[model].downloaded = downloaded;
      downloadProgressState[model].size = size;
      downloadProgressState[model].speed_mbps = speed;
      // Sanitize details to prevent XSS
      const detailsText = data.status === 'error'
        ? 'Error: ' + (data.error || 'Unknown error')
        : data.status === 'complete'
          ? 'Complete!'
          : (speed ? speed.toFixed(1) + ' MB/s' + ' · ' : '');
      downloadProgressState[model].details = detailsText.replace(/</g, '&lt;').replace(/>/g, '&gt;');

      // Update state for tile rendering (refresh() will update the DOM)
      // This avoids race conditions between SSE updates and tile re-rendering

       // Update DOM directly for real-time progress
       const safeModelId = safeId(model);
       const bar = document.getElementById('progress-bar-' + safeModelId);
       const text = document.getElementById('progress-text-' + safeModelId);
       const details = document.getElementById('progress-details-' + safeModelId);
      if (bar) bar.style.width = progress + '%';
      if (text) text.textContent = progress + '%';
      if (details) {
        const statusText = data.status === 'error'
          ? 'Error: ' + (data.error || 'Unknown')
          : data.status === 'complete'
            ? 'Complete!'
            : (speed ? speed.toFixed(1) + ' MB/s' : '');
        details.textContent = statusText;
      }

      if (data.status === 'complete' || data.status === 'error') {
        // Remove the progress bar from DOM
        const container = document.getElementById('progress-' + safeModelId);
        if (container) container.remove();

        setTimeout(() => {
          eventSource.close();
          // Clean up progress state after completion
          setTimeout(() => {
            delete downloadProgressState[model];
          }, 5000);
        }, 500);
      }
    } catch (err) {
      console.error('Progress parse error:', err);
    }
  };

  eventSource.onerror = function (e) {
    console.log('SSE closed for', model, e);
    eventSource.close();
  };
}
