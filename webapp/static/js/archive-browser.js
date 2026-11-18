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
    fileHistory: { offset: 0, limit: 50, total: 0, path: '' }
  };
  
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
    elements.journalFrom = document.getElementById('archive-journal-from');
    elements.journalTo = document.getElementById('archive-journal-to');
    elements.searchQuery = document.getElementById('archive-search-query');
    elements.searchMode = document.getElementById('archive-search-mode');
    elements.searchBtn = document.getElementById('archive-search-btn');
    elements.filePath = document.getElementById('archive-file-path');
    elements.fileHistoryBtn = document.getElementById('archive-file-history-btn');
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
        pagination.search.mode = state.searchMode;
        if (elements.searchMode) elements.searchMode.value = state.searchMode;
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
        loadFileHistory();
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
      
      if (!data.success) throw new Error(data.error || 'Failed to load conversations');
      
      const conversations = data.data.conversations || [];
      pg.total = data.data.total || 0;
      
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

  // Render conversations list
  function renderConversations(conversations) {
    const listEl = elements.lists.conversations;
    if (!listEl) return;
    
    if (conversations.length === 0) {
      listEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">💬</div><div>No conversations found</div></div>';
      return;
    }
    
    let html = '<table style="width: 100%; border-collapse: collapse; font-size: 0.75rem;">';
    html += '<thead><tr style="text-align: left; color: var(--text2);">';
    html += '<th style="padding: 0.25rem; width: 40%;">ID / Title</th>';
    html += '<th style="padding: 0.25rem; width: 20%;">Time Range</th>';
    html += '<th style="padding: 0.25rem; width: 15%;">Msgs/Tokens/Segs</th>';
    html += '<th style="padding: 0.25rem; width: 25%;">Preview</th>';
    html += '</tr></thead><tbody>';
    
    conversations.forEach(conv => {
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
      
      html += `<tr class="archive-conversation-row" data-conv-id="${he(id)}">`;
      html += `<td><div class="conv-id">${he(id.substring(0, 20))}…</div><div class="conv-title" title="${he(title)}">${he(title)}</div></td>`;
      html += `<td class="conv-time">${he(formatTimeRange(created, updated))}</td>`;
      html += `<td class="conv-stats">${msgCount} / ${tokenCount.toLocaleString()} / ${segCount}</td>`;
      html += `<td class="conv-preview" title="${he(preview)}">${he(preview)}</td>`;
      html += '</tr>';
    });
    
    html += '</tbody></table>';
    listEl.innerHTML = html;
    
    // Add click handlers
    listEl.querySelectorAll('.archive-conversation-row').forEach(row => {
      row.addEventListener('click', () => {
        const convId = row.dataset.convId;
        // Single click: select and open session detail (per requirement)
        selectConversation(convId);
        loadConversationDetail(convId);
      });
      
      // Double-click also opens detail (kept for consistency)
      row.addEventListener('dblclick', (e) => {
        e.preventDefault();
        const convId = row.dataset.convId;
        loadConversationDetail(convId);
      });
    });
  }

  // Select conversation (highlight row)
  function selectConversation(convId) {
    // Update selection visual
    document.querySelectorAll('.archive-conversation-row').forEach(row => {
      row.classList.toggle('selected', row.dataset.convId === convId);
    });
    
    currentConversationId = convId;
    updateBreadcrumbs();
    updateURLState();
    
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
      
      if (!data.success) throw new Error(data.error || 'Failed to load journal');
      
      const events = data.data.events || [];
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
      
      if (!data.success) throw new Error(data.error || 'Failed to load journal');
      
      const events = data.data.events || [];
      pg.total = data.data.total || 0;
      
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

  // Render journal list
  function renderJournal(events) {
    const listEl = elements.lists.journal;
    if (!listEl) return;
    
    if (events.length === 0) {
      listEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">📋</div><div>No journal events found</div></div>';
      return;
    }
    
    let html = '';
    events.forEach(event => {
      const id = event.id || '';
      const eventType = event.event_type || 'unknown';
      const timestamp = formatTimestamp(event.timestamp);
      const convId = event.conversation_id || '';
      const data = event.data ? JSON.stringify(event.data).substring(0, 200) : '{}';
      
      html += `<div class="archive-journal-item" data-event-id="${he(id)}" data-conv-id="${he(convId)}">`;
      html += `<div class="journal-header">`;
      html += `<span class="journal-event-type ${he(eventType)}">${he(eventType)}</span>`;
      html += `<span class="journal-timestamp">${he(timestamp)}</span>`;
      html += `</div>`;
      if (convId) {
        html += `<div class="journal-conversation">Conversation: ${he(convId.substring(0, 16))}…</div>`;
      }
      html += `<div class="journal-data">${he(data)}${event.data && JSON.stringify(event.data).length > 200 ? '…' : ''}</div>`;
      html += `</div>`;
    });
    
    listEl.innerHTML = html;
    
    // Add click handlers
    listEl.querySelectorAll('.archive-journal-item').forEach(item => {
      item.addEventListener('click', () => {
        const eventId = item.dataset.eventId;
        const convId = item.dataset.convId;
        selectJournalEvent(eventId, convId);
      });
    });
  }

  // Select journal event
  function selectJournalEvent(eventId, convId) {
    document.querySelectorAll('.archive-journal-item').forEach(item => {
      item.classList.toggle('selected', item.dataset.eventId === eventId);
    });
    
    if (convId) {
      currentConversationId = convId;
      updateBreadcrumbs();
    }
    
    // Load event detail via recall
    loadPieceDetail(eventId);
    updateURLState();
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
      
      if (!data.success) throw new Error(data.error || 'Search failed');
      
      const pieces = data.data.pieces || [];
      pg.total = data.data.total || 0;
      
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

  // Render search results
  function renderSearchResults(pieces) {
    const listEl = elements.lists.search;
    if (!listEl) return;
    
    if (pieces.length === 0) {
      listEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">🔍</div><div>No results found</div></div>';
      return;
    }
    
    let html = '';
    pieces.forEach(piece => {
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
      
      html += `<div class="archive-search-result" data-piece-id="${he(id)}" data-conv-id="${he(convId)}">`;
      html += `<div class="search-result-header">`;
      html += `<span class="search-result-role ${he(role)}">${he(role)}</span>`;
      html += `<span style="font-size: 0.7rem; color: var(--text2); font-family: monospace;">seg:${segment} msg:${msgIndex}</span>`;
      html += `</div>`;
      html += `<div class="search-result-meta">`;
      html += `${he(created)} | Conv: ${he(convId.substring(0, 12))}…`;
      html += `</div>`;
      html += `<div class="search-result-content">${displayContent}${content.length > 150 ? '…' : ''}</div>`;
      html += `</div>`;
    });
    
    listEl.innerHTML = html;
    
    // Add click handlers
    listEl.querySelectorAll('.archive-search-result').forEach(item => {
      item.addEventListener('click', () => {
        const pieceId = item.dataset.pieceId;
        const convId = item.dataset.convId;
        selectSearchResult(pieceId, convId);
      });
    });
  }

  // Select search result
  function selectSearchResult(pieceId, convId) {
    document.querySelectorAll('.archive-search-result').forEach(item => {
      item.classList.toggle('selected', item.dataset.pieceId === pieceId);
    });
    
    if (convId) {
      currentConversationId = convId;
    }
    currentPieceId = pieceId;
    updateBreadcrumbs();
    
    loadPieceDetail(pieceId);
    updateURLState();
  }

  // Load file history
  async function loadFileHistory() {
    if (!currentModel || !currentFilePath) return;
    
    const pg = pagination.fileHistory;
    const listEl = elements.lists.fileHistory;
    const paginationEl = elements.pagination.fileHistory;
    
    if (!listEl) return;
    
    listEl.innerHTML = '<div class="archive-loading">Loading file history…</div>';
    setStatus('Loading file history…');
    
    try {
      const params = new URLSearchParams({
        model: currentModel,
        path: currentFilePath,
        limit: pg.limit,
        offset: pg.offset
      });
      
      const resp = await fetch(`/api/proxy/archive/recall?${params}`);
      const data = await resp.json();
      
      if (!data.success) throw new Error(data.error || 'Failed to load file history');
      
      const pieces = data.data.pieces || [];
      pg.total = data.data.total || 0;
      
      renderFileHistory(pieces);
      updatePagination('fileHistory');
      updateCount('fileHistory', pg.total);
      setStatus(`Loaded ${pieces.length} of ${pg.total} file history entries`);
    } catch (e) {
      console.error('Failed to load file history:', e);
      listEl.innerHTML = `<div class="archive-error">Error: ${he(e.message)}</div>`;
      setStatus('Error loading file history');
    }
  }

  // Render file history
  function renderFileHistory(pieces) {
    const listEl = elements.lists.fileHistory;
    if (!listEl) return;
    
    if (pieces.length === 0) {
      listEl.innerHTML = '<div class="archive-empty"><div class="archive-empty-icon">📁</div><div>No history found for this file</div></div>';
      return;
    }
    
    let html = '';
    pieces.forEach(piece => {
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
      
      html += `<div class="archive-file-history-item" data-piece-id="${he(id)}" data-conv-id="${he(convId)}">`;
      html += `<div class="file-history-header">`;
      html += `<span class="file-history-operation">${he(operation)}</span>`;
      html += `<span class="file-history-timestamp">${he(timestamp)}</span>`;
      html += `</div>`;
      html += `<div class="file-history-conversation">Segment: ${segment}, Message: ${msgIndex} | Conv: ${he(convId.substring(0, 16))}…</div>`;
      html += `</div>`;
    });
    
    listEl.innerHTML = html;
    
    // Add click handlers
    listEl.querySelectorAll('.archive-file-history-item').forEach(item => {
      item.addEventListener('click', () => {
        const pieceId = item.dataset.pieceId;
        const convId = item.dataset.convId;
        selectFileHistoryItem(pieceId, convId);
      });
    });
  }

  // Select file history item
  function selectFileHistoryItem(pieceId, convId) {
    document.querySelectorAll('.archive-file-history-item').forEach(item => {
      item.classList.toggle('selected', item.dataset.pieceId === pieceId);
    });
    
    if (convId) {
      currentConversationId = convId;
    }
    currentPieceId = pieceId;
    updateBreadcrumbs();
    
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
      const resp = await fetch(`/api/proxy/archive/piece/${encodeURIComponent(pieceId)}?model=${encodeURIComponent(currentModel)}`);
      const data = await resp.json();
      
      if (!data.success) throw new Error(data.error || 'Failed to load piece');
      
      const piece = data.data;
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
    const metadata = piece.metadata || {};
    const requestContext = piece.request_context || {};
    const recallUse = piece.recall_use || {};
    const output = piece.output || '';
    const reasoning = piece.reasoning || '';
    
    let html = `
      <div class="archive-detail-header">
        <div>
          <div class="archive-detail-title">${he(role.charAt(0).toUpperCase() + role.slice(1))} Piece</div>
          <div class="archive-detail-id">${he(id)}</div>
        </div>
      </div>
      
      <div class="archive-detail-tabs">
        <button class="archive-detail-tab active" data-tab="content">Content</button>
        <button class="archive-detail-tab" data-tab="metadata">Metadata</button>
        <button class="archive-detail-tab" data-tab="request">Request Context</button>
        <button class="archive-detail-tab" data-tab="recall">Recall Use</button>
        <button class="archive-detail-tab" data-tab="output">Output</button>
        <button class="archive-detail-tab" data-tab="reasoning">Reasoning</button>
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
            </div>
          </div>
        </div>
        
        <!-- Metadata Tab -->
        <div class="archive-tab-panel" data-tab="metadata" style="display: none;">
          <div class="archive-detail-section">
            <h4>Metadata</h4>
            <pre>${he(JSON.stringify(metadata, null, 2))}</pre>
          </div>
        </div>
        
        <!-- Request Context Tab -->
        <div class="archive-tab-panel" data-tab="request" style="display: none;">
          <div class="archive-detail-section">
            <h4>Request Context</h4>
            <pre>${he(JSON.stringify(requestContext, null, 2))}</pre>
          </div>
        </div>
        
        <!-- Recall Use Tab -->
        <div class="archive-tab-panel" data-tab="recall" style="display: none;">
          <div class="archive-detail-section">
            <h4>Recall Use (Tool Result)</h4>
            <pre>${he(JSON.stringify(recallUse, null, 2))}</pre>
          </div>
        </div>
        
        <!-- Output Tab -->
        <div class="archive-tab-panel" data-tab="output" style="display: none;">
          <div class="archive-detail-section">
            <h4>Output</h4>
            <pre>${he(output)}</pre>
          </div>
        </div>
        
        <!-- Reasoning Tab -->
        <div class="archive-tab-panel" data-tab="reasoning" style="display: none;">
          <div class="archive-detail-section">
            <h4>Reasoning</h4>
            <pre>${he(reasoning)}</pre>
          </div>
        </div>
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
    if (elements.fileHistoryBtn) {
      elements.fileHistoryBtn.addEventListener('click', () => {
        const path = elements.filePath.value.trim();
        if (path) {
          currentFilePath = path;
          pagination.fileHistory.path = path;
          pagination.fileHistory.offset = 0;
          loadFileHistory();
          updateBreadcrumbs();
        }
      });
    }
    
    if (elements.filePath) {
      elements.filePath.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          elements.fileHistoryBtn?.click();
        }
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

  // Open modal
  function openModal(modelName = null) {
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
    loadModelList: loadModelList
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