# Llama.cpp Model Manager

A comprehensive web-based interface for managing, configuring, and optimizing [llama.cpp](https://github.com/ggerganov/llama.cpp) model deployments. Provides an intuitive dashboard for monitoring, starting/stopping models, chat proxying, performance analysis, and context management via [mnemonic-proxy](https://github.com/lastreload/mnemonic-proxy).

---

## Credits

This project builds on the excellent work of two open-source teams:

- **[llama.cpp team](https://github.com/ggerganov/llama.cpp)** — for the `llama-server` inference engine that powers model serving, with its rich parameter set, KV cache slot support, speculative decoding, and multi-backend (CPU/Vulkan/SYCL/OpenVINO) capabilities.
- **[Mnemonics Proxy team](https://github.com/lastreload/mnemonic-proxy)** — for the context management proxy that enables long-context conversations via KV cache slot save/restore, context masking, recall tools, and semantic conversation archiving.

---

## Overview

The Llama.cpp Model Manager is a Flask-based web application that provides:

- **Model lifecycle management** — start, stop, list, and monitor multiple llama-server instances
- **Chat proxy** — OpenAI-compatible chat completion endpoint routed through llama-server or mnemonic-proxy
- **Slot KV cache persistence** — save/restore model context across sessions via llama-server slot files
- **Mnemonic-proxy integration** — context masking, automatic recall, and semantic archiving for long conversations
- **Performance metrics** — collect, scrape, and export per-model metrics (tokens/sec, ms/token, acceptance rates)
- **Hugging Face integration** — search and discover models from the Hugging Face Hub
- **Model downloading** — background download management for GGUF model files
- **System information** — hardware detection (CPU, GPU, memory), logs, and server health
- **WSL support** — Windows Subsystem for Linux with PowerShell wrapper for Intel oneAPI/SYCL
- **Speculative decoding** — ngram and draft-model speculation configuration
- **Background metrics scheduler** — periodic automatic metrics export

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Model CRUD** | Add, list, start, stop, delete models via web UI or REST API |
| **Chat API** | `/api/chat` — OpenAI-compatible chat completion proxy |
| **Mnemonic Proxy** | Long-context management with KV cache slots, masking, and recall |
| **Slot Persistence** | Save/restore KV cache per model session for context continuity |
| **Metrics Collection** | Scrape `/metrics` from running models and export to CSV |
| **Background Scheduler** | Automatic periodic metrics export |
| **HF Integration** | Search Hugging Face models via `/api/hf-repo-search` |
| **Downloads** | Resumable GGUF model downloads with progress tracking |
| **Hardware Detection** | CPU, GPU (NVIDIA/AMD/Intel), memory detection |
| **WSL Support** | Full Windows Subsystem for Linux support with PowerShell wrapper |
| **Speculative Decoding** | Ngram and draft model speculation parameters |
| **SSE Logging** | Real-time log streaming to the web console |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Web Browser (Frontend)                 │
└─────────────────────────────────────────────────────────────┘
                              │ HTTP
┌─────────────────────────────────────────────────────────────┐
│              Flask Web App (webapp/main.py)                 │
│  Port 5000  •  static/  •  templates/  •  SSE logging       │
├─────────────────────────────────────────────────────────────┤
│  chat     → proxy to llama-server / mnemonic-proxy          │
│  models   → CRUD + registry                                 │
│  lifecycle → start/stop model processes                     │
│  status   → health, debug, proxy management                 │
│  system   → hardware info, logs, shutdown                   │
│  downloads → GGUF download management                       │
│  huggingface → HF model search                              │
└─────────────────────────────────────────────────────────────┘
                              │ HTTP / slot restore
┌─────────────────────────────────────────────────────────────┐
│             llama-server (llama.cpp inference)              │
│  Ports 9931+  •  KV cache slots  •  /metrics endpoint        │
└─────────────────────────────────────────────────────────────┘
                              │ optional
┌─────────────────────────────────────────────────────────────┐
│           mnemonic-proxy (context management)               │
│  Port 8096  •  KV slot save/restore  •  Recall tool          │
└─────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
llama.cpp-model-manager/
├── webapp/              # Web application (Flask)
│   ├── main.py          # Flask entry point
│   ├── config.py        # Configuration & defaults
│   ├── models.py        # Model registry & management
│   ├── processes.py     # llama-server process lifecycle
│   ├── scheduler.py     # Background metrics export scheduler
│   ├── state.py         # Shared runtime state & SSE logging
│   ├── metrics.py       # Metrics scraping & parsing
│   ├── mnemonic_proxy.py # Mnemonic-proxy integration
│   ├── mnemonic_proxy_updater.py # mnemonic-proxy package updater
│   ├── benchmarks.py    # llama-bench runner
│   ├── downloads.py     # GGUF download management
│   ├── exporter.py      # Metrics CSV exporter
│   ├── kilo_config.py   # Kilo Code config generator
│   ├── llama_updater.py # llama.cpp binary updater
│   ├── optimizer.py     # Benchmark sweep optimizer
│   ├── utils.py         # Utility functions
│   ├── compression_zstd_shim.py # zstd shim for Python < 3.14
│   ├── routes/          # Flask route blueprints
│   │   ├── chat.py          # /api/chat
│   │   ├── downloads.py     # /api/download
│   │   ├── huggingface.py   # /api/hf-repo-search
│   │   ├── lifecycle.py     # /api/models/load|unload
│   │   ├── models.py        # /api/add-model|delete-model
│   │   ├── status.py        # /api/status|/api/next-port
│   │   └── system.py        # /api/system-info|/api/logs
│   ├── scripts/         # Wrapper scripts
│   │   ├── llama-server-wrapper.ps1     # Windows PowerShell wrapper
│   │   └── llama-server-sycl-wrapper.sh # Linux SYCL wrapper
│   ├── static/          # Static assets (CSS, JS)
│   ├── templates/       # HTML templates
│   └── data/            # JSON data files
│       ├── models.json                  # Model registry (runtime)
│       ├── mnemonic_default_config_override.json
│       └── .llama-platform-config.json
├── routes/              # (legacy, redirects to webapp/routes)
├── tests/               # Test suite (pytest)
├── examples/            # Example files
│   ├── models.example.json    # Example model configuration
│   └── example_benchmark_result.json
├── docs/                # Documentation
│   ├── BENCHMARK_DESIGN.md
│   ├── CODING_TEST_PROMPT.md
│   ├── llama.cpp-parameters-reference.md
│   └── SCHEDULING_METRICS_INVESTIGATION.md
├── logs/                # Runtime log files (webapp.log, server.log, etc.)
├── README.md            # This file
├── AGENTS.md            # Agent configuration
├── CLAUDE.md            # Claude configuration
├── start.sh             # Start script (Linux/macOS/WSL)
├── start.ps1            # Start script (Windows PowerShell)
├── LICENSE              # MIT License
└── requirements.txt     # Python dependencies
```

---

## Getting Started

### Prerequisites

- **Python 3.10+** (3.14+ recommended for zstd cold archive)
- **llama-server** built with KV cache slot save support (`--slot-save-path`)
- **pip** packages: see `requirements.txt`

### Installation

#### Linux / macOS / WSL

```bash
# Clone the repository
git clone https://github.com/rpowell01/llama.cpp-model-manager.git
cd llama.cpp-model-manager

# Start the application (creates venv, installs deps, launches Flask)
./start.sh
```

#### Windows (PowerShell)

```powershell
# Clone the repository
git clone https://github.com/rpowell01/llama.cpp-model-manager.git
cd llama.cpp-model-manager

# Start the application
./start.ps1
```

The start scripts handle virtual environment creation, dependency installation, and launching the Flask app. By default the web UI is available at **http://localhost:5000**.

## Configuration

### Model Registry (`webapp/data/models.json`)

Copy the example and edit for your models:

```bash
cp examples/models.example.json webapp/data/models.json
```

Each model entry supports fields such as:

| Field | Description | Example |
|-------|-------------|---------|
| `name` | Human-readable model name | `qwen3-30b-a3b` |
| `repo` | Hugging Face repository | `Qwen/Qwen3-30B-A3B` |
| `filename` | GGUF filename | `Qwen3-30B-A3B-Instruct-Q4_K_M.gguf` |
| `expected_size` | Expected file size in bytes | `18000000000` |
| `port` | llama-server port | `9931` |
| `gpu_layers` | Layers offloaded to GPU | `99` |
| `threads` | CPU threads | `16` |
| `threads_batch` | Batch threads | `16` |
| `timeout` | Server timeout (seconds) | `3600` |
| `ctx_size` | Context window size | `131072` |
| `cache_type_k` | K cache quantization | `fp16`, `q4_0`, `q8_0` |
| `cache_type_v` | V cache quantization | `fp16`, `q4_0`, `q8_0` |
| `spec_type` | Speculative decoding type | `ngram-mod`, `none` |
| `spec_draft_model` | Draft model repo | `Qwen/Qwen3-1.7B` |
| `spec_ngram_mod_n_match` | Ngram match length | `24` |
| `spec_ngram_mod_n_min` | Min ngram size | `48` |
| `spec_ngram_mod_n_max` | Max ngram size | `64` |
| `slot_save_path` | KV cache slot directory | `./data/<model name>` |
| `chat_template_file` | Chat template path | `./templates/qwen_sharp_chat_template.jinja` |
| `mmproj` | Multimodal projector path | `./mmproj/Qwen3-VL-30B-A3B-Instruct.mmproj-Q8_0.gguf` |
| `mmproj_auto` | Auto-load mmproj | `true` |
| `mmproj_offload` | Offload mmproj to GPU | `true` |
| `llama_cpp_platform` | Target platform | `linux`, `windows`, `auto` |
| `llama_cpp_backend` | GPU backend | `vulkan`, `sycl`, `openvino`, `auto` |
| `device` | GPU device override | `vulkan`, `sycl`, `opencl`, `cuda`, `none` |
| `gpu_layers` | Layers offloaded to GPU | `99` |
| `threads` | CPU threads | `16` |
| `threads_batch` | Batch threads | `16` |
| `ctx_size` | Context window size | `131072` |
| `cache_type_k` | K cache quantization | `fp16`, `q4_0`, `q8_0` |
| `cache_type_v` | V cache quantization | `fp16`, `q4_0`, `q8_0` |
| `spec_type` | Speculative decoding | `ngram-mod`, `none` |
| `slot_save_path` | KV cache slot directory | `./data/<model name>` |
| `mnemonic_proxy` | Proxy config object | see below |

### Mnemonic Proxy Configuration

Each model can enable mnemonic-proxy for long-context management. The proxy is configured via a `config.json` in the model's data directory. Default configuration:

```json
{
  "kv_archive": true,
  "kv_archive_level": 3,
  "kv_archive_idle_s": 600,
  "kv_archive_min_age_s": 1800,
  "kv_archive_min_bytes": 67108864,
  "kv_archive_poll_s": 60,
  "kv_archive_threads": 2,
  "autosave": true,
  "autosave_idle_s": 30,
  "autosave_keep": 8,
  "autosave_max_gb": 10,
  "mask_anchor": true,
  "mask_reasoning": true,
  "mask_target": 8000,
  "mask_tool_args": true,
  "mask_trigger": 14000,
  "min_age_turns": 1,
  "min_batch_tokens": 2000,
  "notes_max_tokens": 12288,
  "recall_flex": true,
  "recall_multi": true,
  "recall_struct": true,
  "response_floor": 16384,
  "seal_experimental": true,
  "slot_save": true,
  "tools_paging": true,
  "tools_paging_native_split": true,
  "tools_paging_native_split_build": "",
  "tools_core": "read/bash/edit/write/grep/find/ls/execute + read_file/write_file/patch/search_files/terminal"
}
```

The `tools_core` parameter defines which tools are always available when `tools_paging` is enabled. Add `execute` (or any other tool) to the slash-separated list to make it automatically available.

To enable mnemonic-proxy for a model, add the following to the model's configuration in `models.json`:

```json
{
  "mnemonic_proxy": {
    "enabled": true,
    "port": 8096,
    "data_dir": "./data/my-model",
    "slot_dir": "./data/my-model/slots",
    "mcp_enabled": false,
    "mcp_port": 8133
  }
}
```

### Web Interface Config (`.llama-web-config.json`)

Binary preference (WSL Linux vs Windows) is persisted in `webapp/data/.llama-web-config.json`. Use the web UI or `start.sh`/`start.ps1` to toggle.

---

## API Endpoints

### Models
- `POST /api/add-model` — Register a new model
- `POST /api/delete-model` — Remove a model
- `POST /api/models/load` — Start a model
- `POST /api/models/unload` — Stop a model
- `GET /api/models` — List all models

### Chat
- `POST /api/chat` — OpenAI-compatible chat completion

### Status
- `GET /api/status` — Overall server status
- `GET /api/next-port` — Get an available port
- `GET /api/debug/<model>` — Detailed model debug info

### System
- `GET /api/system-info` — Hardware and system information
- `GET /api/logs` — Recent application logs

### Downloads
- `POST /api/download` — Download a GGUF model
- `GET /api/download-status` — Download progress

### Hugging Face
- `POST /api/hf-repo-search` — Search models on Hugging Face

---

## Usage

### Starting a Model

1. Add your model configuration to `webapp/data/models.json` (or via the web UI)
2. Start the web UI: `./start.sh` (Linux) or `./start.ps1` (Windows)
3. Navigate to `http://localhost:5000`
4. Select your model and click "Start"
5. Once running, use the chat interface to interact with the model

### Mnemonic Proxy (Long Context)

Enable `mnemonic_proxy.enabled: true` in a model's config to get:
- KV cache slot save/restore across sessions
- Context masking when approaching window limits
- Semantic recall of past conversations
- Automatic anchor-based state management



---

## Development

### Running Tests

```bash
pip install -r requirements.txt
pip install pytest
python -m pytest tests/
```

### Project Commands

- `./start.sh` — Start the web app (Linux/macOS/WSL)
- `./start.ps1` — Start the web app (Windows PowerShell)
- `python -m pytest tests/` — Run the test suite

---

## Credits

This project builds on the excellent work of:

- **[llama.cpp](https://github.com/ggerganov/llama.cpp)** by Georgi Gerganov and contributors — the inference engine powering `llama-server`, with KV cache slots, speculative decoding, and multi-backend GPU support.
- **[mnemonic-proxy](https://github.com/lastreload/mnemonic-proxy)** by lastreload — the context management proxy enabling long-context conversations via KV cache slot management, context masking, and semantic recall.

Thank you to both teams for their hard work and excellent open-source software.

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

The llama.cpp and mnemonic-proxy projects are separately licensed under their respective licenses.

---

## Support

For help with the Llama.cpp Model Manager:
- Review the documentation in `docs/`
- Consult the example configuration in `examples/models.example.json`
- Open an issue on GitHub if you encounter problems