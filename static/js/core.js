/**
 * Core utility functions
 */

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
    .replace(/"/g, '&quot;')
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
 * Log message to the log panel
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
  
  // Limit to 200 entries to prevent excessive memory usage
  const entries = contentEl.getElementsByTagName('span');
  if (entries.length > 200) {
    // Remove excess entries from the end
    while (entries.length > 200) {
      contentEl.removeChild(entries[entries.length - 1]);
    }
  }
}
