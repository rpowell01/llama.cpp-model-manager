/**
 * Application state management
 */

// Global constants - will be set from template
const HOST = "{{ host }}";
const FLAVOR = "{{ flask_port }}";
const CACHE_VERSION = "{{ cache_version }}";

// Application state
let selectedModel = null;
let currentModelRows = []; // Cache of model data from latest refresh
let autoSearchAttempted = new Set(); // Track auto-search attempts per model (web-iet)
let startingIntervals = {}; // Track auto-refresh intervals for models in 'starting' state (web-rev)

// Download progress state - preserved across refreshes
const downloadProgressState = {};

// Chat message history per model
const chatHistory = {};

// Load saved chat histories from localStorage
for (let i = 0; i < localStorage.length; i++) {
  const key = localStorage.key(i);
  if (key && key.startsWith('chat_history_')) {
    const modelName = key.slice('chat_history_'.length);
    try {
      const saved = JSON.parse(localStorage.getItem(key));
      if (Array.isArray(saved)) {
        chatHistory[modelName] = saved;
      }
    } catch (e) {
      console.error('Failed to parse saved chat history for model', modelName, e);
    }
  }
}

let isRefreshing = false;
