/**
 * Chat module - modal chat interface with streaming not used (fetch-based)
 */

// Render chat messages for a specific model
function renderChatMessages(modelName) {
  const modelSafeId = safeId(modelName);
  const messagesDiv = document.getElementById(`chat-messages-${modelSafeId}`);
  if (!messagesDiv) return;

  messagesDiv.innerHTML = "";
  const history = chatHistory[modelName] || [];

  history.forEach((msg) => {
    const msgEl = document.createElement("div");
    msgEl.className = `chat-message ${msg.role}`;
    msgEl.textContent = msg.content;
    messagesDiv.appendChild(msgEl);
  });

  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Send a chat message to the model
async function sendChatMessage(modelName) {
  const modelSafeId = safeId(modelName);
  const input = document.getElementById(`chat-input-${modelSafeId}`);
  const sendBtn = document.getElementById(`chat-send-${modelSafeId}`);
  const messagesDiv = document.getElementById(`chat-messages-${modelSafeId}`);
  const content = input.value.trim();
  if (!content) return;

  // Get parameters
  const temperature = parseFloat(
    document.getElementById(`chat-temp-${modelSafeId}`).value,
  );
  const top_p = parseFloat(
    document.getElementById(`chat-top-p-${modelSafeId}`).value,
  );
  const max_tokens = parseInt(
    document.getElementById(`chat-max-tokens-${modelSafeId}`).value,
    10,
  );
  const timeout =
    parseInt(
      document.getElementById(`chat-timeout-${modelSafeId}`).value,
      10,
    ) || 300;

  // Add user message to history and UI
  if (!chatHistory[modelName]) chatHistory[modelName] = [];
  chatHistory[modelName].push({ role: "user", content });
  // Cap chat history to 100 messages to prevent memory growth
  const MAX_HISTORY = 100;
  if (chatHistory[modelName].length > MAX_HISTORY) {
    chatHistory[modelName] = chatHistory[modelName].slice(-MAX_HISTORY);
  }
  localStorage.setItem(
    "chat_history_" + modelName,
    JSON.stringify(chatHistory[modelName]),
  );
  input.value = "";
  localStorage.removeItem("chat_input_" + modelName);
  renderChatMessages(modelName);

  // Disable input and show loading
  sendBtn.disabled = true;
  input.disabled = true;
  const loadingMsg = document.createElement("div");
  loadingMsg.className = "chat-message assistant";
  loadingMsg.textContent = "Thinking...";
  loadingMsg.id = "chat-loading";
  messagesDiv.appendChild(loadingMsg);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;

  const startTime = Date.now();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: modelName,
        messages: chatHistory[modelName].map((m) => ({
          role: m.role,
          content: m.content,
        })),
        temperature,
        top_p,
        max_tokens,
        timeout,
      }),
    });

    // Remove loading indicator
    const loading = document.getElementById("chat-loading");
    if (loading) loading.remove();

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }

    const data = await resp.json();
    const responseTime = Date.now() - startTime;

    // Extract assistant message from OpenAI response format
    const assistantMessage = data.choices?.[0]?.message?.content || "";
    if (!assistantMessage) {
      throw new Error("Empty response from model");
    }

    // Add assistant response to history
    chatHistory[modelName].push({
      role: "assistant",
      content: assistantMessage,
    });
    // Cap chat history to 100 messages to prevent memory growth
    const MAX_HISTORY = 100;
    if (chatHistory[modelName].length > MAX_HISTORY) {
      chatHistory[modelName] = chatHistory[modelName].slice(-MAX_HISTORY);
    }
    localStorage.setItem(
      "chat_history_" + modelName,
      JSON.stringify(chatHistory[modelName]),
    );
    renderChatMessages(modelName);

    // Sync performance metrics in modal (use per-request stats)
    const usage = data.usage || {};
    const tokens = usage.completion_tokens || usage.total_tokens || 0;
    const tps = (tokens / (responseTime / 1000)).toFixed(1);
    const ppEl = document.getElementById(`chat-pp-${modelSafeId}`);
    const tgEl = document.getElementById(`chat-tg-${modelSafeId}`);
    const pgEl = document.getElementById(`chat-pg-${modelSafeId}`);
    if (ppEl) ppEl.textContent = responseTime + " ms";
    if (tgEl) tgEl.textContent = tps + " t/s";
    if (pgEl) pgEl.textContent = tokens + " tokens";
  } catch (err) {
    const loading = document.getElementById("chat-loading");
    if (loading) loading.remove();

    const errorMsg = document.createElement("div");
    errorMsg.className = "chat-message error";
    errorMsg.textContent = `Error: ${err.message}`;
    messagesDiv.appendChild(errorMsg);
  } finally {
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

/**
 * ModalManager for accessible modal handling (focus trap, escape close)
 */
const ModalManager = {
  activeModal: null,
  lastFocusedElement: null,
  focusableSelector:
    'a, button, textarea, input, select, [tabindex]:not([tabindex="-1"])',
  registry: {},

  open(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    this.lastFocusedElement = document.activeElement;

    modal.style.display = "flex";
    modal.setAttribute("aria-hidden", "false");

    // Trap focus - find all focusable elements
    const focusable = Array.from(
      modal.querySelectorAll(this.focusableSelector),
    );
    if (focusable.length > 0) {
      setTimeout(() => focusable[0].focus(), 0);
    }

    this._attachHandlers(modal);
    this.activeModal = modal;
  },

  close(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");

    this._detachHandlers(modal);

    if (this.registry[modalId]) {
      this.registry[modalId]();
      delete this.registry[modalId];
    }

    if (this.lastFocusedElement) {
      this.lastFocusedElement.focus();
    }

    this.activeModal = null;
  },

  register(modalId, onClose) {
    this.registry[modalId] = onClose;
  },

  _attachHandlers(modal) {
    const handleKeydown = (e) => this._onKeyDown(e, modal);
    const handleClick = (e) => this._onClick(e, modal);

    modal._modalKeydownHandler = handleKeydown;
    modal._modalClickHandler = handleClick;

    modal.addEventListener("keydown", handleKeydown);
    modal.addEventListener("click", handleClick);
  },

  _detachHandlers(modal) {
    if (modal._modalKeydownHandler) {
      modal.removeEventListener("keydown", modal._modalKeydownHandler);
      delete modal._modalKeydownHandler;
    }
    if (modal._modalClickHandler) {
      modal.removeEventListener("click", modal._modalClickHandler);
      delete modal._modalClickHandler;
    }
  },

  _onKeyDown(e, modal) {
    if (e.key === "Escape") {
      this.close(modal.id);
      return;
    }

    if (e.key === "Tab") {
      const focusable = Array.from(
        modal.querySelectorAll(this.focusableSelector),
      );
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  },

  _onClick(e, modal) {
    if (e.target === modal) {
      this.close(modal.id);
    }
  },
};

// Render chat interface inside modal
function renderChatInModal(modelName) {
  const modelSafeId = safeId(modelName);
  const contentDiv = document.getElementById("chat-modal-content");
  if (!contentDiv) return;

  if (!chatHistory[modelName]) {
    chatHistory[modelName] = [];
  }

  // Get model data for performance metrics and max tokens
  const modelData = currentModelRows.find((r) => r.model === modelName);
  const isRunning =
    modelData && modelData.status && modelData.status.startsWith("running");
  const ppVal = isRunning && modelData.pp ? modelData.pp + " ms" : "-";
  const tgVal = isRunning && modelData.tg ? modelData.tg + " t/s" : "-";
  const pgVal = isRunning && modelData.pg ? modelData.pg : "-";
  const maxTokens = modelData?.cache_size || 512;

  contentDiv.innerHTML = `
    <div class="tile-chat modal-chat">
      <div class="chat-params">
        <div class="panel-item"><span class="label">Temperature</span>
          <input type="range" id="chat-temp-${modelSafeId}" min="0" max="2" step="0.1" value="0.7"
                 oninput="document.getElementById('chat-temp-val-${modelSafeId}').textContent = this.value">
          <span class="value" id="chat-temp-val-${modelSafeId}">0.7</span>
        </div>
        <div class="panel-item"><span class="label">Top P</span>
          <input type="range" id="chat-top-p-${modelSafeId}" min="0" max="1" step="0.05" value="0.9"
                 oninput="document.getElementById('chat-top-p-val-${modelSafeId}').textContent = this.value">
          <span class="value" id="chat-top-p-val-${modelSafeId}">0.9</span>
        </div>
         <div class="panel-item"><span class="label">Max Tokens</span>
            <input type="number" id="chat-max-tokens-${modelSafeId}" min="1" max="${he(String(maxTokens))}" value="${he(String(maxTokens))}">
          </div>
         <div class="panel-item"><span class="label">Timeout (s)</span>
            <input type="number" id="chat-timeout-${modelSafeId}" min="1" max="600" value="300">
          </div>
        </div>
        <div class="panel-section">
          <h3>Performance Metrics</h3>
          <div class="panel-grid">
            <div class="panel-item"><span class="label">Prompt Processing</span><span class="value" id="chat-pp-${modelSafeId}">${he(ppVal)}</span></div>
            <div class="panel-item"><span class="label">Text Generation</span><span class="value" id="chat-tg-${modelSafeId}">${he(tgVal)}</span></div>
            <div class="panel-item"><span class="label">Combined</span><span class="value" id="chat-pg-${modelSafeId}">${he(pgVal)}</span></div>
          </div>
        </div>
        <!-- Chat header with title and clear button -->
        <div class="chat-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
          <h3 style="margin: 0;">Chat</h3>
          <button id="chat-modal-clear" class="btn-chat-clear">Clear Conversation</button>
        </div>
        <div class="chat-messages" id="chat-messages-${modelSafeId}"></div>
        <div class="chat-input-area">
          <textarea id="chat-input-${modelSafeId}" placeholder="Type your message..." rows="3"></textarea>
          <button id="chat-send-${modelSafeId}" class="btn-chat-send">Send</button>
        </div>
     `;

  // Render existing messages
  renderChatMessages(modelName);

  // Restore input from localStorage
  const savedInput = localStorage.getItem("chat_input_" + modelName);
  const input = document.getElementById(`chat-input-${modelSafeId}`);
  if (input && savedInput !== null) {
    input.value = savedInput;
  }

  // Attach send button handler
  const sendBtn = document.getElementById(`chat-send-${modelSafeId}`);
  if (sendBtn) {
    sendBtn.onclick = () => sendChatMessage(modelName);
  }

  // Attach textarea Enter-to-send (Shift+Enter for newline)
  if (input) {
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage(modelName);
      }
    });
    // Save draft on input changes
    input.addEventListener("input", () => {
      localStorage.setItem("chat_input_" + modelName, input.value);
    });
    input.focus();
  }
}
