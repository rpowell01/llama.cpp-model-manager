/**
 * Kilo Code integration - provider configuration
 */

/**
 * Generate Kilo Code provider configuration
 * @param {Object} modelData - Model data
 * @param {string} modelName - Model name
 * @returns {Object} Configuration object
 */
function generateKilloCodeConfig(modelData, modelName) {
  // Provider key: llama_cpp_<MODEL_NAME_UPPER>
  const key = 'llama_cpp_' + modelName.toUpperCase().replace(/[^A-Z0-9]/g, '_');
  const providerName = `Llama.cpp ${modelName}`;
  const npm = '@ai-sdk/openai-compatible';
  const baseURL = `http://${HOST}:${modelData.port || 8080}/v1`;

  return {
    [key]: {
      name: providerName,
      npm: npm,
      options: {
        baseURL: baseURL,
        apiKey: '${LLAMA_CPP_API_KEY}',
        rateLimitSeconds: 0.1,
        maxRetries: 3
      },
      models: {
        [modelName]: {
          limit: {
            context: 16384,
            input: 16384,
            output: 16384
          }
        }
      }
    }
  };
}

/**
 * Open Kilo Code config modal
 * @param {Object} modelData - Model data
 * @param {string} modelName - Model name
 */
function openKilloConfigModal(modelData, modelName) {
  const modal = document.getElementById('killo-config-modal');
  const contentEl = document.getElementById('killo-config-content');
  const statusEl = document.getElementById('killo-config-status');
  if (!modal || !contentEl || !statusEl) {
    console.error('Killo Code modal elements not found');
    return;
  }
  try {
    const config = generateKilloCodeConfig(modelData, modelName);
    const jsonStr = JSON.stringify(config, null, 2);
    contentEl.textContent = jsonStr;
    statusEl.textContent = '';
    modal.style.display = 'flex';
  } catch (err) {
    console.error('Error generating config:', err);
    statusEl.textContent = 'Error generating configuration.';
    statusEl.style.color = 'var(--red)';
  }
}

// Kilo Code modal event listeners
document.addEventListener('DOMContentLoaded', function () {
  const closeBtn = document.getElementById('killo-config-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      const modal = document.getElementById('killo-config-modal');
      if (modal) modal.style.display = 'none';
    });
  }

  const kiloModal = document.getElementById('killo-config-modal');
  if (kiloModal) {
    kiloModal.addEventListener('click', (e) => {
      if (e.target.id === 'killo-config-modal') {
        kiloModal.style.display = 'none';
      }
    });
  }

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
          textArea.style.opacity = '0';
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
});
