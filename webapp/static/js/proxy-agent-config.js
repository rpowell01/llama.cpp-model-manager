/**
 * Mnemonic Proxy Agent Configuration Generator
 * 
 * Generates configuration snippets for various AI agents to connect to mnemonic-proxy.
 * 
 * Supported agents:
 * - claude-code: Claude Code (ANTHROPIC_BASE_URL)
 * - codex: OpenAI Codex (model_providers.local + wire_api=responses)
 * - pi: pi/omp (provider baseUrl)
 * - opencode: opencode (provider mnemonic)
 * - hermes: Hermes
 * - continue: Continue.dev
 * - aider: Aider
 * - cursor: Cursor
 */

// Agent configuration templates
const AGENT_CONFIGS = {
    'claude-code': {
        name: 'Claude Code',
        description: 'Anthropic\'s Claude Code CLI',
        envVars: {
            'ANTHROPIC_BASE_URL': '{proxy_url}/v1',
            'ANTHROPIC_API_KEY': 'dummy-key', // Not needed for local proxy
        },
        reminder: 'Set DISABLE_AUTO_COMPACT=1 in your shell config to prevent context compaction',
        configFile: '~/.bashrc or ~/.zshrc (for env vars)',
        example: `export ANTHROPIC_BASE_URL="{proxy_url}/v1"
export ANTHROPIC_API_KEY="dummy-key"
export DISABLE_AUTO_COMPACT=1

# Then run: claude-code`
    },
    'codex': {
        name: 'OpenAI Codex',
        description: 'OpenAI\'s Codex CLI',
        configFile: '~/.codex/config.toml',
        toml: `[model_providers.local]
name = "mnemonic-proxy"
base_url = "{proxy_url}/v1"
api_key = "dummy-key"
wire_api = "responses"

[model]
provider = "local"
model = "{model_id}"`,
        reminder: 'Add compaction.enabled = false to prevent auto-compaction',
        example: `[model_providers.local]
name = "mnemonic-proxy"
base_url = "{proxy_url}/v1"
api_key = "dummy-key"
wire_api = "responses"

[model]
provider = "local"
model = "{model_id}"

# Add to prevent compaction:
# [model_providers.local]
# compaction.enabled = false`
    },
    'pi': {
        name: 'pi / omp',
        description: 'pi (personal intelligence) and omp CLI',
        configFile: '~/.config/pi/config.json',
        json: `{
  "providers": {
    "mnemonic": {
      "type": "openai",
      "baseUrl": "{proxy_url}/v1",
      "apiKey": "dummy-key",
      "models": ["{model_id}"]
    }
  },
  "defaultProvider": "mnemonic"
}`,
        reminder: 'Disable auto-compaction in pi settings',
        example: `{
  "providers": {
    "mnemonic": {
      "type": "openai",
      "baseUrl": "{proxy_url}/v1",
      "apiKey": "dummy-key",
      "models": ["{model_id}"]
    }
  },
  "defaultProvider": "mnemonic"
}`
    },
    'opencode': {
        name: 'opencode',
        description: 'opencode AI coding agent',
        configFile: '~/.config/opencode/config.json',
        json: `{
  "providers": {
    "mnemonic": {
      "type": "openai",
      "baseUrl": "{proxy_url}/v1",
      "apiKey": "dummy-key"
    }
  },
  "model": "{model_id}",
  "provider": "mnemonic"
}`,
        reminder: 'Set compaction.enabled = false in opencode config',
        example: `{
  "providers": {
    "mnemonic": {
      "type": "openai",
      "baseUrl": "{proxy_url}/v1",
      "apiKey": "dummy-key"
    }
  },
  "model": "{model_id}",
  "provider": "mnemonic"
}`
    },
    'hermes': {
        name: 'Hermes',
        description: 'Hermes AI assistant',
        configFile: '~/.config/hermes/config.yaml',
        yaml: `providers:
  mnemonic:
    type: openai
    base_url: "{proxy_url}/v1"
    api_key: "dummy-key"
    models:
      - "{model_id}"

default_provider: mnemonic
default_model: "{model_id}"`,
        reminder: 'Disable auto-compaction in Hermes settings',
        example: `providers:
  mnemonic:
    type: openai
    base_url: "{proxy_url}/v1"
    api_key: "dummy-key"
    models:
      - "{model_id}"

default_provider: mnemonic
default_model: "{model_id}"

# Add to prevent compaction:
# compaction:
#   enabled: false`
    },
    'continue': {
        name: 'Continue.dev',
        description: 'Continue VS Code / JetBrains extension',
        configFile: '~/.continue/config.json',
        json: `{
  "models": [
    {
      "title": "mnemonic-proxy ({model_id})",
      "provider": "openai",
      "model": "{model_id}",
      "apiBase": "{proxy_url}/v1",
      "apiKey": "dummy-key",
      "contextLength": 131072
    }
  ],
  "customCommands": [],
  "tabAutocompleteModel": {
    "title": "mnemonic-proxy ({model_id})",
    "provider": "openai",
    "model": "{model_id}",
    "apiBase": "{proxy_url}/v1",
    "apiKey": "dummy-key"
  }
}`,
        reminder: 'Set "disableAutoCompact": true in Continue settings',
        example: `{
  "models": [
    {
      "title": "mnemonic-proxy ({model_id})",
      "provider": "openai",
      "model": "{model_id}",
      "apiBase": "{proxy_url}/v1",
      "apiKey": "dummy-key",
      "contextLength": 131072
    }
  ],
  "tabAutocompleteModel": {
    "title": "mnemonic-proxy ({model_id})",
    "provider": "openai",
    "model": "{model_id}",
    "apiBase": "{proxy_url}/v1",
    "apiKey": "dummy-key"
  }
}`
    },
    'aider': {
        name: 'Aider',
        description: 'Aider AI pair programming',
        configFile: '~/.aider.conf.yml',
        yaml: `model: {model_id}
api_base: {proxy_url}/v1
api_key: dummy-key
weak_model: {model_id}
editor_model: {model_id}
# Disable auto-compaction
compact_model: {model_id}
compact_threshold: 0.95`,
        reminder: 'Set compact_threshold high or use --no-auto-compact flag',
        example: `model: {model_id}
api_base: {proxy_url}/v1
api_key: dummy-key
weak_model: {model_id}
editor_model: {model_id}

# Run with: aider --no-auto-compact`
    },
    'cursor': {
        name: 'Cursor',
        description: 'Cursor AI editor',
        configFile: 'Cursor Settings > Models > Add Custom Model',
        json: `{
  "name": "mnemonic-proxy ({model_id})",
  "provider": "openai",
  "baseUrl": "{proxy_url}/v1",
  "apiKey": "dummy-key",
  "model": "{model_id}"
}`,
        reminder: 'Disable auto-compaction in Cursor settings',
        example: `In Cursor Settings > Models > Add Custom Model:
- Name: mnemonic-proxy ({model_id})
- Provider: OpenAI Compatible
- Base URL: {proxy_url}/v1
- API Key: dummy-key
- Model: {model_id}`
    },
    'kilo': {
        name: 'Kilo Code',
        description: 'Kilo Code AI coding assistant',
        configFile: '~/.kilo/kilo.jsonc',
        jsonc: `{
  // Mnemonic Proxy configuration for Kilo Code
  "mnemonic": {
    "name": "mnemonic-proxy",
    "npm": "@ai-sdk/openai-compatible",
    "options": {
      "baseURL": "{proxy_url}/v1",
      "apiKey": "dummy-key",
      "rateLimitSeconds": 0.1,
      "maxRetries": 3
    },
    "models": {
      "{model_id}": {
        "attachment": true,
        "reasoning": true,
        "tool_call": true,
        "structured_output": true,
        "temperature": true,
        "limit": {
          "context": {context},
          "input": {input_limit},
          "output": {output_limit}
        },
        {modalities}
        "supported_parameters": [
          "include_reasoning",
          "max_tokens",
          "reasoning",
          "temperature",
          "tool_choice",
          "tools"
        ],
        "default_parameters": {}
      }
    }
  },
  "defaultProvider": "mnemonic",
  "defaultModel": "{model_id}",
  // Disable auto-compaction to preserve context
  "compaction": {
    "enabled": false
  }
}`,
        reminder: 'Place config at ~/.kilo/kilo.jsonc (JSONC format supports comments). Note: Kilo Code expects baseUrl WITHOUT /v1 suffix (it appends /v1 automatically). Uses "provider" (singular) not "providers".',
        example: `{
  // Mnemonic Proxy configuration for Kilo Code
  "mnemonic": {
    "name": "mnemonic-proxy",
    "npm": "@ai-sdk/openai-compatible",
    "options": {
      "baseURL": "{proxy_url}/v1",
      "apiKey": "dummy-key",
      "rateLimitSeconds": 0.1,
      "maxRetries": 3
    },
    "models": {
      "{model_id}": {
        "attachment": true,
        "reasoning": true,
        "tool_call": true,
        "structured_output": true,
        "temperature": true,
        "limit": {
          "context": {context},
          "input": {input_limit},
          "output": {output_limit}
        },
        "supported_parameters": [
          "include_reasoning",
          "max_tokens",
          "reasoning",
          "temperature",
          "tool_choice",
          "tools"
        ],
        "default_parameters": {}
      }
    }
  },
  "defaultProvider": "mnemonic",
  "defaultModel": "{model_id}",
  // Disable auto-compaction to preserve context
  "compaction": {
    "enabled": false
  }
}`
    }
};

/**
 * Generate configuration for a specific agent
 * @param {string} agentType - Agent type (e.g., 'claude-code', 'codex', 'pi', etc.)
 * @param {string} proxyUrl - Base URL of mnemonic-proxy (e.g., 'http://localhost:8096')
 * @param {string} modelId - Model identifier
 * @param {number} [context] - Model context size (optional, used for kilo config limits)
 * @param {string} [mmproj] - Multimodal projector file path (optional, used for kilo config modalities)
 * @returns {Object} Configuration object with formatted config and metadata
 */
function generateConfig(agentType, proxyUrl, modelId, context, mmproj) {
    const agent = AGENT_CONFIGS[agentType];
    if (!agent) {
        throw new Error(`Unknown agent type: ${agentType}`);
    }
    
    // Normalize proxy URL (ensure no trailing slash)
    const normalizedUrl = proxyUrl.replace(/\/$/, '');
    
    // Calculate limits for kilo if context is provided
    let contextLimit = 131072;
    let inputLimit = 111411;
    let outputLimit = 19661;
    
    if (context && agentType === 'kilo') {
        contextLimit = context;
        // Use 70/30 ratio: input = 70% of context, output = 30% of context
        inputLimit = Math.round(context * 0.7);
        outputLimit = Math.round(context * 0.3);
        
        // Ensure output doesn't exceed context (leave at least 1 for input)
        outputLimit = Math.min(outputLimit, contextLimit - 1);
        // Ensure input + output doesn't exceed context
        inputLimit = Math.min(inputLimit, contextLimit - outputLimit);
        // Minimum bounds
        inputLimit = Math.max(inputLimit, 1);
        outputLimit = Math.max(outputLimit, 1);
    }
    
    // Build modalities block for kilo if mmproj is set
    let modalitiesBlock = '';
    if (agentType === 'kilo' && mmproj && mmproj.trim()) {
        modalitiesBlock = `"modalities": {
          "input": ["text", "image"],
          "output": ["text"]
        },`;
    }
    
    let config = '';
    let format = 'text';
    
    // Helper to replace all occurrences
    const replaceAll = (str, search, replace) => str.split(search).join(replace);
    
    if (agent.envVars) {
        // Environment variable format (Claude Code)
        config = Object.entries(agent.envVars)
            .map(([key, value]) => `export ${key}="${replaceAll(value, '{proxy_url}', normalizedUrl)}"`)
            .join('\n');
        format = 'shell';
    } else if (agent.toml) {
        config = replaceAll(replaceAll(agent.toml, '{proxy_url}', normalizedUrl), '{model_id}', modelId);
        format = 'toml';
    } else if (agent.json || agent.jsonc) {
        // JSON or JSONC format
        config = replaceAll(replaceAll(agent.jsonc || agent.json, '{proxy_url}', normalizedUrl), '{model_id}', modelId);
        // Replace modalities placeholder for kilo
        config = replaceAll(config, '{modalities}', modalitiesBlock);
        format = agent.jsonc ? 'jsonc' : 'json';
    } else if (agent.yaml) {
        config = replaceAll(replaceAll(agent.yaml, '{proxy_url}', normalizedUrl), '{model_id}', modelId);
        format = 'yaml';
    }
    
    // Replace context limit placeholders for kilo (unquoted in template for numeric values)
    config = replaceAll(config, '{context}', contextLimit);
    config = replaceAll(config, '{input_limit}', inputLimit);
    config = replaceAll(config, '{output_limit}', outputLimit);
    
    // Also replace in example
    let example = replaceAll(replaceAll(agent.example, '{proxy_url}', normalizedUrl), '{model_id}', modelId);
    example = replaceAll(example, '{context}', contextLimit);
    example = replaceAll(example, '{input_limit}', inputLimit);
    example = replaceAll(example, '{output_limit}', outputLimit);
    
    return {
        agentType,
        agentName: agent.name,
        description: agent.description,
        configFile: agent.configFile,
        format,
        config,
        reminder: agent.reminder,
        example
    };
}

/**
 * Get list of supported agents
 * @returns {Array} List of agent objects with type, name, description
 */
function getSupportedAgents() {
    return Object.entries(AGENT_CONFIGS).map(([type, agent]) => ({
        type,
        name: agent.name,
        description: agent.description,
        configFile: agent.configFile
    }));
}

/**
 * Generate all agent configurations at once
 * @param {string} proxyUrl - Base URL of mnemonic-proxy
 * @param {string} modelId - Model identifier
 * @param {number} [context] - Model context size (optional, used for kilo config limits)
 * @param {string} [mmproj] - Multimodal projector file path (optional, used for kilo config modalities)
 * @returns {Object} Map of agentType to config object
 */
function generateAllConfigs(proxyUrl, modelId, context, mmproj) {
    const configs = {};
    for (const agentType of Object.keys(AGENT_CONFIGS)) {
        configs[agentType] = generateConfig(agentType, proxyUrl, modelId, context, mmproj);
    }
    return configs;
}

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { generateConfig, getSupportedAgents, generateAllConfigs, AGENT_CONFIGS };
}

// Export for browser usage
if (typeof window !== 'undefined') {
    window.ProxyAgentConfig = { generateConfig, getSupportedAgents, generateAllConfigs };
}