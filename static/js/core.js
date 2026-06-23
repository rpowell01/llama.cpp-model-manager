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
 * Format bytes per second as MB/s
 * @param {number} bytesPerSecond - Speed in bytes/second
 * @returns {string} Formatted speed string or empty
 */
function fmtSpeed(bytesPerSecond) {
  if (!bytesPerSecond) return '';
  const mbps = bytesPerSecond / (1024 * 1024);
  return mbps.toFixed(1) + ' MB/s';
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
  const ts = new Date().toISOString().slice(11, 19);
  el.innerHTML = `<span style="color:${color || 'inherit'}">[${ts}] ${msg}</span>\n` + el.innerHTML;
  if (el.children.length > 200) el.innerHTML = el.innerHTML.slice(0, 8000);
}
