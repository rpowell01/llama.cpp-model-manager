/**
 * Initialization and Event Listeners
 * Sets up all DOM event handlers and initial application state
 */

// Wait for DOM to be ready
document.addEventListener('DOMContentLoaded', function() {
  init();
});

function init() {
   console.log('Initializing application...');

   // Theme switching
   initTheme();

   // Search input debounce
   initSearchDebounce();

   // Tile sort order and speculative model visibility
   initTileControls();

   // Button event listeners
   initButtonListeners();

   // Modal initializations
   initHfModal();
   initChatModal();
    initKiloConfigModal();

   // Restore chat input from localStorage (if needed)
   restoreChatInputs();

// Initial refresh with retry logic
    console.log('Calling initial refresh...');
    const maxRetries = 3;
    const retryDelay = 1000;
    
    function attemptRefresh(attempt) {
      return refresh().catch(err => {
        console.error(`Initial refresh attempt ${attempt} failed:`, err);
        if (attempt < maxRetries) {
          console.log(`Retrying in ${retryDelay}ms... (attempt ${attempt + 1}/${maxRetries})`);
          return new Promise(resolve => setTimeout(resolve, retryDelay))
            .then(() => attemptRefresh(attempt + 1));
        }
        throw err;
      });
    }
    
    attemptRefresh(1).catch(err => {
      console.error('All initial refresh attempts failed:', err);
      // Hide loading spinner even on failure
      const loadingEl = document.getElementById('loading');
      if (loadingEl) loadingEl.style.display = 'none';
    });
  }

// ──────────────────────────────────────────────────────────────
// Theme Switching
// ──────────────────────────────────────────────────────────────
function initTheme() {
  const themeSelector = document.getElementById('themeSelector');
  if (!themeSelector) {
    console.warn('Theme selector not found');
    return;
  }

  const savedTheme = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', savedTheme);
  themeSelector.value = savedTheme;

  themeSelector.addEventListener('change', function() {
    const theme = this.value;
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  });
}

// ──────────────────────────────────────────────────────────────
// Search Input Debounce
// ──────────────────────────────────────────────────────────────
function initSearchDebounce() {
  let searchTimeout;
  const searchInput = document.getElementById('search');
  if (!searchInput) {
    console.warn('Search input not found');
    return;
  }

  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(refresh, 300);
  });
}

// ──────────────────────────────────────────────────────────────
// Tile Controls (sort order, speculative model visibility)
// ──────────────────────────────────────────────────────────────
function initTileControls() {
  const sortSelect = document.getElementById('tileSortOrder');
  const hideSpecCheckbox = document.getElementById('hideSpecModels');

  if (sortSelect) {
    sortSelect.value = tilePrefs.sortOrder;
    sortSelect.addEventListener('change', function() {
      updateTilePrefs({ sortOrder: this.value });
    });
  } else {
    console.warn('Tile sort order control not found');
  }

  if (hideSpecCheckbox) {
    hideSpecCheckbox.checked = tilePrefs.hideSpecModels;
    hideSpecCheckbox.addEventListener('change', function() {
      updateTilePrefs({ hideSpecModels: this.checked });
    });
  } else {
    console.warn('Hide speculative models control not found');
  }
}

// ──────────────────────────────────────────────────────────────
// Button Listeners
// ──────────────────────────────────────────────────────────────
function initButtonListeners() {
  // Refresh button
  const refreshBtn = document.getElementById('refreshBtn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', refresh);
  }

  // Shutdown button
  const shutdownBtn = document.getElementById('shutdownBtn');
  if (shutdownBtn) {
    shutdownBtn.addEventListener('click', async function() {
      if (!confirm('Shutdown the web application? This will stop the Flask server.')) {
        return;
      }
      try {
        const res = await fetch('/api/shutdown', { method: 'POST' });
        const data = await res.json();
        console.log('Shutdown response:', data);
        alert('Server shutting down...');
      } catch (e) {
        console.error('Shutdown error:', e);
      }
    });
  }

  // Add model button
  const addModelBtn = document.getElementById('addModelBtn');
  if (addModelBtn) {
    addModelBtn.addEventListener('click', openAddModelModal);
  }

  // Add model modal close buttons
  const addModelModalClose = document.getElementById('add-model-modal-close');
  const addModelModalCancel = document.getElementById('add-model-modal-cancel');
  if (addModelModalClose) {
    addModelModalClose.addEventListener('click', () => ModalManager.close('add-model-modal'));
  }
  if (addModelModalCancel) {
    addModelModalCancel.addEventListener('click', () => ModalManager.close('add-model-modal'));
  }

  // Panel close button
  const panelCloseBtn = document.getElementById('panel-close');
  if (panelCloseBtn) {
    panelCloseBtn.addEventListener('click', closePanel);
  }
}

// ──────────────────────────────────────────────────────────────
// Hugging Face Modal
// ──────────────────────────────────────────────────────────────
function initHfModal() {
  let currentRepoSearchModel = null;
  const hfModal = document.getElementById('hf-repo-modal');
  const hfModalCancel = document.getElementById('hf-modal-cancel');

  // Register cleanup to reset state when modal closes
  ModalManager.register('hf-repo-modal', () => {
    currentRepoSearchModel = null;
  });

  // Cancel button uses ModalManager
  if (hfModalCancel) {
    hfModalCancel.addEventListener('click', () => {
      ModalManager.close('hf-repo-modal');
    });
  }

  // Close modal on outside click is handled by ModalManager

  // Attach click handlers to "Find on Hugging Face" buttons (event delegation)
  document.addEventListener('click', function(e) {
    if (e.target && e.target.id && e.target.id.startsWith('hf-find-btn-')) {
      const modelName = e.target.getAttribute('data-model');
      if (!modelName) return;
      const safeId = modelName.replace(/[^a-zA-Z0-9]/g, '_');
      const statusEl = document.getElementById(`hf-search-status-${safeId}`);
      if (statusEl) statusEl.textContent = 'Searching Hugging Face...';

      fetch('/api/hf-repo-search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelName })
      })
      .then(resp => resp.json())
      .then(data => {
        if (data.error) {
          throw new Error(data.error);
        }
        if (statusEl) statusEl.textContent = '';
        // Auto-select best repo based on downloads and likes
        const best = selectBestRepo(data.results);
        if (statusEl) {
          statusEl.textContent = `Selected: ${best.repo} (↓${best.downloads.toLocaleString()} ♥${best.likes.toLocaleString()})`;
        }
        updateModelRepo(modelName, best.repo);
      })
      .catch(err => {
        if (statusEl) statusEl.textContent = 'Error: ' + err.message;
      });
    }
  });
}

// ──────────────────────────────────────────────────────────────
// Chat Modal
// ──────────────────────────────────────────────────────────────
function initChatModal() {
  const chatModal = document.getElementById('chat-modal');
  const chatCloseBtn = document.getElementById('chat-modal-close');

   if (chatCloseBtn) {
     chatCloseBtn.addEventListener('click', () => ModalManager.close('chat-modal'));
   }

   // Register chat modal
   ModalManager.register('chat-modal', () => {
     // Cleanup handler for when modal closes
   });

   // Performance Graph Modal - close button listener (at init, like chat-modal)
   const perfCloseBtn = document.getElementById('performance-graph-modal-close');
   if (perfCloseBtn) {
     perfCloseBtn.addEventListener('click', () => ModalManager.close('performance-graph-modal'));
   }

   // Register performance graph modal (cleanup only)
   ModalManager.register('performance-graph-modal', () => {
     if (typeof window.cleanupPerformanceGraph === 'function') {
       window.cleanupPerformanceGraph();
     }
   });

  // Clear chat button handler (event delegation for dynamic button)
  if (chatModal) {
    chatModal.addEventListener('click', function(e) {
      if (e.target && e.target.id === 'chat-modal-clear') {
        const modelName = chatModal.getAttribute('data-model-name');
        if (modelName) {
          if (chatHistory[modelName]) {
            chatHistory[modelName] = [];
          }
          const safeId = modelName.replace(/[^a-zA-Z0-9]/g, '_');
          const messagesDiv = document.getElementById(`chat-messages-${safeId}`);
          if (messagesDiv) {
            messagesDiv.innerHTML = '';
          }
          const input = document.getElementById(`chat-input-${safeId}`);
          if (input) {
            input.value = '';
          }
          // Reset performance metrics to placeholder
          const ppEl = document.getElementById(`chat-pp-${safeId}`);
          const tgEl = document.getElementById(`chat-tg-${safeId}`);
          const pgEl = document.getElementById(`chat-pg-${safeId}`);
          if (ppEl) ppEl.textContent = '-';
          if (tgEl) tgEl.textContent = '-';
          if (pgEl) pgEl.textContent = '-';
          localStorage.removeItem('chat_input_' + modelName);
          localStorage.removeItem('chat_history_' + modelName);
        }
      }
    });
  }

  // Global function to open chat modal (called by tile button)
  window.openChatModal = function(modelName) {
    // Store model name on modal for later use
    chatModal.setAttribute('data-model-name', modelName);

    // Render chat UI in modal
    renderChatInModal(modelName);

    // Open modal using manager
    ModalManager.open('chat-modal');
  };
}

// ──────────────────────────────────────────────────────────────
// Kilo Code Config Modal
// ──────────────────────────────────────────────────────────────
function initKiloConfigModal() {
  const closeBtn = document.getElementById('kilo-config-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      ModalManager.close('kilo-config-modal');
    });
  }

  // Click outside is handled by ModalManager when modal is opened via ModalManager.open

  const copyBtn = document.getElementById('kilo-config-copy');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      const contentEl = document.getElementById('kilo-config-content');
      const content = contentEl ? contentEl.textContent : '';
      if (!content) return;
      try {
        await navigator.clipboard.writeText(content);
        const originalText = copyBtn.textContent;
        copyBtn.textContent = 'Copied!';
        copyBtn.style.background = 'var(--green)';
        setTimeout(() => {
          copyBtn.textContent = originalText;
          copyBtn.style.background = '';
        }, 1500);
      } catch (err) {
        // Fallback for older browsers
        try {
          const textArea = document.createElement('textarea');
          textArea.value = content;
          textArea.style.position = 'fixed';
          document.body.appendChild(textArea);
          textArea.select();
          const successful = document.execCommand('copy');
          if (successful) {
            const originalText = copyBtn.textContent;
            copyBtn.textContent = 'Copied!';
            setTimeout(() => {
              copyBtn.textContent = originalText;
            }, 1500);
          } else {
            throw new Error('Copy command failed');
          }
        } catch (e) {
          alert('Failed to copy to clipboard. Please copy manually.');
        }
        if (textArea && textArea.parentNode) {
          document.body.removeChild(textArea);
        }
      }
    });
  }
}

// ──────────────────────────────────────────────────────────────
// Restore Chat Inputs
// ──────────────────────────────────────────────────────────────
function restoreChatInputs() {
  // This is handled in state.js via localStorage iteration
  // But we can also restore any draft inputs if needed
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && key.startsWith('chat_input_')) {
      // Draft inputs will be restored when chat modal opens
      // No action needed here
    }
  }
}
