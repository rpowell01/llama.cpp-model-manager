/**
 * Custom File Browser Module
 * 
 * Provides a file/folder browser modal that maintains an in-memory path stack
 * to return full paths instead of just leaf names.
 * 
 * Uses the File System Access API (showDirectoryPicker) to get a directory handle,
 * then enumerates contents and tracks navigation in memory.
 */

const FileBrowser = (function() {
  'use strict';

  // State for the file browser
  let state = {
    modal: null,
    currentDirHandle: null,
    pathStack: [], // Array of {name: string, handle: FileSystemDirectoryHandle}
    handleMap: new Map(), // Map of name -> handle for current directory
    browseType: 'file', // 'file', 'directory', 'model-file'
    onSelect: null,
    filter: null, // File filter for model-file type
    isLoading: false
  };

  /**
   * Create the modal HTML structure
   */
  function createModal() {
    const modal = document.createElement('div');
    modal.id = 'file-browser-modal';
    modal.className = 'file-browser-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'file-browser-title');
    modal.innerHTML = `
      <div class="file-browser-overlay" tabindex="-1"></div>
      <div class="file-browser-dialog">
        <div class="file-browser-header">
          <h2 id="file-browser-title">Select File</h2>
          <button type="button" class="file-browser-close" aria-label="Close">&times;</button>
        </div>
        <div class="file-browser-toolbar">
          <div class="file-browser-path" id="file-browser-path"></div>
          <div class="file-browser-actions">
            <button type="button" class="file-browser-up" id="file-browser-up" title="Go up one level" disabled>↑ Up</button>
            <button type="button" class="file-browser-refresh" id="file-browser-refresh" title="Refresh">⟳</button>
          </div>
        </div>
        <div class="file-browser-content" id="file-browser-content" role="listbox" aria-label="Files and folders">
          <div class="file-browser-loading" id="file-browser-loading">Loading...</div>
        </div>
        <div class="file-browser-footer">
          <button type="button" class="file-browser-cancel" id="file-browser-cancel">Cancel</button>
          <button type="button" class="file-browser-select" id="file-browser-select" disabled>Select</button>
        </div>
      </div>
    `;
    return modal;
  }

  /**
   * Get CSS styles for the file browser
   */
  function getStyles() {
    return `
      .file-browser-modal {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        z-index: 10000;
        display: flex;
        align-items: center;
        justify-content: center;
        font-family: inherit;
      }

      .file-browser-overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.5);
      }

      .file-browser-dialog {
        position: relative;
        width: 90%;
        max-width: 800px;
        height: 80%;
        max-height: 600px;
        background: var(--surface, #1e1e1e);
        border: 1px solid var(--border, #333);
        border-radius: 8px;
        display: flex;
        flex-direction: column;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
      }

      .file-browser-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 1rem 1.5rem;
        border-bottom: 1px solid var(--border, #333);
        background: var(--surface2, #252526);
        border-radius: 8px 8px 0 0;
      }

      .file-browser-header h2 {
        margin: 0;
        font-size: 1.1rem;
        font-weight: 600;
        color: var(--text, #e0e0e0);
      }

      .file-browser-close {
        background: none;
        border: none;
        font-size: 1.5rem;
        color: var(--muted, #888);
        cursor: pointer;
        padding: 0.25rem 0.5rem;
        line-height: 1;
        border-radius: 4px;
      }

      .file-browser-close:hover {
        background: var(--surface3, #2d2d2d);
        color: var(--text, #e0e0e0);
      }

      .file-browser-toolbar {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
        padding: 0.75rem 1.5rem;
        border-bottom: 1px solid var(--border, #333);
        background: var(--surface2, #252526);
      }

      .file-browser-path {
        font-family: monospace;
        font-size: 0.85rem;
        color: var(--muted, #888);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        padding: 0.25rem 0.5rem;
        background: var(--surface, #1e1e1e);
        border-radius: 4px;
        border: 1px solid var(--border, #333);
      }

      .file-browser-actions {
        display: flex;
        gap: 0.5rem;
      }

      .file-browser-actions button {
        padding: 0.375rem 0.75rem;
        font-size: 0.85rem;
        background: var(--surface3, #2d2d2d);
        border: 1px solid var(--border, #333);
        border-radius: 4px;
        color: var(--text, #e0e0e0);
        cursor: pointer;
        transition: all 0.15s ease;
      }

      .file-browser-actions button:hover:not(:disabled) {
        background: var(--accent, #007acc);
        border-color: var(--accent, #007acc);
      }

      .file-browser-actions button:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }

      .file-browser-content {
        flex: 1;
        overflow-y: auto;
        padding: 0.5rem;
      }

      .file-browser-loading {
        display: flex;
        align-items: center;
        justify-content: center;
        height: 100%;
        color: var(--muted, #888);
        font-size: 0.9rem;
      }

      .file-browser-list {
        list-style: none;
        margin: 0;
        padding: 0;
      }

      .file-browser-item {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.5rem 0.75rem;
        border-radius: 4px;
        cursor: pointer;
        transition: background 0.1s ease;
        user-select: none;
      }

      .file-browser-item:hover {
        background: var(--surface3, #2d2d2d);
      }

      .file-browser-item.selected {
        background: var(--accent, #007acc);
        color: white;
      }

      .file-browser-item.selected .file-browser-item-name {
        color: white;
      }

      .file-browser-item.selected .file-browser-item-meta {
        color: rgba(255, 255, 255, 0.7);
      }

      .file-browser-item-icon {
        font-size: 1.1rem;
        flex-shrink: 0;
        width: 24px;
        text-align: center;
      }

      .file-browser-item-name {
        flex: 1;
        font-size: 0.9rem;
        color: var(--text, #e0e0e0);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .file-browser-item-meta {
        font-size: 0.75rem;
        color: var(--muted, #888);
        flex-shrink: 0;
      }

      .file-browser-item.directory .file-browser-item-icon {
        color: var(--accent, #007acc);
      }

      .file-browser-item.file .file-browser-item-icon {
        color: var(--muted, #888);
      }

      .file-browser-item.parent .file-browser-item-icon {
        color: var(--warning, #ffa500);
      }

      .file-browser-footer {
        display: flex;
        justify-content: flex-end;
        gap: 0.75rem;
        padding: 1rem 1.5rem;
        border-top: 1px solid var(--border, #333);
        background: var(--surface2, #252526);
        border-radius: 0 0 8px 8px;
      }

      .file-browser-footer button {
        padding: 0.5rem 1.25rem;
        font-size: 0.9rem;
        border-radius: 4px;
        cursor: pointer;
        transition: all 0.15s ease;
      }

      .file-browser-cancel {
        background: var(--surface3, #2d2d2d);
        border: 1px solid var(--border, #333);
        color: var(--text, #e0e0e0);
      }

      .file-browser-cancel:hover {
        background: var(--surface, #1e1e1e);
      }

      .file-browser-select {
        background: var(--accent, #007acc);
        border: 1px solid var(--accent, #007acc);
        color: white;
      }

      .file-browser-select:hover:not(:disabled) {
        background: var(--accent-hover, #005a9e);
        border-color: var(--accent-hover, #005a9e);
      }

      .file-browser-select:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }

      .file-browser-empty {
        display: flex;
        align-items: center;
        justify-content: center;
        height: 200px;
        color: var(--muted, #888);
        font-size: 0.9rem;
      }

      .file-browser-error {
        display: flex;
        align-items: center;
        justify-content: center;
        height: 200px;
        color: var(--error, #f44);
        font-size: 0.9rem;
        padding: 1rem;
        text-align: center;
      }
    `;
  }

  /**
   * Inject styles into the document
   */
  function injectStyles() {
    if (document.getElementById('file-browser-styles')) return;
    const style = document.createElement('style');
    style.id = 'file-browser-styles';
    style.textContent = getStyles();
    document.head.appendChild(style);
  }

  /**
   * Update the path display in the toolbar
   */
  function updatePathDisplay() {
    const pathEl = document.getElementById('file-browser-path');
    if (!pathEl) return;

    if (state.pathStack.length === 0) {
      pathEl.textContent = '/ (root)';
    } else {
      const path = '/' + state.pathStack.map(p => p.name).join('/');
      pathEl.textContent = path;
    }

    // Update up button state
    const upBtn = document.getElementById('file-browser-up');
    if (upBtn) {
      upBtn.disabled = state.pathStack.length === 0;
    }
  }

  /**
   * Update the select button state
   */
  function updateSelectButton() {
    const selectBtn = document.getElementById('file-browser-select');
    if (!selectBtn) return;

    if (state.browseType === 'directory') {
      // Allow selecting current directory (when at root after selecting via picker)
      selectBtn.disabled = false;
      return;
    }

    const selectedItem = document.querySelector('.file-browser-item.selected');
    const canSelect = selectedItem && (
      (state.browseType !== 'directory' && selectedItem.classList.contains('file')) ||
      selectedItem.classList.contains('parent')
    );
    selectBtn.disabled = !canSelect;
  }

  /**
   * Render the file/folder list
   */
  async function renderList() {
    const contentEl = document.getElementById('file-browser-content');
    const loadingEl = document.getElementById('file-browser-loading');
    if (!contentEl) return;

    // Show loading
    if (loadingEl) loadingEl.style.display = 'flex';
    contentEl.innerHTML = '';
    contentEl.appendChild(loadingEl);

    try {
      state.isLoading = true;
      const entries = [];

      // Add parent directory entry if not at root
      if (state.pathStack.length > 0) {
        entries.push({
          name: '..',
          isDirectory: true,
          isParent: true,
          handle: null
        });
      }

      // Read directory contents
      if (state.currentDirHandle) {
        // Clear handle map for new directory
        state.handleMap.clear();
        
        for await (const [name, handle] of state.currentDirHandle.entries()) {
          // Skip hidden files
          if (name.startsWith('.')) continue;

          const isDirectory = handle.kind === 'directory';
          
          // Apply filter for model-file type
          if (state.browseType === 'model-file' && !isDirectory) {
            const ext = name.split('.').pop().toLowerCase();
            if (ext !== 'gguf') continue;
          }

          // Store handle in map for later navigation
          state.handleMap.set(name, handle);

          entries.push({
            name,
            isDirectory,
            isParent: false,
            handle
          });
        }
      }

      // Sort: directories first (with .. at top), then files, alphabetically
      entries.sort((a, b) => {
        if (a.isParent) return -1;
        if (b.isParent) return 1;
        if (a.isDirectory !== b.isDirectory) {
          return a.isDirectory ? -1 : 1;
        }
        return a.name.localeCompare(b.name);
      });

      // Create list
      const list = document.createElement('ul');
      list.className = 'file-browser-list';
      list.setAttribute('role', 'listbox');

      let selectedItem = null;

      entries.forEach((entry, index) => {
        const item = document.createElement('li');
        item.className = `file-browser-item ${entry.isDirectory ? 'directory' : 'file'} ${entry.isParent ? 'parent' : ''}`;
        item.setAttribute('role', 'option');
        item.setAttribute('tabindex', '0');
        item.dataset.index = index;
        item.dataset.name = entry.name;
        item.dataset.isDirectory = entry.isDirectory;
        item.dataset.isParent = entry.isParent;

        const icon = entry.isParent 
          ? '⬆' 
          : entry.isDirectory 
            ? '📁' 
            : '📄';

        const meta = entry.isDirectory ? 'Folder' : 'File';

        item.innerHTML = `
          <span class="file-browser-item-icon">${icon}</span>
          <span class="file-browser-item-name">${escapeHtml(entry.name)}</span>
          <span class="file-browser-item-meta">${meta}</span>
        `;

        // Store handle in dataset for later retrieval
        if (entry.handle) {
          item.dataset.handle = JSON.stringify({
            name: entry.name,
            kind: entry.isDirectory ? 'directory' : 'file'
          });
        }

        item.addEventListener('click', () => handleItemClick(item, entry));
        item.addEventListener('keydown', (e) => handleItemKeydown(e, item, entry));
        item.addEventListener('dblclick', () => handleItemDblClick(item, entry));

        list.appendChild(item);
      });

      contentEl.innerHTML = '';
      if (entries.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'file-browser-empty';
        empty.textContent = 'This folder is empty';
        contentEl.appendChild(empty);
      } else {
        contentEl.appendChild(list);
      }

      updateSelectButton();

    } catch (err) {
      console.error('Error reading directory:', err);
      contentEl.innerHTML = `
        <div class="file-browser-error">
          Error reading directory: ${escapeHtml(err.message)}
        </div>
      `;
    } finally {
      state.isLoading = false;
      if (loadingEl) loadingEl.style.display = 'none';
    }
  }

  /**
   * Handle item click
   */
  function handleItemClick(item, entry) {
    // Remove previous selection
    document.querySelectorAll('.file-browser-item.selected').forEach(el => {
      el.classList.remove('selected');
    });

    // Select this item
    item.classList.add('selected');
    updateSelectButton();
  }

  /**
   * Handle item double-click (navigate or select)
   */
  async function handleItemDblClick(item, entry) {
    if (entry.isParent) {
      await navigateUp();
    } else if (entry.isDirectory) {
      await navigateInto(entry);
    } else if (state.browseType !== 'directory') {
      // File selected in file mode - confirm selection
      await confirmSelection(entry);
    }
  }

  /**
   * Handle keyboard navigation
   */
  function handleItemKeydown(e, item, entry) {
    const list = item.closest('.file-browser-list');
    const items = list?.querySelectorAll('.file-browser-item');
    if (!items || items.length === 0) return;

    const currentIndex = Array.from(items).indexOf(item);

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        const nextIndex = Math.min(currentIndex + 1, items.length - 1);
        items[nextIndex].focus();
        items[nextIndex].click();
        break;
      case 'ArrowUp':
        e.preventDefault();
        const prevIndex = Math.max(currentIndex - 1, 0);
        items[prevIndex].focus();
        items[prevIndex].click();
        break;
      case 'Enter':
        e.preventDefault();
        if (entry.isParent) {
          navigateUp();
        } else if (entry.isDirectory) {
          navigateInto(entry);
        } else if (state.browseType !== 'directory') {
          confirmSelection(entry);
        }
        break;
      case 'Escape':
        closeModal();
        break;
    }
  }

  /**
   * Navigate into a directory
   */
  async function navigateInto(entry) {
    if (!entry.handle || entry.isParent) return;

    try {
      state.isLoading = true;
      state.pathStack.push({ name: entry.name, handle: entry.handle });
      state.currentDirHandle = entry.handle;
      updatePathDisplay();
      await renderList();
    } catch (err) {
      console.error('Error navigating into directory:', err);
      logError('Error navigating into directory: ' + err.message);
      state.pathStack.pop();
    } finally {
      state.isLoading = false;
    }
  }

  /**
   * Navigate up one level
   */
  async function navigateUp() {
    if (state.pathStack.length === 0) return;

    try {
      state.isLoading = true;
      state.pathStack.pop();
      state.currentDirHandle = state.pathStack.length > 0 
        ? state.pathStack[state.pathStack.length - 1].handle 
        : null;
      updatePathDisplay();
      await renderList();
    } catch (err) {
      console.error('Error navigating up:', err);
      logError('Error navigating up: ' + err.message);
    } finally {
      state.isLoading = false;
    }
  }

  /**
   * Build full path from path stack
   */
  function buildFullPath(entry) {
    const pathParts = state.pathStack.map(p => p.name);
    if (entry && !entry.isParent) {
      pathParts.push(entry.name);
    }
    return '/' + pathParts.join('/');
  }

  /**
   * Confirm selection and call callback
   */
  async function confirmSelection(entry) {
    if (!entry || entry.isParent) return;

    const fullPath = buildFullPath(entry);
    
    // Close modal
    closeModal();

    // Call the callback with the full path
    if (state.onSelect) {
      state.onSelect(fullPath, entry);
    }
  }

  /**
   * Close the modal
   */
  function closeModal() {
    if (state.modal) {
      state.modal.remove();
      state.modal = null;
    }
    // Reset state
    state.currentDirHandle = null;
    state.pathStack = [];
    state.handleMap.clear();
    state.onSelect = null;
    state.filter = null;
    state.browseType = 'file';
  }

  /**
   * Open the file browser modal
   * @param {Object} options - Configuration options
   * @param {string} options.type - 'file', 'directory', or 'model-file'
   * @param {Function} options.onSelect - Callback function(fullPath, entry)
   * @param {string} [options.startPath] - Optional starting path (not used with FSA API)
   * @param {Array} [options.filters] - File filters for model-file type
   */
  async function open(options = {}) {
    // Check for File System Access API support
    if (!('showDirectoryPicker' in window)) {
      // Fallback to prompt
      const message = options.type === 'directory' 
        ? 'Enter full directory path:' 
        : options.type === 'model-file' 
          ? 'Enter full path to GGUF model file:' 
          : 'Enter full file path:';
      const path = prompt(message);
      if (path && options.onSelect) {
        options.onSelect(path, { name: path.split('/').pop(), isDirectory: options.type === 'directory' });
      }
      return;
    }

    // Initialize state
    state.browseType = options.type || 'file';
    state.onSelect = options.onSelect;
    state.filter = options.filters;
    state.pathStack = [];
    state.currentDirHandle = null;

    // Inject styles
    injectStyles();

    // Create and show modal
    state.modal = createModal();
    document.body.appendChild(state.modal);

    // Set title based on type
    const titleEl = document.getElementById('file-browser-title');
    if (titleEl) {
      switch (state.browseType) {
        case 'directory':
          titleEl.textContent = 'Select Folder';
          break;
        case 'model-file':
          titleEl.textContent = 'Select Model File (GGUF)';
          break;
        default:
          titleEl.textContent = 'Select File';
      }
    }

    // Set up event handlers
    setupModalHandlers();

    // Get initial directory handle
    try {
      state.currentDirHandle = await window.showDirectoryPicker({
        mode: 'read'
      });
      await renderList();
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('Error opening directory picker:', err);
        logError('Error opening directory picker: ' + err.message);
        closeModal();
      }
    }
  }

  /**
   * Set up modal event handlers
   */
  function setupModalHandlers() {
    if (!state.modal) return;

    // Close button
    const closeBtn = state.modal.querySelector('.file-browser-close');
    closeBtn?.addEventListener('click', closeModal);

    // Cancel button
    const cancelBtn = document.getElementById('file-browser-cancel');
    cancelBtn?.addEventListener('click', closeModal);

    // Select button
    const selectBtn = document.getElementById('file-browser-select');
    selectBtn?.addEventListener('click', async () => {
      if (state.browseType === 'directory') {
        // Select current directory
        const fullPath = buildFullPath(null);
        closeModal();
        if (state.onSelect) {
          state.onSelect(fullPath, { name: state.pathStack.length > 0 ? state.pathStack[state.pathStack.length - 1].name : 'root', isDirectory: true });
        }
        return;
      }

      const selectedItem = document.querySelector('.file-browser-item.selected');
      if (selectedItem) {
        const name = selectedItem.dataset.name;
        const isDirectory = selectedItem.dataset.isDirectory === 'true';
        const isParent = selectedItem.dataset.isParent === 'true';

        if (isParent) {
          await navigateUp();
        } else if (isDirectory) {
          // In file/model-file mode, navigate into directory
          const handle = state.handleMap.get(name);
          if (handle) {
            await navigateInto({ name, handle, isDirectory: true, isParent: false });
          }
        } else {
          // File selected
          const entry = { name, isDirectory: false, isParent: false };
          await confirmSelection(entry);
        }
      }
    });

    // Up button
    const upBtn = document.getElementById('file-browser-up');
    upBtn?.addEventListener('click', () => {
      navigateUp();
      updateSelectButton();
    });

    // Refresh button
    const refreshBtn = document.getElementById('file-browser-refresh');
    refreshBtn?.addEventListener('click', renderList);

    // Close on overlay click
    const overlay = state.modal.querySelector('.file-browser-overlay');
    overlay?.addEventListener('click', closeModal);

    // Escape key
    const handleKeydown = (e) => {
      if (e.key === 'Escape') {
        closeModal();
        document.removeEventListener('keydown', handleKeydown);
      }
    };
    document.addEventListener('keydown', handleKeydown);

    // Store handler for cleanup
    state.modal._keydownHandler = handleKeydown;
  }

  /**
   * Escape HTML for safe insertion
   */
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Log error to UI (if log function exists)
   */
  function logError(message) {
    if (typeof log === 'function') {
      log('<b>File Browser Error:</b> ' + escapeHtml(message), 'red');
    } else {
      console.error(message);
    }
  }

  // Public API
  return {
    open,
    close: closeModal
  };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
  module.exports = FileBrowser;
}

// Make available globally for non-module usage
window.FileBrowser = FileBrowser;