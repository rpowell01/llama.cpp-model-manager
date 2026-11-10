# Mnemonic Proxy Documentation

This document provides comprehensive documentation for the **mnemonic-proxy** integration in the Llama.cpp Model Manager, covering command-line parameters, configuration options (config.json), and their usage/functionality.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Installation & Requirements](#installation--requirements)
4. [Command-Line Parameters](#command-line-parameters)
5. [Configuration Options (config.json)](#configuration-options-configjson)
6. [Integration with Llama.cpp Model Manager](#integration-with-llamacpp-model-manager)
7. [Key Configuration Examples](#key-configuration-examples)
8. [Advanced Features](#advanced-features)
9. [Troubleshooting](#troubleshooting)

---

## Overview

**mnemonic-proxy** is a context management proxy that sits between AI agents (Claude Code, Codex, Cursor, etc.) and llama-server instances. It provides long-context management capabilities including:

- **Verbatim Archive**: Full conversation history stored in SQLite with JSONL journal logging
- **Recall Tool**: Semantic search and retrieval of past conversation context
- **Saved Engine State**: Automatic save/restore of llama-server KV cache slots for instant context switching
- **Context Masking**: Intelligent masking of old context to fit within window limits while preserving recent and important content
- **Automatic Configuration**: Smart defaults based on engine capabilities and machine resources
- **MCP Server**: Optional read-only Model Context Protocol server for external tool integration

**Repository**: https://github.com/lastreload/mnemonic-proxy
**Documentation Source**: https://github.com/lastreload/mnemonic-proxy/blob/main/docs/configuration.md

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  AI Agents      │────▶│  mnemonic-proxy  │────▶│  llama-server    │
│  (Claude Code,  │     │  (port 8096)     │     │  (port 9931+)    │
│   Codex, etc.)  │     │                  │     │                  │
└─────────────────┘     └──────────────────┘     └──────────────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │  Data Storage    │
                     │  - archive.sqlite│
                     │  - journal.jsonl │
                     │  - slot files    │
                     └──────────────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │  mnemonic-mcp    │
                     │  (port 8133)     │
                     │  (optional)      │
                     └──────────────────┘
```

### Data Flow

1. **AI Agent** sends request to **mnemonic-proxy** on port 8096
2. **mnemonic-proxy** manages context:
   - Archives full conversation verbatim
   - Masks old context when approaching window limits
   - Saves/restores llama-server KV cache state via slot files
   - Provides recall tool for semantic retrieval
3. **mnemonic-proxy** forwards request to **llama-server** on port 9931+
4. **llama-server** generates response
5. **mnemonic-proxy** archives response and returns to AI Agent

---

## Installation & Requirements

### Prerequisites

```bash
# Install mnemonic-proxy Python package
pip install mnemonic-proxy

# For exact token counting (recommended)
pip install 'mnemonic-proxy[exact]'

# For cold archive (kv_archive) - requires Python 3.14+ or zstandard
pip install zstandard
```

### System Requirements

- **Python**: 3.10+ (3.14+ recommended for kv_archive)
- **llama-server**: With `ctx_size >= 16384` (32768 recommended)
- **Saved State Support**: llama-server built with `--slot-save-path` support
- **RAM**: Sufficient for model + saved state files (~19.5 KB per token of window)

### Verification

```bash
# Check mnemonic-proxy installation
mnemonic-proxy --version

# Check available commands
mnemonic-proxy --help

# Generate example configuration
mnemonic-proxy init-config --engine llama-server --dir ./config
```

---

## Command-Line Parameters

### Main Proxy Command: `mnemonic-proxy`

| Option | Description | Default |
|--------|-------------|---------|
| `--upstream URL` | The engine's address (llama-server endpoint) | `http://127.0.0.1:8095` |
| `--host ADDR` | Bind address (use `0.0.0.0` for external access) | `127.0.0.1` |
| `--port N` | Listen port for proxy | `8096` |
| `--data DIR` | Archive, journal and lock directory | `./data` |
| `--config FILE` | JSON file with configuration fields | None |
| `--mode tools\|off` | `tools` = managed context (default); `off` = plain pass-through | `tools` |
| `--tokenizer PATH` | Hugging Face `tokenizer.json` or Strata pack `tokenizer/` folder for exact token counts | None |
| `--api-key KEY` | API key sent to upstream engine | None |
| `--engine auto\|strata\|llama.cpp\|ds4\|openai` | Force engine type instead of auto-detection | `auto` |
| `--version` | Print version and exit | - |

### Subcommand: `mnemonic-proxy demo`

| Option | Description |
|--------|-------------|
| `--keep DIR` | Keep demo's archive in DIR (`archive.sqlite`, `journal.jsonl`) instead of temporary folder |

### Subcommand: `mnemonic-proxy check`

Validates configuration and environment without starting the proxy.

| Option | Description |
|--------|-------------|
| All server options | Accepts all `mnemonic-proxy` server options above |
| `--live` | Also run a real generation, tool call, and save/restore on the engine |
| `--json` | Machine-readable report: `{"ready", "exit_code", "checks": [...], "auto_config": {...}}` |
| `--explain-config` | Print automatic configuration in full: what was read about engine/machine, every option chosen with reason and how to force otherwise |

### Subcommand: `mnemonic-proxy init-config`

Generates a starter configuration file.

| Option | Description |
|--------|-------------|
| `--engine NAME` | Target engine: `strata`, `llama-server`, `ds4`, `openai-32k`, `official-strata`, `llama-server-32k`, `llama-server-router` (required) |
| `--dir DIR` | Folder to write file in (created if missing) | `.` |
| `--name FILE` | File name to write | `config.json` |
| `--force` | Overwrite existing file (without it, exits with code 1 if file exists) |

### Subcommand: `mnemonic-proxy forget`

Removes conversations from the archive.

| Option | Description |
|--------|-------------|
| `--data DIR` | Data folder to delete from (required) |
| `--list` | List conversation IDs, logical sizes, and UTC timestamps |
| `--conv ID` | Conversation ID to remove |
| `--dry-run` | Report what would be removed, change nothing (requires `--conv`) |
| `--slot-dir DIR`, `--kv-archive DIR` | Saved-state and cold-archive folders when not in config |
| `--config FILE` | Proxy's config; defaults to `$MNEMONIC_CONFIG` |

### MCP Server: `mnemonic-mcp`

Read-only MCP recall server for external tool integration.

| Option | Description |
|--------|-------------|
| `--db FILE` | Proxy's `archive.sqlite`, opened read-only (required) |
| `--journal FILE` | `journal.jsonl`; defaults to one next to database |
| `--max-tokens N` | Token cap of one recall result | `6000` |
| `--tokenizer PATH` | Tokenizer for token counts |
| `--http PORT` | Streamable HTTP on `/mcp` instead of stdio |
| `--host ADDR` | Bind address for `--http` | `127.0.0.1` |

---

## Configuration Options (config.json)

The configuration is a JSON object with fields from `ctxproxy.core.Config`. The proxy reads from:
1. `--config` file (highest priority)
2. Command-line arguments
3. Automatic configuration (based on engine/machine detection)
4. Built-in defaults (lowest priority)

### Core Configuration Fields

#### Window & Context Management

| Field | Default | Description |
|-------|---------|-------------|
| `window` | `131072` | Physical context window of the engine. For llama-server, ds4, Strata, Ollama, LM Studio: taken from engine if unset. Ollama/LM Studio: larger value refuses start. |
| `window_margin` | `64` | Window guard: tokens kept free as safety margin |
| `window_estimate_slack` | `0` (0.15 on generic engines without `--tokenizer`) | Inflation factor for estimated prompt token count |

#### Masking Configuration

| Field | Default | Description |
|-------|---------|-------------|
| `mask_enabled` | `true` | Level 1 on/off: with `false` nothing is ever masked (archive still records everything) |
| `mask_trigger` | `80000` | Start a masking block above this token count |
| `mask_target` | `40000` | Mask oldest-first down to this token count |
| `min_batch_tokens` | `16000` | A masking block runs only if it frees at least this many tokens |
| `keep_recent_tokens` | `16000` | Protected tail: never masked |
| `min_age_turns` | `2` | Minimum age in turns before content can be masked |
| `mask_reasoning` | `false` | Also mask old reasoning content (opt-in: changes prompt) |
| `mask_tool_args` | `false` | Also mask large old tool-call arguments (opt-in: changes prompt) |

#### Mask Anchor (Saved State at Masking Frontier)

| Field | Default | Description |
|-------|---------|-------------|
| `mask_anchor` | `false` | Save/restore engine state at the masking frontier (saved state) |
| `anchor_min_tokens` | `24000` | Minimum tokens before creating an anchor |
| `slot_dir` | `""` | Engine's `--slot-save-path` folder (same host). Required for anchor cleanup, autosave, kv_archive. |

#### Segments & Handoff Notes

| Field | Default | Description |
|-------|---------|-------------|
| `segments_enabled` | `true` | Enable segment switching with handoff notes |
| `tail_max` | `24000` | Maximum tokens in segment tail |
| `notes_max_tokens` | `8192` | Maximum tokens for handoff notes (auto-scaled: `window * 3 / 32`) |

#### Saved State (Slot Save/Restore)

| Field | Default | Description |
|-------|---------|-------------|
| `slot_save` | `false` | Save old segment to disk before switching (saved state) |
| `seal_experimental` | `false` | Experimental save optimization (opt-in) |
| `response_floor` | `0` | Minimum useful response budget for segment switching (bounded to quarter of window; auto-scaled: `window / 8`) |
| `data_dir` | `""` | Where physical prompt of outgoing segment is written when `slot_save` is on; empty = `--data` folder |

#### Automatic Configuration

| Field | Default | Description |
|-------|---------|-------------|
| `auto_config` | `true` | Choose engine/machine-dependent options at startup and print every choice with reason (`check --explain-config`) |
| `state_kb_per_token` | `19.5` | KB of engine state per token of window (20000 bytes/token of autosave.py), used to check state file fits in available RAM |

#### Recall Tool Configuration

| Field | Default | Description |
|-------|---------|-------------|
| `inject_recall` | `true` | Enable the `recall` tool |
| `max_recall_rounds` | `4` | Maximum recall rounds per request |
| `recall_max_tokens` | `6000` | Token cap of one recall result |
| `recall_max_chars` | `24000` | Character cap of one recall result |
| `recall_struct_max_tokens` | `3500` | Token cap of structured recall result |
| `recall_tool_name` | `"recall"` | Name of proxy's recall tool for new conversations |
| `tools_tool_name` | `"tools"` | Name of proxy's tools tool for new conversations |
| `prompt_language` | `"en"` | Language of texts proxy writes into prompt: `"en"` or `"it"` (stored per conversation at first use) |
| `rich_receipts` | `false` | Opt-in: richer receipt for hidden tool output (stored per conversation at first use) |

#### Structured Recall (Opt-in: Changes Prompt)

| Field | Default | Description |
|-------|---------|-------------|
| `recall_struct` | `false` | Structured recall: file timelines, links (benchmark supported) |
| `recall_flex` | `false` | Flexible word matching in recall |
| `recall_multi` | `false` | Multiple queries per recall call |

#### Automatic Recall

| Field | Default | Description |
|-------|---------|-------------|
| `auto_recall` | `false` | Enable automatic retrieval on new user messages |
| `auto_recall_hint` | `false` | Only select one-line hint with IDs instead of pieces (requires `auto_recall: true`) |
| `auto_recall_on_error` | `false` | Automatic retrieval also after failed tool result |
| `auto_recall_k` | `4` | Pieces at most per injection |
| `auto_recall_max_tokens` | `4000` | Cap of injected message |
| `auto_recall_piece_tokens` | `1200` | Cap of each piece |
| `auto_recall_min_score` | `8.0` | Threshold on FTS5 bm25 score |
| `auto_recall_min_terms` | `2` | Distinct query terms a piece must contain (common excluded) |
| `auto_recall_common_frac` | `0.4` | Term appearing in more than this share of hits counts as common |
| `auto_recall_max_args` | `1` | Tool-call arguments at most per injection |
| `auto_recall_hint_k` | `5` | Candidates named in a hint |

#### Tools Paging (Opt-in: Changes Prompt)

| Field | Default | Description |
|-------|---------|-------------|
| `tools_paging` | `false` | Tools on demand: only core tools + `recall` + `tools` in prompt |
| `tools_core` | `read/bash/edit/write/grep/find/ls + read_file/write_file/patch/search_files/terminal` | Core tool names (Hermes base names) |
| `tools_load_max` | `5` | How many tool definitions one `tools` call may load |
| `tooldefs_desc_max` | `0` | Shorten each tool's description to this many characters (0 = off) |
| `tooldefs_param_max` | `0` | Same for parameter descriptions |
| `tooldefs_keep` | `recall, read, bash, edit, write, grep, find, ls` | Tools whose definitions are never shortened |

#### Receipts & Guards

| Field | Default | Description |
|-------|---------|-------------|
| `typed_receipts` | `true` | Typed one-line receipts for hidden outputs |
| `receipt_guard` | `true` | Block write/edit/shell calls that copy a receipt or shortened old argument |
| `journal_tool_heads` | `true` | Journal one `tool_heads` line per conversation and tool-list change |

#### Pins & Carryover

| Field | Default | Description |
|-------|---------|-------------|
| `pins_max_tokens` | `8192` | Cap of carried-over 📌 block |
| `keep_first_user` | `true` | Carry first user message over to new segment |
| `index_max_tokens` | `2000` | Cap of segment index block |

#### Autosave (Idle State Persistence)

| Field | Default | Description |
|-------|---------|-------------|
| `autosave` | `false` | Save engine state when conversation is idle (saved state) |
| `autosave_idle_s` | `180` | Idle seconds before autosave triggers |
| `autosave_keep` | `10` | Maximum autosave files to keep |
| `autosave_max_gb` | `25` | Maximum total size of autosave files (GB) |
| `autosave_min_free_gb` | `8` | No save when slot disk has less free space |
| `autosave_min_tokens` | `16384` | Below this, re-read is cheaper than state file: no save |
| `autosave_poll_s` | `10` | How often autosaver checks engine |

#### Auto-Restore

| Field | Default | Description |
|-------|---------|-------------|
| `autorestore` | `true` | Restore before forwarding when it saves at least `autorestore_min_gain` tokens |
| `autorestore_min_gain` | `8192` | Minimum token gain to trigger restore |
| `autorestore_idle_s` | `600` | Idle seconds before autorestore considered |

#### Cold Archive (kv_archive)

Requires Python ≥ 3.14 for `compression.zstd` or `zstandard` package.

| Field | Default | Description |
|-------|---------|-------------|
| `kv_archive` | `false` | Cold archive of state files (background compression thread) |
| `kv_archive_idle_s` | `600` | Idle seconds before cold archive checks |
| `kv_archive_min_age_s` | `1800` | Minimum age of state files before archiving |
| `kv_archive_level` | `3` | zstd compression level |
| `kv_archive_threads` | `4` | zstd threads |
| `kv_archive_poll_s` | `60` | How often cold archiver checks |
| `kv_archive_min_bytes` | `67108864` | Smaller state files left where they are (64 MB) |

#### Engine & Token Counting

| Field | Default | Description |
|-------|---------|-------------|
| `engine` | `"auto"` | Engine type: `auto`, `strata`, `llama.cpp`, `ds4`, `openai` |
| `slot_id` | `0` | Engine slot ID |
| `engine_tokenize` | `false` | Token counting via engine's `/tokenize` endpoint (auto-chosen when available) |
| `chars_per_token` | `3.5` | Characters per token when no tokenizer available |
| `msg_overhead` | `6` | Tokens chat template adds per message |

#### Router Mode (llama-server)

| Field | Default | Description |
|-------|---------|-------------|
| `router_model` | `""` | Model used when client's `model` is not served (empty = refuse) |
| `router_load_timeout_s` | `600` | How long a model may take to load |
| `router_keepalive_s` | `10` | Seconds between SSE comments to streaming client while model loads |

#### Vision & Images

| Field | Default | Description |
|-------|---------|-------------|
| `image_tokens` | `1026` | Tokens charged for one picture (`vision.max_tokens + 2`). Must satisfy `image_tokens >= vision.max_tokens + 2`. |
| `reasoning_last_turn_only` | `false` | Strata template: keep only last turn's reasoning |

#### Answer Thinking & Truncation Guard (Experimental)

| Field | Default | Description |
|-------|---------|-------------|
| `answer_thinking_after_recall` | `false` | Turn thinking on only for answer following recall |
| `answer_thinking_budget` | `512` | Reasoning budget for that answer |
| `answer_min_tokens` | `512` | Thinking budget always leaves at least this room for answer |
| `answer_thinking_strip` | `true` | Reasoning of replays not returned to client |
| `truncation_guard` | `false` | Replay once with thinking a short answer ending on prefix of recalled token |
| `truncation_guard_max_tokens` | `16` | Only answers up to this many tokens replayed |
| `truncation_guard_budget` | `512` | Reasoning cap of replay (0 = no cap) |

#### Notes & Handoff

| Field | Default | Description |
|-------|---------|-------------|
| `notes_thinking` | `"auto"` | Thinking while writing handoff notes: `auto` (on when engine leaves room) \| `off` \| `on` |

#### Recall Removal Policy

| Field | Default | Description |
|-------|---------|-------------|
| `recall_removal` | `"value"` | What recall loop gives up first when space runs out: `value` \| `reasoning` \| `age` |

#### Final Drop Internal Tools

| Field | Default | Description |
|-------|---------|-------------|
| `final_drop_internal_tools` | `true` | Final call made for lack of space goes without proxy's own tools |

#### Live Dump

| Field | Default | Description |
|-------|---------|-------------|
| `live_dump` | `false` | Write `data/live/last_request.json` for dashboard (opt-in: writes full prompt to disk) |

#### Checkpoint Alignment

| Field | Default | Description |
|-------|---------|-------------|
| `checkpoint_align_slack` | `0.25` | Accepted waste after checkpoint multiple |

---

## Integration with Llama.cpp Model Manager

### Configuration in models.json

Each model can have a `mnemonic_proxy` section:

```json
{
  "name": "my-model",
  "model_path": "/path/to/model.gguf",
  "gpu_layers": 99,
  "ctx_size": 32768,
  "mnemonic_proxy": {
    "enabled": true,
    "port": 8096,
    "data_dir": "./data/my-model",
    "slot_dir": "./data/my-model/slots",
    "tokenizer_path": "",
    "config_override": {
      "mask_anchor": true,
      "autosave": true,
      "window_estimate_slack": 0.15
    },
    "mcp_enabled": false,
    "mcp_port": 8133
  }
}
```

### Default Configuration (from config.py)

The Llama.cpp Model Manager provides these defaults in `config.py`:

```python
"mnemonic_proxy": {
    "enabled": False,
    "port": 8096,
    "data_dir": "",
    "slot_dir": "./data/<model name>",
    "tokenizer_path": "",
    "config_override": {
        "mask_anchor": True,
        "autosave": True,
        "window_estimate_slack": 0.15,
        # Cold archive (kv_archive) - requires Python >= 3.14 or zstandard package
        # "kv_archive": True,
        # "kv_archive_idle_s": 600,
        # "kv_archive_min_age_s": 1800,
        # "kv_archive_level": 3,
        # "kv_archive_threads": 4,
        # "kv_archive_poll_s": 60,
        # "kv_archive_min_bytes": 67108864,  # 64 MB
    },
    "mcp_enabled": False,
    "kv_archive": True,
    "kv_archive_idle_s": 600,
    "kv_archive_min_age_s": 1800,
    "kv_archive_level": 3,
    "kv_archive_threads": 4,
    "kv_archive_poll_s": 60,
    "kv_archive_min_bytes": 67108864,
    "autosave": True,
    "autosave_idle_s": 60,
    "autosave_min_tokens": 4096,
    "auto_recall": True,
    "autorestore": True,
    "autorestore_min_gain": 8192,
    "autorestore_idle_s": 600,
    "mask_anchor": True,
    "mask_reasoning": True,
    "mask_trigger": 14000,
    "mask_tool_args": True,
    "mask_target": 8000,
    "response_floor": 16384,
    "seal_experimental": True,
    "slot_save": True,
    "tools_paging": True,
    "upstream": "http://127.0.0.1:9931",
    "mcp_port": 8133,
}
```

### Enabling Mnemonic Proxy

1. **Install mnemonic-proxy**:
   ```bash
   pip install mnemonic-proxy
   # For exact token counting
   pip install 'mnemonic-proxy[exact]'
   # For kv_archive
   pip install zstandard
   ```

2. **Enable in model configuration**:
   ```json
   "mnemonic_proxy": {
     "enabled": true,
     "port": 8096,
     "slot_dir": "./data/my-model/slots"
   }
   ```

3. **Configure llama-server with slot saving**:
   ```bash
   llama-server -m model.gguf -c 32768 --slot-save-path ./data/my-model/slots --port 9931
   ```

4. **Start the model manager** - it will automatically start mnemonic-proxy for enabled models

### Port Allocation

| Service | Default Port | Configuration |
|---------|--------------|---------------|
| llama-server | 9931+ | Model-specific |
| mnemonic-proxy | 8096 | Per-model (increments for multiple models) |
| mnemonic-mcp | 8133 | Per-model (increments for multiple models) |
| Model Manager Web UI | 5000 | Fixed |

---

## Key Configuration Examples

### 1. Basic Llama-Server with Saved State (Recommended)

```json
{
  "mask_reasoning": true,
  "mask_tool_args": true,
  "mask_anchor": true,
  "slot_dir": "/path/to/slots",
  "slot_save": true,
  "seal_experimental": true,
  "notes_max_tokens": 12288,
  "response_floor": 16384,
  "recall_struct": true,
  "recall_flex": true,
  "recall_multi": true,
  "auto_recall": false,
  "tools_paging": true,
  "autosave": true
}
```

### 2. Generic OpenAI Engine (No Saved State)

```json
{
  "window": 131072,
  "mask_reasoning": true,
  "mask_tool_args": true,
  "recall_struct": true,
  "recall_flex": true,
  "recall_multi": true,
  "tools_paging": true,
  "auto_recall": false
}
```
*Note: Saved-state fields (`mask_anchor`, `slot_dir`, `slot_save`, `seal_experimental`, `autosave`, `kv_archive`) are automatically disabled with a warning for non-llama.cpp engines.*

### 3. High-Performance Configuration (Strata/Llama-Server)

```json
{
  "window": 131072,
  "mask_trigger": 80000,
  "mask_target": 40000,
  "min_batch_tokens": 16000,
  "keep_recent_tokens": 16000,
  "min_age_turns": 2,
  "mask_reasoning": true,
  "mask_tool_args": true,
  "mask_anchor": true,
  "anchor_min_tokens": 24000,
  "slot_dir": "/fast/nvme/slots",
  "segments_enabled": true,
  "tail_max": 24000,
  "notes_max_tokens": 12288,
  "slot_save": true,
  "seal_experimental": true,
  "response_floor": 16384,
  "slot_save": true,
  "autosave": true,
  "autosave_idle_s": 180,
  "autosave_keep": 10,
  "autosave_max_gb": 25,
  "autosave_min_free_gb": 8,
  "autorestore": true,
  "autorestore_min_gain": 8192,
  "kv_archive": true,
  "kv_archive_idle_s": 600,
  "kv_archive_min_age_s": 1800,
  "kv_archive_level": 3,
  "kv_archive_threads": 4,
  "engine_tokenize": true,
  "tools_paging": true,
  "recall_struct": true,
  "recall_flex": true,
  "recall_multi": true,
  "auto_recall": false
}
```

### 4. Memory-Constrained Environment (32K Context)

```json
{
  "window": 32768,
  "response_floor": 4096,
  "notes_max_tokens": 3072,
  "anchor_min_tokens": 6000,
  "mask_anchor": true,
  "slot_dir": "./slots",
  "slot_save": true,
  "autosave": true,
  "autosave_min_free_gb": 4
}
```
*For 32K windows, prefer removing overrides to get scaled defaults, or start conservatively.*

---

## Advanced Features

### Automatic Configuration (`auto_config`)

Since v0.3.23, mnemonic-proxy automatically detects engine capabilities and machine resources:

**What it reads:**
- Engine kind, context window, saved-state support (from `/v1/status`, `/props`, `/slots`)
- Engine version (Strata: `/v1/status` → `engine`; llama-server: `/props` → `build_info`)
- Concurrency (Strata: `/v1/status` → `concurrency`; llama-server: `/props` → `total_slots`)
- RAM (total/available), VRAM (from engine or `nvidia-smi`)
- Free disk space of `slot_dir`

**What it decides:**
| Field | Condition | Rule |
|-------|-----------|------|
| `slot_save`, `mask_anchor`, `autosave` | Engine supports saved state, runs on this host, `slot_dir` set, state file fits in RAM | Enable all three |
| `response_floor` | Window ≥ 16384 | `window / 8` (16384 at 131072, 4096 at 32768) |
| `notes_max_tokens` | Window ≥ 16384 | `window * 3 / 32` (12288 at 131072) |
| `engine_tokenize` | Engine has `/tokenize`, no `--tokenizer` given | Enable for exact counting |

**Check automatic config:**
```bash
mnemonic-proxy check --explain-config --upstream http://127.0.0.1:9931 --config config.json
```

### Data Folder Provenance

The automatic configuration behavior depends on data folder origin:

| Data Folder | Behavior |
|-------------|----------|
| **New** (not there/empty) | Choices applied, recorded in `autoconfig.json` |
| **Created with auto-config** | Choices keep being applied at every start |
| **From earlier setup** | Choices only reported (marked `kept`), nothing changes |

Force apply on earlier folder: `"auto_config": true` in config
Treat as earlier folder: delete `autoconfig.json`

### MCP Server Integration

The optional `mnemonic-mcp` provides a read-only MCP server for external tools:

```bash
# Start MCP server
mnemonic-mcp --db ./data/my-model/archive.sqlite --http 8133
```

**MCP Tools Available:**
- `recall` - Semantic search of conversation history
- `tools` - List available tools

### Tokenizer for Exact Counting

For accurate token counting (recommended):

```bash
# Download tokenizer for your model
# Example for Llama models:
wget https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct/resolve/main/tokenizer.json

# Use with proxy
mnemonic-proxy --tokenizer ./tokenizer.json --upstream http://127.0.0.1:9931
```

Or in config.json:
```json
{
  "engine_tokenize": true,
  "tokenizer_path": "./tokenizer.json"
}
```

### Cold Archive (kv_archive)

Compresses old state files to save disk space:

**Requirements:**
- Python ≥ 3.14 (stdlib `compression.zstd`) OR `zstandard` package
- Enable in config: `"kv_archive": true`

**How it works:**
- Runs background thread every `kv_archive_poll_s` (default 60s)
- Compresses state files older than `kv_archive_min_age_s` (default 1800s)
- Files smaller than `kv_archive_min_bytes` (default 64MB) left uncompressed
- Uses zstd level `kv_archive_level` (default 3) with `kv_archive_threads` (default 4)

---

## Troubleshooting

### Common Issues

#### 1. "mnemonic-proxy not found"
```bash
pip install mnemonic-proxy
# Verify
mnemonic-proxy --version
```

#### 2. "Saved state not supported by engine"
- Ensure llama-server was built with slot saving support
- Check `--slot-save-path` is set on llama-server
- Verify `slot_dir` in proxy config matches llama-server's `--slot-save-path`

#### 3. "State file doesn't fit in RAM"
- Increase available RAM or reduce `window`
- Disable saved state: `"slot_save": false, "mask_anchor": false, "autosave": false`
- Check with `check --explain-config`

#### 4. "Port already in use"
- Each model needs unique proxy/MCP ports
- Check `port`, `mcp_port` in model config
- Default: proxy=8096, mcp=8133 (auto-incremented for multiple models)

#### 5. "kv_archive requires Python 3.14+ or zstandard"
```bash
pip install zstandard
# Or upgrade Python to 3.14+
```

#### 6. Token counting inaccurate
```bash
# Install exact tokenizer support
pip install 'mnemonic-proxy[exact]'
# Provide tokenizer
mnemonic-proxy --tokenizer ./tokenizer.json
```

### Debugging Commands

```bash
# Full configuration explanation
mnemonic-proxy check --explain-config --upstream http://127.0.0.1:9931 --config config.json

# Machine-readable health check
mnemonic-proxy check --json --upstream http://127.0.0.1:9931 --config config.json

# Live test (generation + tool call + save/restore)
mnemonic-proxy check --live --upstream http://127.0.0.1:9931 --config config.json

# List conversations in archive
mnemonic-proxy forget --data ./data/my-model --list

# Remove specific conversation
mnemonic-proxy forget --data ./data/my-model --conv <conversation-id>
```

### Log Locations

- **Proxy logs**: stdout/stderr (captured by Model Manager)
- **Archive**: `<data_dir>/archive.sqlite`
- **Journal**: `<data_dir>/journal.jsonl`
- **Slot files**: `<slot_dir>/slot_*.bin`
- **Autosave**: `<slot_dir>/autosave_*.bin`
- **Cold archive**: `<slot_dir>/archive/*.zst`
- **Live dump**: `<data_dir>/live/last_request.json` (if `live_dump: true`)

---

## Quick Reference: Recommended Settings by Use Case

| Use Case | Key Settings |
|----------|--------------|
| **Maximum Context Retention** | `mask_anchor: true`, `slot_save: true`, `autosave: true`, `kv_archive: true`, `autorestore: true` |
| **Fastest Response** | `response_floor: 16384`, `notes_max_tokens: 12288`, `tools_paging: true`, `auto_recall: false` |
| **Structured Recall** | `recall_struct: true`, `recall_flex: true`, `recall_multi: true` |
| **Memory Constrained** | Reduce `window`, `autosave_max_gb`, `kv_archive_min_bytes`, disable `seal_experimental` |
| **Multiple Models** | Unique `port`, `mcp_port`, `data_dir`, `slot_dir` per model |
| **External Tool Access** | `mcp_enabled: true`, start `mnemonic-mcp` |

---

## Version Compatibility

| Component | Minimum Version | Recommended |
|-----------|-----------------|-------------|
| mnemonic-proxy | 0.3.23+ | Latest |
| llama-server | With slot-save support | Latest |
| Python | 3.10+ | 3.14+ (for kv_archive) |
| zstandard | - | Latest (if Python < 3.14) |

---

## References

- **mnemonic-proxy GitHub**: https://github.com/lastreload/mnemonic-proxy
- **Configuration Documentation**: https://github.com/lastreload/mnemonic-proxy/blob/main/docs/configuration.md
- **Dev Notes**: https://github.com/lastreload/mnemonic-proxy/tree/main/docs/dev-notes
- **Example Configurations**: https://github.com/lastreload/mnemonic-proxy/tree/main/examples

---

*This documentation is based on mnemonic-proxy configuration.md and the Llama.cpp Model Manager integration code. For the most current information, refer to the official mnemonic-proxy documentation.*