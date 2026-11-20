/**
 * Hugging Face integration - repo search and selection
 */

// Modal state
let currentRepoSearchModel = null;
const hfModal = document.getElementById('hf-repo-modal');
const hfRepoList = document.getElementById('hf-repo-list');
const hfModalCancel = document.getElementById('hf-modal-cancel');
if (hfModalCancel) {
  hfModalCancel.addEventListener('click', () => ModalManager.close('hf-repo-modal'));
}

/**
 * Update model's Hugging Face repository
 * @param {string} modelName - Model name
 * @param {string} repo - Hugging Face repo ID
 */
function updateModelRepo(modelName, repo) {
  fetch('/api/update-model-repo', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: modelName, repo: repo })
  })
    .then(resp => resp.json())
    .then(data => {
      if (data.error) throw new Error(data.error);
      // Refresh model list and panel will update via refresh's completion
      return refresh(false);
    })
    .catch(err => {
      const statusEl = document.getElementById(`hf-search-status-${safeId(modelName)}`);
      if (statusEl) statusEl.textContent = 'Error: ' + err.message;
    });
}

/**
 * Show repo selection modal
 * @param {string} modelName - Model name
 * @param {Array} results - Search results
 */
function showRepoSelectionModal(modelName, results) {
  currentRepoSearchModel = modelName;
  if (!hfRepoList) return;

  hfRepoList.innerHTML = '';
  const desc = document.getElementById('hf-modal-description');
  if (desc) desc.textContent = `Found ${results.length} result(s). Select the correct Hugging Face repository.`;

  // Aggregate results by repo
  const reposMap = new Map();
  results.forEach(r => {
    if (!reposMap.has(r.repo)) {
      reposMap.set(r.repo, { repo: r.repo, files: [], downloads: r.downloads, likes: r.likes });
    }
    reposMap.get(r.repo).files.push(r);
  });

   reposMap.forEach(item => {
     const card = document.createElement('div');
     card.className = 'hf-repo-card';
     card.style.cssText = 'padding: 0.75rem; border: 1px solid var(--border); border-radius: 6px; background: var(--surface1); cursor: pointer; transition: background 0.2s;';
     card.innerHTML = `
       <div style="display: flex; justify-content: space-between; align-items: start;">
         <div style="flex: 1;">
           <div style="font-weight: 600; color: var(--text); margin-bottom: 0.25rem;">${he(item.repo)}</div>
           <div style="font-size: 0.8rem; color: var(--muted);">${item.files.length} GQUF file(s)</div>
         </div>
         <div style="text-align: right; font-size: 0.85rem; color: var(--text2);">
           <div>⬇ ${item.downloads.toLocaleString()}</div>
           <div>♥ ${item.likes.toLocaleString()}</div>
         </div>
       </div>
     `;
     card.addEventListener('click', () => {
        ModalManager.close('hf-repo-modal');
       updateModelRepo(modelName, item.repo);
     });
     card.addEventListener('mouseenter', () => card.style.background = 'var(--surface2)');
     card.addEventListener('mouseleave', () => card.style.background = 'var(--surface1)');
     hfRepoList.appendChild(card);
   });

   ModalManager.open('hf-repo-modal');
}

/**
 * Select the best Hugging Face repo based on downloads and likes
 * @param {Array} results - Search results
 * @returns {Object} Best repo candidate
 */
function selectBestRepo(results) {
  const reposMap = new Map();
  results.forEach(r => {
    if (!reposMap.has(r.repo)) {
      reposMap.set(r.repo, { repo: r.repo, downloads: r.downloads, likes: r.likes, fileCount: 0 });
    }
    reposMap.get(r.repo).fileCount++;
  });
  const repos = Array.from(reposMap.values());
  repos.sort((a, b) => {
    const scoreA = a.downloads + a.likes;
    const scoreB = b.downloads + b.likes;
    if (scoreB !== scoreA) return scoreB - scoreA;
    return b.downloads - a.downloads;
  });
  return repos[0];
}

/**
 * Automatically search and select best HF repo for a model (web-iet)
 * @param {string} modelName - Model name
 */
async function autoSearchAndSelectRepo(modelName) {
  try {
    const resp = await fetch('/api/hf-repo-search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelName })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || 'Search failed');
    }
    const data = await resp.json();
    if (!data.results || data.results.length === 0) {
      const statusEl = document.getElementById(`hf-search-status-${safeId(modelName)}`);
      if (statusEl) statusEl.textContent = 'No matching GGUF repository found on Hugging Face.';
      return;
    }
    const best = selectBestRepo(data.results);
    // Update model repo with the best candidate
    updateModelRepo(modelName, best.repo);
  } catch (err) {
    console.error('Auto-search error:', err);
    const statusEl = document.getElementById(`hf-search-status-${safeId(modelName)}`);
    if (statusEl) statusEl.textContent = 'Auto-search failed: ' + err.message;
  }
}

