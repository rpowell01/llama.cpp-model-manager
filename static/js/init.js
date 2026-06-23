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

   // Button event listeners
   initButtonListeners();

   // Modal initializations
   initHfModal();
   initChatModal();
   initKilloConfigModal();

   // Restore chat input from localStorage (if needed)
   restoreChatInputs();

   // Initial refresh
   console.log('Calling initial refresh...');
   refresh();
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
    addModelBtn.addEventListener('click', showAddForm);
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
function initKilloConfigModal() {
  const closeBtn = document.getElementById('killo-config-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      ModalManager.close('killo-config-modal');
    });
  }

  // Click outside is handled by ModalManager when modal is opened via ModalManager.open

  const copyBtn = document.getElementById('killo-config-copy');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      const contentEl = document.getElementById('killo-config-content');
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
