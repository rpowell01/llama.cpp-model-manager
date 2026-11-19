/**
 * Archive Browser Module
 * Provides a modal interface for browsing mnemonic-archive data
 */

(function() {
  'use strict';

  // State
  let currentModel = null;
  let currentView = 'conversations';
  let currentConversationId = null;
  let currentPieceId = null;
  let currentFilePath = null;
  
  // Pagination state
  let pagination = {
    conversations: { offset: 0, limit: 50, total: 0, sort: 'updated_desc', query: '' },
    journal: { offset: 0, limit: 100, total: 0, eventType: '', fromTs: '', toTs: '' },
    search: { offset: 0, limit: 50, total: 0, query: '', mode: 'default', queries: [] },
    fileHistory: { offset: 0, limit: 50, total: 0 },
    fileHistoryFiles: { offset: 0, limit: 50, total: 0 }
  };

  // VirtualList instances for each view
  let virtualLists = {
    conversations: null,
    journal: null,
    search: null,
    fileHistory: null,
    fileHistoryFiles: null
  };

  // Store current data arrays for virtualization
  let currentData = {
    conversations: [],
    journal: [],
    search: [],
    fileHistory: [],
    fileHistoryFiles: []
  };
  
  // Track selected file for file history detail
  let selectedFilePath = null;
  
  // Debounce timer for search
  let searchDebounceTimer = null;
  const SEARCH_DEBOUNCE_MS = 300;

  // DOM Elements
  const elements = {
    modal: null,
    modelSelect: null,
    closeBtn: null,
    tabs: {},
    views: {},
    controls: {},
    lists: {},
    pagination: {},
    detailPanel: null,
    breadcrumbs: null,
    statusText: null
  };

  // Initialize DOM references
  function cacheElements() {
    elements.modal = document.getElementById('archive-browser-modal');
    elements.modelSelect = document.getElementById('archive-model-select');
    elements.closeBtn = document.getElementById('archive-browser-close');
    elements.breadcrumbs = document.getElementById('archive-breadcrumbs');
    elements.statusText = document.getElementById('archive-status-text');
    elements.detailPanel = document.getElementById('archive-detail-panel');
    
    // Tabs
    elements.tabs.conversations = document.querySelector('.archive-tab-btn[data-view="conversations"]');
    elements.tabs.journal = document.querySelector('.archive-tab-btn[data-view="journal"]');
    elements.tabs.search = document.querySelector('.archive-tab-btn[data-view="search"]');
    elements.tabs.fileHistory = document.querySelector('.archive-tab-btn[data-view="fileHistory"]');
    
    // Views
    elements.views.conversations = document.getElementById('archive-conversations-view');
    elements.views.journal = document.getElementById('archive-journal-view');
    elements.views.search = document.getElementById('archive-search-view');
    elements.views.fileHistory = document.getElementById('archive-file-history-view');
    
    // Controls
    elements.controls.conversations = document.getElementById('archive-conversations-controls');
    elements.controls.journal = document.getElementById('archive-journal-controls');
    elements.controls.search = document.getElementById('archive-search-controls');
    elements.controls.fileHistory = document.getElementById('archive-file-history-controls');
    
    // Lists
    elements.lists.conversations = document.getElementById('archive-conversations-list');
    elements.lists.journal = document.getElementById('archive-journal-list');
    elements.lists.search = document.getElementById('archive-search-results');
    elements.lists.fileHistory = document.getElementById('archive-file-history-list');
    
    // Pagination
    elements.pagination.conversations = {
      prev: document.getElementById('archive-conv-prev'),
      next: document.getElementById('archive-conv-next'),
      info: document.getElementById('archive-conv-page-info'),
      count: document.getElementById('archive-conv-count')
    };
    elements.pagination.journal = {
      prev: document.getElementById('archive-journal-prev'),
      next: document.getElementById('archive-journal-next'),
      info: document.getElementById('archive-journal-page-info'),
      count: document.getElementById('archive-journal-count')
    };
    elements.pagination.search = {
      prev: document.getElementById('archive-search-prev'),
      next: document.getElementById('archive-search-next'),
      info: document.getElementById('archive-search-page-info'),
      count: document.getElementById('archive-search-count')
    };
    elements.pagination.fileHistory = {
      prev: document.getElementById('archive-file-history-prev'),
      next: document.getElementById('archive-file-history-next'),
      info: document.getElementById('archive-file-history-page-info'),
      count: document.getElementById('archive-file-history-count')
    };
    
    // Control inputs
    elements.convSearch = document.getElementById('archive-conv-search');
    elements.convSort = document.getElementById('archive-conv-sort');
    elements.journalEventType = document.getElementById('archive-journal-event-type');
    elements.fileHistoryCount = document.getElementById('archive-file-history-count');
    elements.journalFrom = document.getElementById('archive-journal-from');
    elements.journalTo = document.getElementById('archive-journal-to');
    elements.searchQuery = document.getElementById('archive-search-query');
    elements.searchMode = document.getElementById('archive-search-mode');
    elements.searchBtn = document.getElementById('archive-search-btn');
    elements.fileHistoryBtn = document.getElementById('archive-file-history-btn');
    elements.fileHistoryRefresh = document.getElementById('archive-file-history-refresh');
    elements.fileHistoryDetail = document.getElementById('archive-file-history-detail');
    elements.fileHistoryDetailContent = document.getElementById('archive-file-history-detail-content');
  }

  // Set status text
  function setStatus(text) {
    if (elements.statusText) {
      elements.statusText.textContent = text;
    }
  }

  // Show/hide views
  function showView(viewName) {
    currentView = viewName;
    
    // Update tabs
    Object.keys(elements.tabs).forEach(key => {
      const tab = elements.tabs[key];
      if (tab) {
        if (key === viewName) {
          tab.classList.add('active');
          tab.style.background = 'var(--btn-goto)';
          tab.style.color = '#fff';
          tab.style.borderColor = 'var(--blue)';
        } else {
          tab.classList.remove('active');
          tab.style.background = 'var(--surface2)';
          tab.style.color = 'var(--text)';
          tab.style.borderColor = 'var(--surface2)';
        }
      }
    });
    
    // Update views
    Object.keys(elements.views).forEach(key => {
      const view = elements.views[key];
      if (view) {
        view.style.display = key === viewName ? 'flex' : 'none';
      }
    });
    
    // Update controls
    Object.keys(elements.controls).forEach(key => {
      const control = elements.controls[key];
      if (control) {
        control.style.display = key === viewName ? 'flex' : 'none';
      }
    });
    
    // Update right panel visibility based on view
    if (elements.detailPanel) {
      if (viewName === 'fileHistory') {
        // In fileHistory view, show the detail panel on the right
        // (it will be populated when a file is selected)
        elements.detailPanel.style.display = 'flex';
      } else {
        elements.detailPanel.style.display = 'flex';
      }
    }
    
    // Load data for the view if we have a model
    if (currentModel) {
      loadCurrentView();
    }
    
    updateBreadcrumbs();
    updateURLState();
  }

  // Update breadcrumbs
  function updateBreadcrumbs() {
    if (!elements.breadcrumbs) return;
    
    const modelName = currentModel ? currentModel : 'No model';
    let html = `<span class="archive-breadcrumb-item" data-level="model">${he(modelName)}</span>`;
    
    if (currentView !== 'conversations' || currentConversationId) {
      html += `<span class="archive-breadcrumb-separator">›</span>`;
      html += `<span class="archive-breadcrumb-item" data-level="view">${he(getViewLabel(currentView))}</span>`;
    }
    
    if (currentConversationId) {
      html += `<span class="archive-breadcrumb-separator">›</span>`;
      html += `<span class="archive-breadcrumb-item current" data-level="conversation">${he(currentConversationId.substring(0, 12))}…</span>`;
    }
    
    if (currentPieceId) {
      html += `<span class="archive-breadcrumb-separator">›</span>`;
      html += `<span class="archive-breadcrumb-item current" data-level="piece">${he(currentPieceId.substring(0, 12))}…</span>`;
    }
    
    if (currentFilePath) {
      html += `<span class="archive-breadcrumb-separator">›</span>`;
      html += `<span class="archive-breadcrumb-item current" data-level="file">${he(currentFilePath)}</span>`;
    }
    
    elements.breadcrumbs.innerHTML = html;
    
    // Add click handlers for breadcrumb navigation
    elements.breadcrumbs.querySelectorAll('.archive-breadcrumb-item[data-level]').forEach(item => {
      item.addEventListener('click', () => {
        const level = item.dataset.level;
        if (level === 'model') {
          // Stay on model, just clear deeper levels
          currentConversationId = null;
          currentPieceId = null;
          currentFilePath = null;
          showView('conversations');
        } else if (level === 'view') {
          currentConversationId = null;
          currentPieceId = null;
          currentFilePath = null;
          showView(currentView);
        } else if (level === 'conversation') {
          currentPieceId = null;
          loadConversationDetail(currentConversationId);
        }
      });
    });
  }

  // Get view label
  function getViewLabel(view) {
    const labels = {
      conversations: 'Conversations',
      journal: 'Journal',
      search: 'Search',
      fileHistory: 'File History'
    };
    return labels[view] || view;
  }

  // HTML escape - matches core.js he() implementation
  function he(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Escape regex special characters
  function reEscape(str) {
    return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // URL state management for deep-linking and back/forward navigation
  const ARCHIVE_URL_PARAM = 'archive';

// Internal flag to suppress history manipulation during programmatic restore
let _restoring = false;
  
  function serializeArchiveState() {
    const state = {
      model: currentModel,
      view: currentView,
      conversationId: currentConversationId,
      pieceId: currentPieceId,
      filePath: currentFilePath,
      // Include all pagination states (per-view) for deep-link restoration
      pagination: {
        conversations: { ...pagination.conversations },
        journal: { ...pagination.journal },
        search: { ...pagination.search },
        fileHistory: { ...pagination.fileHistory }
      }
    };
    
    // Include search query if in search view
    if (currentView === 'search') {
      state.searchQuery = pagination.search.query;
      state.searchMode = pagination.search.mode;
    }
    
    // Include journal filters if in journal view
    if (currentView === 'journal') {
      state.journalEventType = pagination.journal.eventType;
      state.journalFromTs = pagination.journal.fromTs;
      state.journalToTs = pagination.journal.toTs;
    }
    
    // Include conversations sort/filter if in conversations view
    if (currentView === 'conversations') {
      state.convSort = pagination.conversations.sort;
      state.convQuery = pagination.conversations.query;
    }
    
    // Include file history path if in fileHistory view
    if (currentView === 'fileHistory') {
      state.filePath = currentFilePath;
    }
    
    return state;
  }
  
  function updateURLState(replace = false) {
    // Skip history manipulation during programmatic restore
    if (_restoring) return;
    
    try {
      const state = serializeArchiveState();
      // Unicode-safe base64 encoding
      const jsonStr = JSON.stringify(state);
      const encoded = btoa(unescape(encodeURIComponent(jsonStr)));
      const url = new URL(window.location.href);
      url.searchParams.set(ARCHIVE_URL_PARAM, encoded);
      
      if (replace) {
        history.replaceState(state, '', url);
      } else {
        history.pushState(state, '', url);
      }
    } catch (e) {
      console.warn('Failed to update URL state:', e);
    }
  }
  
  function parseURLState() {
    try {
      const url = new URL(window.location.href);
      const encoded = url.searchParams.get(ARCHIVE_URL_PARAM);
      if (!encoded) return null;
      
      // Unicode-safe base64 decoding
      const jsonStr = decodeURIComponent(escape(atob(encoded)));
      const state = JSON.parse(jsonStr);
      return state;
    } catch (e) {
      console.warn('Failed to parse URL state:', e);
      return null;
    }
  }
  
  function restoreURLState(state) {
    if (!state) return;
    
    // Set restoring flag to suppress history pushes during restore
    _restoring = true;
    
    try {
      // Restore model
      if (state.model) {
        currentModel = state.model;
        if (elements.modelSelect) {
          elements.modelSelect.value = state.model;
        }
      }
      
      // Restore view
      if (state.view) {
        currentView = state.view;
      }
      
      // Restore conversation/piece/file state
      currentConversationId = state.conversationId || null;
      currentPieceId = state.pieceId || null;
      currentFilePath = state.filePath || null;
      
      // Restore pagination
      if (state.pagination) {
        Object.keys(pagination).forEach(key => {
          if (state.pagination[key]) {
            pagination[key] = { ...pagination[key], ...state.pagination[key] };
          }
        });
      }
      
      // Restore view-specific state
      if (state.searchQuery !== undefined) {
        pagination.search.query = state.searchQuery;
        if (elements.searchQuery) elements.searchQuery.value = state.searchQuery;
      }
      if (state.searchMode !== undefined) {
        // Validate restored search mode against supported values
        const supportedModes = ["default", "timeline", "first"];
        pagination.search.mode = supportedModes.includes(state.searchMode)
          ? state.searchMode
          : "default";
        if (elements.searchMode) elements.searchMode.value = pagination.search.mode;
      }
      if (state.journalEventType !== undefined) {
        pagination.journal.eventType = state.journalEventType;
        if (elements.journalEventType) elements.journalEventType.value = state.journalEventType;
      }
      // Helper to convert ISO string to local datetime-local format
      function isoToLocalDatetime(isoStr) {
        if (!isoStr) return '';
        try {
          const date = new Date(isoStr);
          // Convert to local timezone and format as YYYY-MM-DDThh:mm
          const year = date.getFullYear();
          const month = String(date.getMonth() + 1).padStart(2, '0');
          const day = String(date.getDate()).padStart(2, '0');
          const hours = String(date.getHours()).padStart(2, '0');
          const minutes = String(date.getMinutes()).padStart(2, '0');
          return `${year}-${month}-${day}T${hours}:${minutes}`;
        } catch {
          return '';
        }
      }
      
      if (state.journalFromTs !== undefined) {
        pagination.journal.fromTs = state.journalFromTs;
        if (elements.journalFrom) elements.journalFrom.value = isoToLocalDatetime(state.journalFromTs);
      }
      if (state.journalToTs !== undefined) {
        pagination.journal.toTs = state.journalToTs;
        if (elements.journalTo) elements.journalTo.value = isoToLocalDatetime(state.journalToTs);
      }
      if (state.convSort !== undefined) {
        pagination.conversations.sort = state.convSort;
        if (elements.convSort) elements.convSort.value = state.convSort;
      }
      if (state.convQuery !== undefined) {
        pagination.conversations.query = state.convQuery;
        if (elements.convSearch) elements.convSearch.value = state.convQuery;
      }
      
      // Update UI to match restored state (silent - no history push)
      showView(currentView);
      updateBreadcrumbs();
      
      // Load data for the restored view
      if (currentModel) {
        loadCurrentView();
      }
    } finally {
      // Clear restoring flag
      _restoring = false;
    }
  }
  
  function bindURLStateHandlers() {
    // Handle popstate (back/forward navigation)
    window.addEventListener('popstate', (e) => {
      if (e.state && e.state.model) {
        // State was pushed by us, restore it
        restoreURLState(e.state);
      } else {
        // State might be from URL param, parse it
        const state = parseURLState();
        if (state) {
          restoreURLState(state);
        }
      }
    });
  }

  // Format timestamp
  function formatTimestamp(ts) {
    if (!ts) return '-';
    try {
      const date = new Date(ts);
      return date.toLocaleString();
    } catch {
      return ts;
    }
  }

  // Format time range
  function formatTimeRange(created, updated) {
    const start = formatTimestamp(created);
    const end = formatTimestamp(updated);
    if (start === end) return start;
    return `${start} → ${end}`;
  }

  // Load model list into selector
  async function loadModelList() {
    if (!elements.modelSelect) return;
    
    try {
      setStatus('Loading models…');
      const resp = await fetch('/api/status');
      if (!resp.ok) throw new Error('Failed to load models');
      const data = await resp.json();
      
      // Filter models with proxy enabled
      const modelsWithProxy = (data.models || []).filter(m => 
        m.mnemonic_proxy && m.mnemonic_proxy.enabled
      );
      
      // Preserve current selection
      const currentValue = elements.modelSelect.value;
      
      // Build options
      let html = '<option value="">Select a model with proxy enabled</option>';
      modelsWithProxy.forEach(m => {
        const selected = m.model === currentValue ? 'selected' : '';
        html += `<option value="${he(m.model)}" ${selected}>${he(m.model)}</option>`;
      });
      
      elements.modelSelect.innerHTML = html;
      
      // If we had a selection and it's still valid, keep it
      if (currentValue && modelsWithProxy.some(m => m.model === currentValue)) {
        elements.modelSelect.value = currentValue;
        currentModel = currentValue;
        loadCurrentView();
      } else if (modelsWithProxy.length > 0) {
        // Auto-select first model
        elements.modelSelect.value = modelsWithProxy[0].model;
        currentModel = modelsWithProxy[0].model;
        loadCurrentView();
      }
      
      setStatus('Ready');
    } catch (e) {
      console.error('Failed to load model list:', e);
      setStatus('Error loading models');
      elements.modelSelect.innerHTML = '<option value="">Error loading models</option>';
    }
  }

  // Load current view data
  function loadCurrentView() {
    switch (currentView) {
      case 'conversations':
        loadConversations();
        break;
      case 'journal':
        loadJournal();
        break;
      case 'search':
        loadSearch();
        break;
      case 'fileHistory':
        // In the new two-pane layout, load the file list first
        loadFileHistoryFiles();
        // Initially hide the detail panel until a file is selected
        if (elements.fileHistoryDetail) {
          elements.fileHistoryDetail.style.display = 'none';
        }
        break;
    }
  }

  // Load conversations
  async function loadConversations() {
    if (!currentModel) return;
    
    const pg = pagination.conversations;
    const listEl = elements.lists.conversations;
    const paginationEl = elements.pagination.conversations;
    
    if (!listEl) return;
    
    listEl.innerHTML = '<div class="archive-loading">Loading conversations…</div>';
    setStatus('Loading conversations…');
    
    try {
      const params = new URLSearchParams({
        model: currentModel,
        limit: pg.limit,
        offset: pg.offset,
        sort: pg.sort
      });
      if (pg.query) params.append('query', pg.query);
      
      const resp = await fetch(`/api/proxy/archive/conversations?${params}`);
      const data = await resp.json();
      
      // Handle wrapped response format {success: true, data: {...}}
      const payload = (data && data.success && data.data) ? data.data : data;
      
      if (payload.error) throw new Error(payload.error || 'Failed to load conversations');
      
      const conversations = payload.conversations || [];
      pg.total = payload.total || payload.count || 0;
      
      renderConversations(conversations);
      updatePagination('conversations');
      updateCount('conversations', pg.total);
      setStatus(`Loaded ${conversations.length} of ${pg.total} conversations`);
    } catch (e) {
      console.error('Failed to load conversations:', e);
      listEl.innerHTML = `<div class="archive-error">Error: ${he(e.message)}</div>`;
      setStatus('Error loading conversations');
    }
  }

  // Render conversations list using VirtualList (div-based layout for proper virtualization)
  function renderConversations(conversations) {
    const listEl = elements.lists.conversations;
    if (!listEl) return;
    
    // Store data for virtualization
    currentData.conversations = conversations || [];
    
    if (currentData.conversations.length === 0) {
      listEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">💬</div><div>No conversations found</div></div>';
      // Clean up existing virtual list
      if (virtualLists.conversations) {
        virtualLists.conversations.destroy();
        virtualLists.conversations = null;
      }
      return;
    }
    
    // Item height estimate for conversation items (matches contain-intrinsic-size: 52px)
    const ITEM_HEIGHT = 52;
    
    // Render item function for VirtualList
    function renderConversationItem(conv, index) {
      const id = conv.id || '';
      const title = conv.title || '(untitled)';
      const created = conv.created_at || '';
      const updated = conv.updated_at || '';
      const msgCount = conv.message_count || 0;
      const tokenCount = conv.token_count || 0;
      const segCount = conv.segment_count || 0;
      
      // Get preview from first message or title
      let preview = title;
      if (conv.preview) {
        preview = conv.preview.substring(0, 80);
      }
      
      let html = `<div class="archive-conversation-item" data-conv-id="${he(id)}">`;
      html += `<div class="conv-row">`;
      html += `<div class="conv-col conv-col-id"><div class="conv-id">${he(id.substring(0, 20))}…</div><div class="conv-title" title="${he(title)}">${he(title)}</div></div>`;
      html += `<div class="conv-col conv-col-time">${he(formatTimeRange(created, updated))}</div>`;
      html += `<div class="conv-col conv-col-stats">${msgCount} / ${tokenCount.toLocaleString()} / ${segCount}</div>`;
      html += `<div class="conv-col conv-col-preview" title="${he(preview)}">${he(preview)}</div>`;
      html += `</div>`;
      html += `</div>`;
      return html;
    }
    
    // Initialize or update VirtualList
    if (!virtualLists.conversations) {
      virtualLists.conversations = new VirtualList({
        container: listEl,
        items: currentData.conversations,
        renderItem: renderConversationItem,
        itemHeight: ITEM_HEIGHT,
        buffer: 5,
        getItemId: (conv) => conv.id || '',
        selectedId: currentConversationId,
        onItemClick: (conv, index) => {
          const convId = conv.id;
          selectConversation(convId);
          loadConversationDetail(convId);
        }
      });
    } else {
      virtualLists.conversations.setItems(currentData.conversations, currentConversationId);
    }
  }

  // Select conversation (highlight row) - called from VirtualList click handler
  function selectConversation(convId) {
    currentConversationId = convId;
    updateBreadcrumbs();
    updateURLState();
    
    // Update VirtualList selection if it exists
    if (virtualLists.conversations) {
      const index = currentData.conversations.findIndex(c => c.id === convId);
      if (index >= 0) {
        virtualLists.conversations.setSelectedIndex(index);
      }
    }
    
    // Load journal for this conversation
    if (currentView === 'journal') {
      loadJournal(convId);
    }
  }

  // Load conversation detail
  async function loadConversationDetail(convId) {
    if (!currentModel || !convId) return;
    
    const detailEl = elements.detailPanel;
    if (!detailEl) return;
    
    detailEl.innerHTML = '<div class="archive-loading">Loading conversation detail…</div>';
    setStatus('Loading conversation detail…');
    
    try {
      // Get journal events for this conversation
      const params = new URLSearchParams({
        model: currentModel,
        conversation_id: convId,
        limit: 200
      });
      
      const resp = await fetch(`/api/proxy/archive/journal?${params}`);
      const data = await resp.json();
      
      // Handle wrapped response format {success: true, data: {...}}
      const payload = (data && data.success && data.data) ? data.data : data;
      
      if (payload.error) throw new Error(payload.error || 'Failed to load journal');
      
      const events = payload.events || [];
      renderConversationDetail(convId, events);
      setStatus(`Loaded conversation ${convId.substring(0, 12)}…`);
    } catch (e) {
      console.error('Failed to load conversation detail:', e);
      detailEl.innerHTML = `<div class="archive-error">Error: ${he(e.message)}</div>`;
      setStatus('Error loading conversation detail');
    }
  }

  // Render conversation detail as timeline
  function renderConversationDetail(convId, events) {
    const detailEl = elements.detailPanel;
    if (!detailEl) return;
    
    let html = `
      <div class="archive-detail-header">
        <div>
          <div class="archive-detail-title">Conversation Timeline</div>
          <div class="archive-detail-id">${he(convId)}</div>
        </div>
      </div>
    `;
    
    if (events.length === 0) {
      html += '<div class="archive-empty"><div class="archive-empty-icon">📋</div><div>No journal events for this conversation</div></div>';
      detailEl.innerHTML = html;
      return;
    }
    
    // Group events by segment
    const segments = {};
    events.forEach(event => {
      const seg = event.segment || 0;
      if (!segments[seg]) segments[seg] = [];
      segments[seg].push(event);
    });
    
    html += '<div class="archive-detail-content">';
    
    Object.keys(segments).sort((a, b) => a - b).forEach(segNum => {
      const segEvents = segments[segNum];
      html += `<div class="archive-detail-section">`;
      html += `<h4>Segment ${segNum} (${segEvents.length} events)</h4>`;
      
      segEvents.forEach(event => {
        const eventType = event.event_type || 'unknown';
        const timestamp = formatTimestamp(event.timestamp);
        const data = event.data ? JSON.stringify(event.data, null, 2) : '{}';
        
        html += `<details style="margin-bottom: 0.5rem; border: 1px solid var(--surface2); border-radius: 4px; overflow: hidden;">`;
        html += `<summary style="padding: 0.5rem; background: var(--surface2); cursor: pointer; display: flex; justify-content: space-between; align-items: center;">`;
        html += `<span><span class="archive-journal-item journal-event-type ${he(eventType)}">${he(eventType)}</span> ${he(timestamp)}</span>`;
        html += `<span style="font-size: 0.7rem; color: var(--text2); font-family: monospace;">${he(event.id || '')}</span>`;
        html += `</summary>`;
        html += `<div style="padding: 0.5rem; font-size: 0.75rem;">`;
        html += `<pre style="margin: 0; white-space: pre-wrap; word-break: break-word;">${he(data)}</pre>`;
        html += `</div>`;
        html += `</details>`;
      });
      
      html += `</div>`;
    });
    
    html += '</div>';
    detailEl.innerHTML = html;
  }

  // Load journal
  async function loadJournal(conversationId = null) {
    if (!currentModel) return;
    
    const pg = pagination.journal;
    const listEl = elements.lists.journal;
    const paginationEl = elements.pagination.journal;
    
    if (!listEl) return;
    
    listEl.innerHTML = '<div class="archive-loading">Loading journal…</div>';
    setStatus('Loading journal…');
    
    try {
      const params = new URLSearchParams({
        model: currentModel,
        limit: pg.limit,
        offset: pg.offset
      });
      if (conversationId) params.append('conversation_id', conversationId);
      if (pg.eventType) params.append('event_type', pg.eventType);
      if (pg.fromTs) params.append('from_timestamp', pg.fromTs);
      if (pg.toTs) params.append('to_timestamp', pg.toTs);
      
      const resp = await fetch(`/api/proxy/archive/journal?${params}`);
      const data = await resp.json();
      
      // Handle wrapped response format {success: true, data: {...}}
      const payload = (data && data.success && data.data) ? data.data : data;
      
      if (payload.error) throw new Error(payload.error || 'Failed to load journal');
      
      const events = payload.events || [];
      pg.total = payload.total || 0;
      
      renderJournal(events);
      updatePagination('journal');
      updateCount('journal', pg.total);
      setStatus(`Loaded ${events.length} of ${pg.total} journal events`);
    } catch (e) {
      console.error('Failed to load journal:', e);
      listEl.innerHTML = `<div class="archive-error">Error: ${he(e.message)}</div>`;
      setStatus('Error loading journal');
    }
  }

  // Render journal list using VirtualList
  function renderJournal(events) {
    const listEl = elements.lists.journal;
    if (!listEl) return;
    
    // Store data for virtualization
    currentData.journal = events || [];
    
    if (currentData.journal.length === 0) {
      listEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">📋</div><div>No journal events found</div></div>';
      // Clean up existing virtual list
      if (virtualLists.journal) {
        virtualLists.journal.destroy();
        virtualLists.journal = null;
      }
      return;
    }
    
    // Item height estimate for journal items (accounts for wrapped journal-data content)
    const ITEM_HEIGHT = 100;
    
    // Render item function for VirtualList
    function renderJournalItem(event, index) {
      const id = event.id || '';
      const eventType = event.event_type || 'unknown';
      const timestamp = formatTimestamp(event.timestamp);
      const convId = event.conversation_id || '';
      const data = event.data ? JSON.stringify(event.data).substring(0, 200) : '{}';
      
      let html = `<div class="archive-journal-item" data-event-id="${he(id)}" data-conv-id="${he(convId)}">`;
      html += `<div class="journal-header">`;
      html += `<span class="journal-event-type ${he(eventType)}">${he(eventType)}</span>`;
      html += `<span class="journal-timestamp">${he(timestamp)}</span>`;
      html += `</div>`;
      if (convId) {
        html += `<div class="journal-conversation">Conversation: ${he(convId.substring(0, 16))}…</div>`;
      }
      html += `<div class="journal-data">${he(data)}${event.data && JSON.stringify(event.data).length > 200 ? '…' : ''}</div>`;
      html += `</div>`;
      return html;
    }
    
    // Initialize or update VirtualList
    if (!virtualLists.journal) {
      virtualLists.journal = new VirtualList({
        container: listEl,
        items: currentData.journal,
        renderItem: renderJournalItem,
        itemHeight: ITEM_HEIGHT,
        buffer: 5,
        // Journal events don't have unique IDs, use index as identifier
        getItemId: (event, index) => `journal-${index}`,
        onItemClick: (event, index) => {
          const convId = event.conversation_id;
          selectJournalEvent(index, convId);
        }
      });
    } else {
      // setItems now auto-preserves selection when selectedId is not passed
      virtualLists.journal.setItems(currentData.journal);
    }
  }

  // Select journal event - called from VirtualList click handler
  // eventId is actually the index since journal events don't have unique IDs
  function selectJournalEvent(eventIndex, convId) {
    if (convId) {
      currentConversationId = convId;
      updateBreadcrumbs();
      
      // Load conversation detail which shows the timeline with journal events
      loadConversationDetail(convId);
    } else {
      // No conversation_id - show the full event data in detail panel
      const event = currentData.journal[eventIndex];
      if (event) {
        renderJournalEventDetail(event);
      }
    }
    
    // Update VirtualList selection if it exists
    if (virtualLists.journal) {
      virtualLists.journal.setSelectedIndex(eventIndex);
    }
    
    updateURLState();
  }

  // Render full journal event detail in the detail panel
  function renderJournalEventDetail(event) {
    const detailEl = elements.detailPanel;
    if (!detailEl) return;
    
    const eventType = event.event_type || 'unknown';
    const timestamp = formatTimestamp(event.timestamp);
    const convId = event.conversation_id || '';
    const data = event.data || {};
    
    let html = `<div class="archive-piece-detail">`;
    html += `<div class="piece-header">`;
    html += `<span class="piece-role ${he(eventType)}">${he(eventType)}</span>`;
    html += `<span class="piece-time">${he(timestamp)}</span>`;
    html += `</div>`;
    
    if (convId) {
      html += `<div class="piece-conversation">`;
      html += `<span class="label">Conversation:</span> `;
      html += `<span class="value" style="font-family: monospace;">${he(convId)}</span>`;
      html += `</div>`;
    }
    
    html += `<div class="piece-content">`;
    html += `<pre class="json-content">${he(JSON.stringify(data, null, 2))}</pre>`;
    html += `</div>`;
    html += `</div>`;
    
    detailEl.innerHTML = html;
  }

  // Load search
  async function loadSearch() {
    if (!currentModel) return;
    
    const pg = pagination.search;
    const listEl = elements.lists.search;
    const paginationEl = elements.pagination.search;
    
    if (!listEl) return;
    
    if (!pg.query && pg.queries.length === 0) {
      listEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">🔍</div><div>Enter a search query and click Search</div></div>';
      updatePagination('search');
      updateCount('search', 0);
      return;
    }
    
    listEl.innerHTML = '<div class="archive-loading">Searching…</div>';
    setStatus('Searching…');
    
    try {
      const params = new URLSearchParams({
        model: currentModel,
        limit: pg.limit,
        offset: pg.offset,
        mode: pg.mode
      });
      if (pg.query) params.append('query', pg.query);
      if (pg.queries.length > 0) params.append('queries', pg.queries.join(','));
      if (currentConversationId) params.append('conversation_id', currentConversationId);
      
      const resp = await fetch(`/api/proxy/archive/recall?${params}`);
      const data = await resp.json();
      
      // Handle wrapped response format {success: true, data: {...}}
      const payload = (data && data.success && data.data) ? data.data : data;
      
      if (payload.error) throw new Error(payload.error || 'Search failed');
      
      const pieces = payload.recall?.results || [];
      pg.total = payload.recall?.total || 0;
      
      renderSearchResults(pieces);
      updatePagination('search');
      updateCount('search', pg.total);
      setStatus(`Found ${pieces.length} of ${pg.total} results`);
    } catch (e) {
      console.error('Search failed:', e);
      listEl.innerHTML = `<div class="archive-error">Error: ${he(e.message)}</div>`;
      setStatus('Search failed');
    }
  }

  // Render search results using VirtualList
  function renderSearchResults(pieces) {
    const listEl = elements.lists.search;
    if (!listEl) return;
    
    // Store data for virtualization
    currentData.search = pieces || [];
    
    if (currentData.search.length === 0) {
      listEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">🔍</div><div>No results found</div></div>';
      // Clean up existing virtual list
      if (virtualLists.search) {
        virtualLists.search.destroy();
        virtualLists.search = null;
      }
      return;
    }
    
    // Item height estimate for search results (accounts for wrapped search-result-content)
    const ITEM_HEIGHT = 120;
    
    // Render item function for VirtualList
    function renderSearchItem(piece, index) {
      const id = piece.id || '';
      const role = piece.role || 'unknown';
      const segment = piece.segment || 0;
      const msgIndex = piece.message_index || 0;
      const content = piece.content || '';
      const created = formatTimestamp(piece.created_at);
      const convId = piece.conversation_id || '';
      
      // Highlight search terms in content
      let displayContent = he(content.substring(0, 150));
      if (pagination.search.query) {
        const terms = pagination.search.query.split(' ').filter(t => t.length > 1);
        terms.forEach(term => {
          const regex = new RegExp(`(${reEscape(he(term))})`, 'gi');
          displayContent = displayContent.replace(regex, '<mark class="search-result-highlight">$1</mark>');
        });
      }
      
      let html = `<div class="archive-search-result" data-piece-id="${he(id)}" data-conv-id="${he(convId)}">`;
      html += `<div class="search-result-header">`;
      html += `<span class="search-result-role ${he(role)}">${he(role)}</span>`;
      html += `<span style="font-size: 0.7rem; color: var(--text2); font-family: monospace;">seg:${segment} msg:${msgIndex}</span>`;
      html += `</div>`;
      html += `<div class="search-result-meta">`;
      html += `${he(created)} | Conv: ${he(convId.substring(0, 12))}…`;
      html += `</div>`;
      html += `<div class="search-result-content">${displayContent}${content.length > 150 ? '…' : ''}</div>`;
      html += `</div>`;
      return html;
    }
    
    // Initialize or update VirtualList
    if (!virtualLists.search) {
      virtualLists.search = new VirtualList({
        container: listEl,
        items: currentData.search,
        renderItem: renderSearchItem,
        itemHeight: ITEM_HEIGHT,
        buffer: 5,
        getItemId: (piece) => piece.id || '',
        onItemClick: (piece, index) => {
          const pieceId = piece.id;
          const convId = piece.conversation_id;
          selectSearchResult(pieceId, convId);
        }
      });
    } else {
      // setItems now auto-preserves selection when selectedId is not passed
      virtualLists.search.setItems(currentData.search);
    }
  }

  // Select search result - called from VirtualList click handler
  function selectSearchResult(pieceId, convId) {
    if (convId) {
      currentConversationId = convId;
    }
    currentPieceId = pieceId;
    updateBreadcrumbs();
    
    // Update VirtualList selection if it exists
    if (virtualLists.search) {
      const index = currentData.search.findIndex(p => p.id === pieceId);
      if (index >= 0) {
        virtualLists.search.setSelectedIndex(index);
      }
    }
    
    loadPieceDetail(pieceId);
    updateURLState();
  }

  // Load file history files (list of unique file paths)
  async function loadFileHistoryFiles() {
    if (!currentModel) return;
    
    const listEl = elements.lists?.fileHistory;
    const paginationEl = elements.pagination?.fileHistory;
    const countEl = elements.fileHistoryCount;
    
    if (!listEl) {
      console.error('loadFileHistoryFiles: listEl not found, elements.lists:', elements.lists);
      return;
    }
    
    listEl.innerHTML = '<div class="archive-loading">Loading file list…</div>';
    setStatus('Loading file list…');
    
    try {
      const resp = await fetch(`/api/proxy/archive/files?model=${currentModel}&limit=100`);
      const data = await resp.json();
      
      // Handle wrapped response format {success: true, data: {...}}
      const payload = (data && data.success && data.data) ? data.data : data;
      
      if (payload.error) throw new Error(payload.error || 'Failed to load file list');
      
      const files = payload.files || [];
      // Safely update pagination total
      if (paginationEl) {
        paginationEl.total = payload.total || files.length;
      }
      
      currentData.fileHistoryFiles = files || [];
      
      // Update count
      if (countEl) {
        countEl.textContent = `${files.length} files`;
      }
      
      // Render the file list using VirtualList
      if (virtualLists.fileHistoryFiles) {
        virtualLists.fileHistoryFiles.destroy();
      }
      
      // Render item function for file list
      function renderFileHistoryFile(file, index) {
        const path = file.path || 'Unknown path';
        const occurrences = file.occurrences || 0;
        
        return `<div class="archive-file-list-item" data-file-path="${he(path)}" data-file-index="${index}">
          <span class="file-list-path">${he(path.substring(0, 50))}…</span>
          <span class="file-list-occurrences">${occurrences} occurrences</span>
        </div>`;
      }
      
      virtualLists.fileHistoryFiles = new VirtualList({
        container: listEl,
        items: currentData.fileHistoryFiles,
        renderItem: renderFileHistoryFile,
        onItemClick: (file) => {
          selectFileHistoryFile(file);
        }
      });
      
      virtualLists.fileHistoryFiles.setItems(currentData.fileHistoryFiles);
      
      setStatus(`Loaded ${files.length} files from archive`);
    } catch (e) {
      console.error('Failed to load file list:', e);
      // Safely update innerHTML if listEl exists
      if (listEl) {
        listEl.innerHTML = `<div class="archive-error">Error: ${he(e.message)}</div>`;
      }
      setStatus('Error loading file list');
    }
  }

  // Select a file from the file history list
  function selectFileHistoryFile(file) {
    const path = file.path || '';
    selectedFilePath = path;
    
    // Update VirtualList selection
    if (virtualLists.fileHistoryFiles) {
      virtualLists.fileHistoryFiles.setSelectedIndex(
        currentData.fileHistoryFiles.findIndex(f => f.path === path)
      );
    }
    
    // Load the file's history and show the detail panel
    loadFileHistoryDetail(path);
  }

  // Load file history detail for a specific file
  async function loadFileHistoryDetail(path) {
    if (!currentModel || !path) return;
    
    const listEl = elements.lists.fileHistory;
    const paginationEl = elements.pagination.fileHistory;
    const countEl = elements.fileHistoryCount;
    const detailEl = elements.fileHistoryDetail;
    const detailContentEl = elements.fileHistoryDetailContent;
    
    if (!listEl || !detailEl || !detailContentEl) return;
    
    // Show loading state in detail panel
    detailContentEl.innerHTML = '<div class="archive-loading">Loading file history…</div>';
    detailEl.style.display = 'block';
    
    setStatus(`Loading file history for ${path.substring(0, 30)}…`);
    
    try {
      // Load file history using the recall API with path filter
      const resp = await fetch(`/api/proxy/archive/recall?model=${currentModel}&path=${encodeURIComponent(path)}&limit=50`);
      const data = await resp.json();
      
      // Handle wrapped response format {success: true, data: {...}}
      const payload = (data && data.success && data.data) ? data.data : data;
      
      if (payload.error) throw new Error(payload.error || 'Failed to load file history');
      
      const pieces = payload.recall?.results || [];
      paginationEl.total = payload.recall?.total || pieces.length;
      
      // Render the file history detail
      renderFileHistoryDetail(pieces);
      
      // Update count
      if (countEl) {
        countEl.textContent = `${pieces.length} entries`;
      }
      
      setStatus(`Loaded ${pieces.length} file history entries for ${path.substring(0, 30)}…`);
    } catch (e) {
      console.error('Failed to load file history detail:', e);
      detailContentEl.innerHTML = `<div class="archive-error">Error: ${he(e.message)}</div>`;
      setStatus('Error loading file history');
    }
  }

  // Render file history detail
  function renderFileHistoryDetail(pieces) {
    const detailContentEl = elements.fileHistoryDetailContent;
    if (!detailContentEl) return;
    
    if (pieces.length === 0) {
      detailContentEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">📁</div><div>No history found for this file</div></div>';
      return;
    }
    
    let html = `<div class="archive-file-history-detail">`;
    html += `<div class="file-history-detail-header">`;
    html += `<span class="file-history-detail-path">${he(pieces[0]?.path || 'Unknown')}</span>`;
    html += `</div>`;
    html += `<div class="file-history-detail-list">`;
    
    pieces.forEach((piece, i) => {
      const id = piece.id || '';
      const role = piece.role || 'unknown';
      const timestamp = formatTimestamp(piece.created_at);
      const convId = piece.conversation_id || '';
      const segment = piece.segment || 0;
      const msgIndex = piece.message_index || 0;
      
      let operation = role;
      if (piece.metadata) {
        try {
          const meta = typeof piece.metadata === 'string' ? JSON.parse(piece.metadata) : piece.metadata;
          if (meta.operation) operation = meta.operation;
        } catch {}
      }
      
      html += `<div class="archive-file-history-detail-item" data-piece-id="${he(id)}" data-conv-id="${he(convId)}">`;
      html += `<div class="file-history-detail-operation">${he(operation)}</span>`;
      html += `<span class="file-history-detail-timestamp">${he(timestamp)}</span>`;
      html += `<span class="file-history-detail-conversation">Segment: ${segment}, Message: ${msgIndex} | Conv: ${he(convId.substring(0, 16))}…</div>`;
      html += `</div>`;
    });
    
    html += `</div></div>`;
    detailContentEl.innerHTML = html;
  }

  // Render file history using VirtualList
  function renderFileHistory(pieces) {
    const listEl = elements.lists.fileHistory;
    if (!listEl) return;
    
    // Store data for virtualization
    currentData.fileHistory = pieces || [];
    
    if (currentData.fileHistory.length === 0) {
      listEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">📁</div><div>No history found for this file</div></div><div class="archive-empty-note">This can happen when the model\'s fileops table is empty. File history tracks read/write operations per model.</div>';
      // Clean up existing virtual list
      if (virtualLists.fileHistory) {
        virtualLists.fileHistory.destroy();
        virtualLists.fileHistory = null;
      }
      return;
    }
    
    // Item height estimate for file history items
    const ITEM_HEIGHT = 72;
    
    // Render item function for VirtualList
    function renderFileHistoryItem(piece, index) {
      const id = piece.id || '';
      const role = piece.role || 'unknown';
      const timestamp = formatTimestamp(piece.created_at);
      const convId = piece.conversation_id || '';
      const segment = piece.segment || 0;
      const msgIndex = piece.message_index || 0;
      
      // Try to determine operation type from metadata or role
      let operation = role;
      if (piece.metadata) {
        try {
          const meta = typeof piece.metadata === 'string' ? JSON.parse(piece.metadata) : piece.metadata;
          if (meta.operation) operation = meta.operation;
        } catch {}
      }
      
      let html = `<div class="archive-file-history-item" data-piece-id="${he(id)}" data-conv-id="${he(convId)}">`;
      html += `<div class="file-history-header">`;
      html += `<span class="file-history-operation">${he(operation)}</span>`;
      html += `<span class="file-history-timestamp">${he(timestamp)}</span>`;
      html += `</div>`;
      html += `<div class="file-history-conversation">Segment: ${segment}, Message: ${msgIndex} | Conv: ${he(convId.substring(0, 16))}…</div>`;
      html += `</div>`;
      return html;
    }
    
    // Initialize or update VirtualList
    if (!virtualLists.fileHistory) {
      virtualLists.fileHistory = new VirtualList({
        container: listEl,
        items: currentData.fileHistory,
        renderItem: renderFileHistoryItem,
        itemHeight: ITEM_HEIGHT,
        buffer: 5,
        getItemId: (piece) => piece.id || '',
        onItemClick: (piece, index) => {
          const pieceId = piece.id;
          const convId = piece.conversation_id;
          selectFileHistoryItem(pieceId, convId);
        }
      });
    } else {
      // setItems now auto-preserves selection when selectedId is not passed
      virtualLists.fileHistory.setItems(currentData.fileHistory);
    }
  }

  // Select file history item - called from VirtualList click handler
  function selectFileHistoryItem(pieceId, convId) {
    if (convId) {
      currentConversationId = convId;
    }
    currentPieceId = pieceId;
    updateBreadcrumbs();
    
    // Update VirtualList selection if it exists
    if (virtualLists.fileHistory) {
      const index = currentData.fileHistory.findIndex(p => p.id === pieceId);
      if (index >= 0) {
        virtualLists.fileHistory.setSelectedIndex(index);
      }
    }
    
    loadPieceDetail(pieceId);
    updateURLState();
  }

  // Load piece detail (full inspector)
  async function loadPieceDetail(pieceId) {
    if (!currentModel || !pieceId) return;
    
    const detailEl = elements.detailPanel;
    if (!detailEl) return;
    
    detailEl.innerHTML = '<div class="archive-loading">Loading piece detail…</div>';
    setStatus('Loading piece detail…');
    
    try {
      const params = new URLSearchParams({
        model: currentModel,
        id: pieceId,
        limit: 1
      });
      
      const resp = await fetch(`/api/proxy/archive/recall?${params}`);
      const data = await resp.json();
      
      // Handle wrapped response format {success: true, data: {...}}
      const payload = (data && data.success && data.data) ? data.data : data;
      
      if (payload.error) throw new Error(payload.error || 'Failed to load piece');
      
      const pieces = payload.recall?.results || [];
      if (pieces.length === 0) {
        throw new Error('Piece not found');
      }
      
      const piece = pieces[0];
      renderPieceDetail(piece);
      setStatus(`Loaded piece ${pieceId.substring(0, 12)}…`);
    } catch (e) {
      console.error('Failed to load piece detail:', e);
      detailEl.innerHTML = `<div class="archive-error">Error: ${he(e.message)}</div>`;
      setStatus('Error loading piece detail');
    }
  }

  // Render piece detail with tabs
  function renderPieceDetail(piece) {
    const detailEl = elements.detailPanel;
    if (!detailEl) return;
    
    const id = piece.id || '';
    const role = piece.role || 'unknown';
    const segment = piece.segment || 0;
    const msgIndex = piece.message_index || 0;
    const content = piece.content || '';
    const created = formatTimestamp(piece.created_at);
    const convId = piece.conversation_id || '';
    const tokens = piece.tokens || 0;
    const name = piece.name || '';
    // These fields don't exist in the real archive schema
    const metadata = piece.metadata || {};
    const requestContext = piece.request_context || {};
    const recallUse = piece.recall_use || {};
    const output = piece.output || '';
    const reasoning = piece.reasoning || '';
    
    // Determine which tabs have data
    const hasMetadata = Object.keys(metadata).length > 0;
    const hasRequestContext = Object.keys(requestContext).length > 0;
    const hasRecallUse = Object.keys(recallUse).length > 0;
    const hasOutput = output && output.length > 0;
    const hasReasoning = reasoning && reasoning.length > 0;
    
    // Build tab buttons dynamically based on available data
    let tabButtons = `
      <button class="archive-detail-tab active" data-tab="content">Content</button>
    `;
    if (hasMetadata) tabButtons += `<button class="archive-detail-tab" data-tab="metadata">Metadata</button>`;
    if (hasRequestContext) tabButtons += `<button class="archive-detail-tab" data-tab="request">Request Context</button>`;
    if (hasRecallUse) tabButtons += `<button class="archive-detail-tab" data-tab="recall">Recall Use</button>`;
    if (hasOutput) tabButtons += `<button class="archive-detail-tab" data-tab="output">Output</button>`;
    if (hasReasoning) tabButtons += `<button class="archive-detail-tab" data-tab="reasoning">Reasoning</button>`;
    
    // If no extra tabs have data, add a Basic Info tab
    if (!hasMetadata && !hasRequestContext && !hasRecallUse && !hasOutput && !hasReasoning) {
      tabButtons += `<button class="archive-detail-tab" data-tab="basic">Basic Info</button>`;
    }
    
    let html = `
      <div class="archive-detail-header">
        <div>
          <div class="archive-detail-title">${he(role.charAt(0).toUpperCase() + role.slice(1))} Piece</div>
          <div class="archive-detail-id">${he(id)}</div>
        </div>
      </div>
      
      <div class="archive-detail-tabs">
        ${tabButtons}
      </div>
      
      <div class="archive-detail-content">
        <!-- Content Tab -->
        <div class="archive-tab-panel" data-tab="content" style="display: block;">
          <div class="archive-detail-section">
            <h4>Content</h4>
            <pre>${he(content)}</pre>
          </div>
          
          <div class="archive-detail-section">
            <h4>Basic Info</h4>
            <div class="archive-detail-meta">
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Role</div>
                <div class="archive-detail-meta-value">${he(role)}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Segment</div>
                <div class="archive-detail-meta-value">${segment}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Message Index</div>
                <div class="archive-detail-meta-value">${msgIndex}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Created</div>
                <div class="archive-detail-meta-value">${he(created)}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Conversation</div>
                <div class="archive-detail-meta-value">${he(convId)}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Tokens</div>
                <div class="archive-detail-meta-value">${tokens}</div>
              </div>
              ${name ? `<div class="archive-detail-meta-item"><div class="archive-detail-meta-label">Name</div><div class="archive-detail-meta-value">${he(name)}</div></div>` : ''}
            </div>
          </div>
        </div>
        
        <!-- Basic Info Tab (shown when no extra tabs have data) -->
        ${!hasMetadata && !hasRequestContext && !hasRecallUse && !hasOutput && !hasReasoning ? `
        <div class="archive-tab-panel" data-tab="basic" style="display: none;">
          <div class="archive-detail-section">
            <h4>Basic Info</h4>
            <div class="archive-detail-meta">
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Role</div>
                <div class="archive-detail-meta-value">${he(role)}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Segment</div>
                <div class="archive-detail-meta-value">${segment}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Message Index</div>
                <div class="archive-detail-meta-value">${msgIndex}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Created</div>
                <div class="archive-detail-meta-value">${he(created)}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Conversation</div>
                <div class="archive-detail-meta-value">${he(convId)}</div>
              </div>
              <div class="archive-detail-meta-item">
                <div class="archive-detail-meta-label">Tokens</div>
                <div class="archive-detail-meta-value">${tokens}</div>
              </div>
              ${name ? `<div class="archive-detail-meta-item"><div class="archive-detail-meta-label">Name</div><div class="archive-detail-meta-value">${he(name)}</div></div>` : ''}
            </div>
          </div>
        </div>
        ` : ''}
        
        <!-- Metadata Tab -->
        ${hasMetadata ? `
        <div class="archive-tab-panel" data-tab="metadata" style="display: none;">
          <div class="archive-detail-section">
            <h4>Metadata</h4>
            <pre>${he(JSON.stringify(metadata, null, 2))}</pre>
          </div>
        </div>
        ` : ''}
        
        <!-- Request Context Tab -->
        ${hasRequestContext ? `
        <div class="archive-tab-panel" data-tab="request" style="display: none;">
          <div class="archive-detail-section">
            <h4>Request Context</h4>
            <pre>${he(JSON.stringify(requestContext, null, 2))}</pre>
          </div>
        </div>
        ` : ''}
        
        <!-- Recall Use Tab -->
        ${hasRecallUse ? `
        <div class="archive-tab-panel" data-tab="recall" style="display: none;">
          <div class="archive-detail-section">
            <h4>Recall Use (Tool Result)</h4>
            <pre>${he(JSON.stringify(recallUse, null, 2))}</pre>
          </div>
        </div>
        ` : ''}
        
        <!-- Output Tab -->
        ${hasOutput ? `
        <div class="archive-tab-panel" data-tab="output" style="display: none;">
          <div class="archive-detail-section">
            <h4>Output</h4>
            <pre>${he(output)}</pre>
          </div>
        </div>
        ` : ''}
        
        <!-- Reasoning Tab -->
        ${hasReasoning ? `
        <div class="archive-tab-panel" data-tab="reasoning" style="display: none;">
          <div class="archive-detail-section">
            <h4>Reasoning</h4>
            <pre>${he(reasoning)}</pre>
          </div>
        </div>
        ` : ''}
      </div>
    `;
    
    detailEl.innerHTML = html;
    
    // Add tab switching
    detailEl.querySelectorAll('.archive-detail-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const tabName = tab.dataset.tab;
        detailEl.querySelectorAll('.archive-detail-tab').forEach(t => {
          t.classList.toggle('active', t === tab);
        });
        detailEl.querySelectorAll('.archive-tab-panel').forEach(panel => {
          panel.style.display = panel.dataset.tab === tabName ? 'block' : 'none';
        });
      });
    });
  }

  // Update pagination controls
  function updatePagination(viewName) {
    const pg = pagination[viewName];
    const pagEl = elements.pagination[viewName];
    if (!pagEl) return;
    
    const totalPages = Math.ceil(pg.total / pg.limit);
    const currentPage = Math.floor(pg.offset / pg.limit) + 1;
    
    if (pagEl.prev) {
      pagEl.prev.disabled = currentPage <= 1;
    }
    if (pagEl.next) {
      pagEl.next.disabled = currentPage >= totalPages;
    }
    if (pagEl.info) {
      pagEl.info.textContent = `Page ${currentPage} of ${totalPages || 1}`;
    }
  }

  // Update count display
  function updateCount(viewName, total) {
    const pagEl = elements.pagination[viewName];
    if (pagEl && pagEl.count) {
      pagEl.count.textContent = `${total} total`;
    }
  }

  // Go to page
  function goToPage(viewName, direction) {
    const pg = pagination[viewName];
    const newOffset = pg.offset + direction * pg.limit;
    if (newOffset < 0 || newOffset >= pg.total) return;
    
    pg.offset = newOffset;
    loadCurrentView();
    updateURLState();
  }

  // Event handlers
  function bindEvents() {
    // Model selector
    if (elements.modelSelect) {
      elements.modelSelect.addEventListener('change', () => {
        currentModel = elements.modelSelect.value || null;
        currentConversationId = null;
        currentPieceId = null;
        currentFilePath = null;
        // Reset pagination for all views
        Object.keys(pagination).forEach(key => {
          pagination[key].offset = 0;
        });
        showView('conversations');
      });
    }
    
    // Close button
    if (elements.closeBtn) {
      elements.closeBtn.addEventListener('click', closeModal);
    }
    
    // Tabs
    Object.keys(elements.tabs).forEach(key => {
      const tab = elements.tabs[key];
      if (tab) {
        tab.addEventListener('click', () => showView(key));
      }
    });
    
    // Conversations controls
    if (elements.convSearch) {
      elements.convSearch.addEventListener('input', debounce(() => {
        pagination.conversations.query = elements.convSearch.value.trim();
        pagination.conversations.offset = 0;
        loadConversations();
      }, SEARCH_DEBOUNCE_MS));
    }
    
    if (elements.convSort) {
      elements.convSort.addEventListener('change', () => {
        pagination.conversations.sort = elements.convSort.value;
        pagination.conversations.offset = 0;
        loadConversations();
      });
    }
    
    // Journal controls
    if (elements.journalEventType) {
      elements.journalEventType.addEventListener('change', () => {
        pagination.journal.eventType = elements.journalEventType.value;
        pagination.journal.offset = 0;
        loadJournal();
      });
    }
    
    if (elements.journalFrom) {
      elements.journalFrom.addEventListener('change', () => {
        pagination.journal.fromTs = elements.journalFrom.value ? new Date(elements.journalFrom.value).toISOString() : '';
        pagination.journal.offset = 0;
        loadJournal();
      });
    }
    
    if (elements.journalTo) {
      elements.journalTo.addEventListener('change', () => {
        pagination.journal.toTs = elements.journalTo.value ? new Date(elements.journalTo.value).toISOString() : '';
        pagination.journal.offset = 0;
        loadJournal();
      });
    }
    
    // Search controls
    if (elements.searchBtn) {
      elements.searchBtn.addEventListener('click', () => {
        const query = elements.searchQuery.value.trim();
        pagination.search.query = query;
        pagination.search.queries = query ? [query] : [];
        pagination.search.mode = elements.searchMode.value;
        pagination.search.offset = 0;
        loadSearch();
      });
    }
    
    if (elements.searchQuery) {
      elements.searchQuery.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          elements.searchBtn?.click();
        }
      });
    }
    
    if (elements.searchMode) {
      elements.searchMode.addEventListener('change', () => {
        pagination.search.mode = elements.searchMode.value;
        pagination.search.offset = 0;
        if (pagination.search.query) loadSearch();
      });
    }
    
    // File history controls
    if (elements.fileHistoryRefresh) {
      elements.fileHistoryRefresh.addEventListener('click', () => {
        // Refresh the file list
        pagination.fileHistoryFiles.offset = 0;
        loadFileHistoryFiles();
        // Hide detail panel when refreshing
        if (elements.fileHistoryDetail) {
          elements.fileHistoryDetail.style.display = 'none';
        }
        selectedFilePath = null;
      });
    }
    
    // Pagination buttons
    ['conversations', 'journal', 'search', 'fileHistory'].forEach(viewName => {
      const pag = elements.pagination[viewName];
      if (pag) {
        if (pag.prev) {
          pag.prev.addEventListener('click', () => goToPage(viewName, -1));
        }
        if (pag.next) {
          pag.next.addEventListener('click', () => goToPage(viewName, 1));
        }
      }
    });
    
    // Close on Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && elements.modal && elements.modal.style.display === 'flex') {
        closeModal();
      }
    });
  
    // URL state handlers for deep-linking and back/forward navigation
    bindURLStateHandlers();
  }

  // Debounce utility
  function debounce(fn, delay) {
    return function(...args) {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => fn.apply(this, args), delay);
    };
  }

  // ============================================================
  // VirtualList - List virtualization/windowing for long lists
  // ============================================================
  
  class VirtualList {
    /**
     * @param {Object} options
     * @param {HTMLElement} options.container - The scrollable container element
     * @param {Array} options.items - Array of data items to render
     * @param {Function} options.renderItem - Function(item, index) => HTML string
     * @param {number} [options.itemHeight=50] - Estimated height of each item in pixels
     * @param {number} [options.buffer=5] - Number of items to render above/below viewport
     * @param {Function} [options.onItemClick] - Click handler: (item, index)
     * @param {string} [options.selectedId] - ID of currently selected item (for restoration)
     * @param {Function} [options.getItemId] - Function(item) => unique ID string
     */
    constructor(options) {
      this.container = options.container;
      this.items = options.items || [];
      this.renderItem = options.renderItem;
      this.itemHeight = options.itemHeight || 50;
      this.buffer = options.buffer || 5;
      this.onItemClick = options.onItemClick;
      this.getItemId = options.getItemId || (item => item.id || '');
      
      // State
      this.selectedIndex = -1;
      this.selectedId = options.selectedId || null;
      this.scrollTop = 0;
      this.containerHeight = 0;
      this.renderedRange = { start: 0, end: 0 };
      this._scrollHandler = this._onScroll.bind(this);
      this._resizeHandler = this._onResize.bind(this);
      this._itemHeightMeasured = false;
      this._rafPending = false;
      
      // Placeholder elements for maintaining scroll height
      this.topSpacer = null;
      this.bottomSpacer = null;
      this.contentWrapper = null;
      this._resizeObserver = null;
      
      this._init();
    }
    
    _init() {
      if (!this.container) return;
      
      // Store original overflow style
      this.container.style.overflowY = 'auto';
      
      // Create content wrapper and spacers
      this._createStructure();
      
      // Initial measurements
      this._measure();
      
      // Initial render
      this._renderVisible();
      
      // Attach listeners
      this.container.addEventListener('scroll', this._scrollHandler, { passive: true });
      
      // Use ResizeObserver for container size changes (more precise than window resize)
      if (typeof ResizeObserver !== 'undefined') {
        this._resizeObserver = new ResizeObserver(this._resizeHandler);
        this._resizeObserver.observe(this.container);
      } else {
        // Fallback for older browsers
        window.addEventListener('resize', this._resizeHandler, { passive: true });
      }
      
      // Handle selection restoration (scroll to item on initial load)
      if (this.selectedId) {
        this._restoreSelectionById(this.selectedId, true);
      }
    }
    
    _createStructure() {
      // Clear container
      this.container.innerHTML = '';
      
      // For div-based lists: create spacer + content wrapper + spacer structure
      this.topSpacer = document.createElement('div');
      this.topSpacer.style.height = '0px';
      this.topSpacer.style.overflow = 'hidden';
      this.topSpacer.setAttribute('aria-hidden', 'true');
      
      this.contentWrapper = document.createElement('div');
      this.contentWrapper.style.contain = 'content';
      
      this.bottomSpacer = document.createElement('div');
      this.bottomSpacer.style.height = '0px';
      this.bottomSpacer.style.overflow = 'hidden';
      this.bottomSpacer.setAttribute('aria-hidden', 'true');
      
      this.container.appendChild(this.topSpacer);
      this.container.appendChild(this.contentWrapper);
      this.container.appendChild(this.bottomSpacer);
    }
    
    _measure() {
      if (!this.container) return;
      const height = this.container.clientHeight;
      // Guard against zero-height measurement when container is hidden
      if (height === 0) return;
      this.containerHeight = height;
      // Ensure we have a reasonable item height
      if (this.itemHeight <= 0) this.itemHeight = 50;
    }
    
    _onScroll() {
      if (!this.container) return;
      this.scrollTop = this.container.scrollTop;
      
      // Throttle with requestAnimationFrame - only schedule render if not already pending
      if (!this._rafPending) {
        this._rafPending = true;
        requestAnimationFrame(() => {
          this._rafPending = false;
          this._renderVisible();
        });
      }
    }
    
    _onResize() {
      // Guard against zero-height measurement when container is hidden
      if (!this.container) return;
      const height = this.container.clientHeight;
      if (height === 0) return; // Container not visible, skip measurement
      
      this._measure();
      this._renderVisible();
    }
    
    _calculateVisibleRange() {
      const visibleStart = Math.floor(this.scrollTop / this.itemHeight);
      const visibleCount = Math.ceil(this.containerHeight / this.itemHeight);
      const start = Math.max(0, visibleStart - this.buffer);
      const end = Math.min(this.items.length, visibleStart + visibleCount + this.buffer);
      // Clamp start to valid range (handles case where scrollTop is stale after data change)
      const clampedStart = Math.min(start, Math.max(0, this.items.length - (end - start)));
      return { start: clampedStart, end };
    }
    
    _renderVisible() {
      if (!this.contentWrapper || this.items.length === 0) return;
      
      const range = this._calculateVisibleRange();
      
      // Skip if range hasn't changed significantly
      if (range.start === this.renderedRange.start && range.end === this.renderedRange.end) {
        return;
      }
      
      this.renderedRange = range;
      
      // Update spacers
      if (this.topSpacer && this.bottomSpacer) {
        this.topSpacer.style.height = `${range.start * this.itemHeight}px`;
        this.bottomSpacer.style.height = `${(this.items.length - range.end) * this.itemHeight}px`;
      }
      
      // Render items
      const fragment = document.createDocumentFragment();
      
      for (let i = range.start; i < range.end; i++) {
        const item = this.items[i];
        const html = this.renderItem(item, i);
        
        // For div-based items
        const div = document.createElement('div');
        div.innerHTML = html;
        const child = div.firstElementChild;
        if (child) {
          child.dataset.virtualIndex = i;
          const itemId = this.getItemId(item);
          if (itemId) child.dataset.itemId = itemId;
          
          // Apply selection styling
          if (i === this.selectedIndex) {
            child.classList.add('selected');
          }
          
          // Add click handler
          if (this.onItemClick) {
            child.addEventListener('click', () => this._handleClick(item, i));
            child.style.cursor = 'pointer';
          }
          
          fragment.appendChild(child);
        }
      }
      
      // Replace content
      this.contentWrapper.innerHTML = '';
      this.contentWrapper.appendChild(fragment);
      
      // Measure actual item heights after first render to improve scrollbar accuracy
      if (!this._itemHeightMeasured && this.contentWrapper.children.length > 0) {
        this._measureItemHeights();
      }
    }
    
    _measureItemHeights() {
      if (this.contentWrapper.children.length === 0) return;
      
      // Measure a few items to get average height
      const children = Array.from(this.contentWrapper.children);
      const sampleSize = Math.min(5, children.length);
      let totalHeight = 0;
      let measuredCount = 0;
      
      for (let i = 0; i < sampleSize; i++) {
        const child = children[i];
        const rect = child.getBoundingClientRect();
        if (rect.height > 0) {
          totalHeight += rect.height;
          measuredCount++;
        }
      }
      
      if (measuredCount > 0) {
        const avgHeight = totalHeight / measuredCount;
        // Set flag unconditionally after first measurement to avoid re-measuring
        this._itemHeightMeasured = true;
        // Only update if significantly different (avoid thrashing)
        if (Math.abs(avgHeight - this.itemHeight) > 5) {
          this.itemHeight = avgHeight;
          // Update spacer heights with new itemHeight
          if (this.topSpacer && this.bottomSpacer) {
            const range = this._calculateVisibleRange();
            this.topSpacer.style.height = `${range.start * this.itemHeight}px`;
            this.bottomSpacer.style.height = `${(this.items.length - range.end) * this.itemHeight}px`;
          }
          // Re-render to ensure visible items match new heights
          this._renderVisible();
        }
      }
    }
    
    _handleClick(item, index) {
      // Update visual selection without triggering callback (click handler will do that)
      this.setSelectedIndex(index);
      
      // Call click handler (which will handle URL state, breadcrumbs, etc.)
      if (this.onItemClick) {
        this.onItemClick(item, index);
      }
    }
    
    _restoreSelectionById(id, restoreScroll = false) {
      const index = this.items.findIndex(item => this.getItemId(item) === id);
      if (index >= 0) {
        this.setSelectedIndex(index);
        if (restoreScroll) {
          this.scrollToIndex(index);
        }
      }
    }
    
    /**
     * Update the items array and re-render
     * @param {*} selectedId - If undefined, preserves current selection. If null, clears selection. If string, sets new selection.
     * @param {boolean} [restoreScroll=false] - If true, scroll to selected item (for initial/deep-link restore)
     */
    setItems(items, selectedId = undefined, restoreScroll = false) {
      this.items = items || [];
      
      // Preserve current selection if selectedId not explicitly passed (undefined)
      // Explicit null clears selection; explicit string sets new selection
      if (selectedId !== undefined) {
        this.selectedId = selectedId;
      }
      this.selectedIndex = -1;
      this.renderedRange = { start: 0, end: 0 };
      
      // Reset scroll position when data changes (fixes blank list when scrolled)
      if (this.container) {
        this.container.scrollTop = 0;
        this.scrollTop = 0;
      }
      
      // Rebuild structure if it was destroyed by loading placeholder
      if (!this.contentWrapper || !this.contentWrapper.isConnected) {
        this._createStructure();
      }
      
      // Update spacer heights for new total
      if (this.topSpacer && this.bottomSpacer) {
        this.topSpacer.style.height = '0px';
        this.bottomSpacer.style.height = `${this.items.length * this.itemHeight}px`;
      }
      
      this._measure();
      this._renderVisible();
      
      // Restore selection if we have one
      if (this.selectedId) {
        this._restoreSelectionById(this.selectedId, restoreScroll);
      }
    }
    
    /**
     * Set selected index and update visual selection
     */
    setSelectedIndex(index) {
      if (index < 0 || index >= this.items.length) {
        this.selectedIndex = -1;
        this.selectedId = null;
        this._updateSelectionVisual();
        return;
      }
      
      this.selectedIndex = index;
      this.selectedId = this.getItemId(this.items[index]);
      this._updateSelectionVisual();
    }
    
    _updateSelectionVisual() {
      // Update all rendered items' selection state
      const renderedItems = this.contentWrapper.querySelectorAll('[data-virtual-index]');
      renderedItems.forEach(el => {
        const virtualIndex = parseInt(el.dataset.virtualIndex, 10);
        if (virtualIndex === this.selectedIndex) {
          el.classList.add('selected');
        } else {
          el.classList.remove('selected');
        }
      });
    }
    
    /**
     * Scroll to make an item visible
     */
    scrollToIndex(index) {
      if (!this.container || index < 0 || index >= this.items.length) return;
      
      const targetScrollTop = index * this.itemHeight;
      const viewportCenter = targetScrollTop - (this.containerHeight / 2) + (this.itemHeight / 2);
      const clampedScrollTop = Math.max(0, Math.min(viewportCenter, this.items.length * this.itemHeight - this.containerHeight));
      
      this.container.scrollTop = clampedScrollTop;
      this.scrollTop = clampedScrollTop;
      this._renderVisible();
    }
    
    /**
     * Clean up event listeners
     */
    destroy() {
      if (this.container) {
        this.container.removeEventListener('scroll', this._scrollHandler);
      }
      // Clean up ResizeObserver
      if (this._resizeObserver) {
        this._resizeObserver.disconnect();
        this._resizeObserver = null;
      } else {
        window.removeEventListener('resize', this._resizeHandler);
      }
      this.container = null;
      this.contentWrapper = null;
      this.topSpacer = null;
      this.bottomSpacer = null;
      this.items = [];
    }
  }

  // Open modal
  function openModal(modelName = null, conversationId = null) {
    if (!elements.modal) return;
    
    cacheElements();
    
    // Check for existing URL state before loading model list
    const urlState = parseURLState();
    const hasURLState = urlState && urlState.model;
    
    elements.modal.style.display = 'flex';
    elements.modal.setAttribute('aria-hidden', 'false');
    
    // Focus first focusable element
    setTimeout(() => {
      const focusable = elements.modal.querySelector(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable) focusable.focus();
    }, 0);
    
    // Load model list
    loadModelList().then(() => {
      if (hasURLState) {
        // Restore state from URL
        restoreURLState(urlState);
      } else if (modelName) {
        elements.modelSelect.value = modelName;
        currentModel = modelName;
        loadCurrentView();
        
        // If conversationId provided, navigate to that conversation
        if (conversationId) {
          navigateToConversation(conversationId);
        }
      }
      // If no modelName and no URL state, wait for user to select a model
      
      // Update URL with initial state (replace to avoid extra history entry)
      updateURLState(true);
    });
    
    // Register cleanup with ModalManager
    if (window.ModalManager) {
      window.ModalManager.register('archive-browser-modal', cleanup);
    }
  }

  // Navigate to a specific conversation
  async function navigateToConversation(conversationId) {
    if (!currentModel || !conversationId) return;
    
    // First load conversations to find the conversation
    const pg = pagination.conversations;
    const listEl = elements.lists.conversations;
    
    if (!listEl) return;
    
    try {
      // Load the conversation detail directly
      currentConversationId = conversationId;
      updateBreadcrumbs();
      updateURLState();
      
      // Switch to conversations view if not already there
      if (currentView !== 'conversations') {
        showView('conversations');
      }
      
      // Load conversation detail
      await loadConversationDetail(conversationId);
      
      // Highlight the conversation in the list
      if (virtualLists.conversations) {
        const index = currentData.conversations.findIndex(c => c.id === conversationId);
        if (index >= 0) {
          virtualLists.conversations.setSelectedIndex(index, false);
          virtualLists.conversations.scrollToIndex(index);
        }
      }
    } catch (e) {
      console.error('Failed to navigate to conversation:', e);
    }
  }

  // Close modal
  function closeModal() {
    if (!elements.modal) return;
    
    elements.modal.style.display = 'none';
    elements.modal.setAttribute('aria-hidden', 'true');
    
    // Clear archive URL state
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete(ARCHIVE_URL_PARAM);
      history.replaceState(null, '', url);
    } catch (e) {
      console.warn('Failed to clear archive URL state:', e);
    }
    
    cleanup();
    
    if (window.ModalManager) {
      window.ModalManager.close('archive-browser-modal');
    }
  }

  // Cleanup
  function cleanup() {
    // Clear search debounce
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = null;
    }
    
    // Destroy VirtualList instances
    Object.keys(virtualLists).forEach(key => {
      if (virtualLists[key]) {
        virtualLists[key].destroy();
        virtualLists[key] = null;
      }
    });
    
    // Clear current data
    Object.keys(currentData).forEach(key => {
      currentData[key] = [];
    });
    
    // Reset state
    currentConversationId = null;
    currentPieceId = null;
    currentFilePath = null;
    Object.keys(pagination).forEach(key => {
      pagination[key].offset = 0;
    });
  }

  // Public API
  window.ArchiveBrowser = {
    open: openModal,
    close: closeModal,
    loadModelList: loadModelList,
    navigateToConversation: navigateToConversation
  };

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      cacheElements();
      bindEvents();
    });
  } else {
    cacheElements();
    bindEvents();
  }

})();