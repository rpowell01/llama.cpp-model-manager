/**
 * Kilo Code integration - provider configuration
 */

/**
 * Generate Kilo Code provider configuration
 * @param {Object} modelData - Model data
 * @param {string} modelName - Model name
 * @returns {Object} Configuration object (provider config without outer braces)
 */
function generateKiloCodeConfig(modelData, modelName) {
  // Provider key: llama_cpp_<MODEL_NAME_UPPER>
  const key = 'llama_cpp_' + modelName.toUpperCase().replace(/[^A-Z0-9]/g, '_');
  const providerName = `Llama.cpp ${modelName}`;
  const npm = '@ai-sdk/openai-compatible';
  const baseURL = `http://${HOST}:${modelData.port || 8080}/v1`;

  // Use actual context length from model data
  const context = modelData.ctx_size || 16384;
  const inputLimit = Math.round(context * 0.85);
  const outputLimit = Math.round(context * 0.15);

  // Build headers object from custom header fields
  const headers = {};
  const headerKey = modelData.custom_header_key || '';
  const headerValue = modelData.custom_header_value || '';
  if (headerKey && headerValue) {
    headers[headerKey] = headerValue;
  }

  // Supported parameters for the model
  const supportedParameters = [
    "include_reasoning",
    "max_tokens",
    "reasoning",
    "temperature",
    "tool_choice",
    "tools"
  ];

  // Default parameters (use null for unset values)
  const defaultParameters = {
    temperature: modelData.temperature !== undefined ? modelData.temperature : null,
    top_p: modelData.top_p !== undefined ? modelData.top_p : null,
    top_k: modelData.top_k !== undefined ? modelData.top_k : null,
    frequency_penalty: null, // Not currently supported in llama.cpp
    presence_penalty: modelData.presence_penalty !== undefined ? modelData.presence_penalty : null,
    repetition_penalty: modelData.repeat_penalty !== undefined ? modelData.repeat_penalty : null,
    reasoning: modelData.reasoning !== undefined ? modelData.reasoning : null,
    reasoning_effort: modelData.reasoning_effort !== undefined ? modelData.reasoning_effort : null,
    reasoning_budget: modelData.reasoning_budget !== undefined ? modelData.reasoning_budget : null,
    min_p: modelData.min_p !== undefined ? modelData.min_p : null,
  };

  // Return provider config object (without outer key wrapper)
  return {
    name: providerName,
    npm: npm,
    options: {
      baseURL: baseURL,
      apiKey: '${LLAMA_CPP_API_KEY}',
      rateLimitSeconds: 0.1,
      maxRetries: 3,
      ...(Object.keys(headers).length > 0 ? { headers } : {})
    },
    models: {
      [modelName]: {
        limit: {
          context: context,
          input: inputLimit,
          output: outputLimit
        },
        supported_parameters: supportedParameters,
        default_parameters: defaultParameters
      }
    }
  };
}

/**
 * Open Kilo Code config modal
 * @param {Object} modelData - Model data
 * @param {string} modelName - Model name
 */
function openKiloCodeModal(modelData, modelName) {
  const modal = document.getElementById('kilo-config-modal');
  const contentEl = document.getElementById('kilo-config-content');
  const statusEl = document.getElementById('kilo-config-status');
  if (!modal || !contentEl || !statusEl) {
    console.error('Kilo Code modal elements not found');
    return;
  }
  try {
    const config = generateKiloCodeConfig(modelData, modelName);
    const jsonStr = JSON.stringify(config, null, 2);
    contentEl.textContent = jsonStr;
    statusEl.textContent = '';
    ModalManager.open('kilo-config-modal');
  } catch (err) {
    console.error('Error generating config:', err);
    statusEl.textContent = 'Error generating configuration.';
    statusEl.style.color = 'var(--red)';
  }
}


  const kiloModal = document.getElementById('kilo-config-modal');
  if (kiloModal) {
    kiloModal.addEventListener('click', (e) => {
      if (e.target.id === 'kilo-config-modal') {
        kiloModal.style.display = 'none';
      }
    });
  }

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
