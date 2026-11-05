/**
 * Core utility functions
 */

let _eventSource = null;

/**
 * Initialize SSE log streaming
 */
function initLogStream() {
  if (_eventSource) return;
  
  const el = document.getElementById('log');
  if (!el) return;
  const contentEl = el.querySelector('.log-content');
  if (!contentEl) return;

  _eventSource = new EventSource('/api/logs/stream');
  
  _eventSource.onmessage = function(event) {
    try {
      const entry = JSON.parse(event.data);
      if (entry.timestamp && entry.message) {
        const escapedMsg = he(entry.message);
        const color = entry.color || 'inherit';
        const logEntry = `<span style="color:${color}">[${entry.timestamp}] ${escapedMsg}</span>\n`;
        contentEl.insertAdjacentHTML('afterbegin', logEntry);
        
        // Limit to 1000 entries
        const entries = contentEl.getElementsByTagName('span');
        if (entries.length > 1000) {
          while (entries.length > 1000) {
            contentEl.removeChild(entries[entries.length - 1]);
          }
        }
      }
    } catch (e) {
      // Ignore parse errors
    }
  };
  
  _eventSource.onerror = function() {
    console.warn('Log stream disconnected, reconnecting...');
    _eventSource.close();
    _eventSource = null;
    // Reconnect after 5 seconds
    setTimeout(initLogStream, 5000);
  };
}

/**
 * Format value as GB with 2 decimal places
 * @param {number|null} v - Value in GB
 * @returns {string} Formatted string or warning chip
 */
function fmtGb(v) {
  if (v == null) return '<span class="chip warn">missing</span>';
  return v.toFixed(2) + ' GB';
}

/**
 * Format bytes as GB
 * @param {number} bytes - Bytes to format
 * @returns {string} Formatted GB string or 'Unknown size'
 */
function fmtSize(bytes) {
  if (!bytes || bytes === 0) return 'Unknown size';
  const gb = bytes / (1024 ** 3);
  return gb.toFixed(2) + ' GB';
}


/**
 * Escape string for HTML output (prevent XSS)
 * @param {string} str - String to escape
 * @returns {string} HTML-escaped string
 */
function he(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '"&quot;")
    .replace(/'/g, '&#039;');
}

/**
 * Generate a safe HTML element ID from any string (alphanumeric + underscore)
 * @param {string} str - Input string
 * @returns {string} Safe ID
 */
function safeId(str) {
  return String(str).replace(/[^a-zA-Z0-9]/g, '_');
}

/**
 * Escape string for JavaScript usage (single quotes and backslashes)
 * @param {string} str - String to escape
 * @returns {string} Escaped string
 */
function jsEscape(str) {
  return String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

/**
 * Generate URL for port (using HOST)
 * @param {number} p - Port number
 * @returns {string} Full URL
 */
function portUrl(p) {
  return 'http://' + HOST + ':' + p;
}

/**
 * Log message to the log panel (for frontend-generated logs)
 * Escapes HTML to prevent XSS. Use logHtml() for trusted HTML markup.
 * @param {string} msg - Message to log
 * @param {string} color - Optional color
 */
function log(msg, color) {
  const el = document.getElementById('log');
  if (!el) return;
  const contentEl = el.querySelector('.log-content');
  if (!contentEl) return;
  
  const ts = new Date().toISOString().slice(11, 19);
  const escapedMsg = he(msg);
  const entry = `<span style="color:${color || 'inherit'}">[${ts}] ${escapedMsg}</span>\n`;
  contentEl.insertAdjacentHTML('afterbegin', entry);
  
  // Limit to 1000 entries to prevent excessive memory usage
  const entries = contentEl.getElementsByTagName('span');
  if (entries.length > 1000) {
    // Remove excess entries from the end
    while (entries.length > 1000) {
      contentEl.removeChild(entries[entries.length - 1]);
    }
  }
}

/**
 * Log trusted HTML markup to the log panel (for frontend-generated logs)
 * Does NOT escape HTML - only use for trusted content with intentional markup.
 * @param {string} html - HTML markup to log
 * @param {string} color - Optional color
 */
function logHtml(html, color) {
  const el = document.getElementById('log');
  if (!el) return;
  const contentEl = el.querySelector('.log-content');
  if (!contentEl) return;
  
  const ts = new Date().toISOString().slice(11, 19);
  const entry = `<span style="color:${color || 'inherit'}">[${ts}] ${html}</span>\n`;
  contentEl.insertAdjacentHTML('afterbegin', entry);
  
  // Limit to 1000 entries to prevent excessive memory usage
  const entries = contentEl.getElementsByTagName('span');
  if (entries.length > 1000) {
    while (entries.length > 1000) {
      contentEl.removeChild(entries[entries.length - 1]);
    }
  }
}

// Auto-initialize log stream when DOM is ready
document.addEventListener('DOMContentLoaded', initLogStream);