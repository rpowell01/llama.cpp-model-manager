# Architecture Analysis: Local vs Remote Mode

## Current Architecture (Local Mode)

```
┌─────────────────────────────────────────────────────────────────┐
│                         Flask Web UI (port 5000)                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│  │   chat   │ │  models  │ │ lifecycle│ │  status  │         │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘         │
│       │            │            │            │                │
│       └────────────┴────────────┴────────────┴───────────────┘│
│                            │                                     │
│                   ┌──────▼──────┐                              │
│                   │  models.py  │  (in-memory registry)        │
│                   └─────────────┘                              │
│                            │                                     │
│  ┌─────────────────────────┼─────────────────────────────────┐ │
│  │                        ▼                                    │ │
│  │              ┌──────────────────┐                          │ │
│  │              │  processes.py    │                          │ │
│  │              │  - build_cmd()  │                          │ │
│  │              │  - start/stop   │                          │ │
│  │              │  - port/PID mgmt│                          │ │
│  │              └────────┬─────────┘                          │ │
│  │                       │                                      │ │
│  │         ┌─────────────┴─────────────┐                       │ │
│  │         │ subprocess.Popen(cmd)      │                       │ │
│  │         └────────────────────────────┘                       │ │
│  │                         │                                      │ │
│  │         ┌───────────────▼───────────────┐                   │ │
│  │         │   llama-server binary         │                   │ │
│  │         │   (runs on same machine)      │                   │ │
│  │         └───────────────────────────────┘                   │ │
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

**Key Characteristics:**
- Flask app and llama-server run on the same machine
- Process management via subprocess and OS signals
- Port-based discovery (PID lookup via `ss`/`lsof`/`netstat`)
- Platform-specific handling (Windows/WSL/Linux)
- Model registry persisted to `models.json` + filesystem scan

---

## Target Architecture (Remote Mode)

```
┌─────────────────────────────────────────────────────────────────┐
│                    Flask Web UI (port 5000)                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│  │   chat   │ │  models  │ │ lifecycle│ │  status  │         │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘         │
│       │            │            │            │                │
│       └────────────┴────────────┴────────────┴───────────────┘│
│                            │                                     │
│                   ┌──────▼──────┐                              │
│                   │  models.py  │  (local registry)            │
│                   └─────────────┘                              │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐ │
│  │              ┌──────────────────┐                         │ │
│  │              │  remote_client.py│                         │ │
│  │              │  (conditional)   │                         │ │
│  │              └────────┬─────────┘                         │ │
│  │                       │ (REST API calls)                   │ │
│  │         ┌─────────────▼─────────────┐                       │ │
│  │         │   requests/httpx          │                       │ │
│  │         └───────────────────────────┘                       │ │
│  │                         │                                      │ │
│  └─────────────────────────▼────────────────────────────────────│ │
│                                │                                 │ │
│                     ┌──────────▼──────────┐                    │ │
│                     │   REMOTE SERVER      │                    │ │
│                     │  (different host)    │                    │ │
│                     │  ┌──────────────┐   │                    │ │
│                     │  │   API Flask   │   │                    │ │
│                     │  │   (port 5001) │   │                    │ │
│                     │  └──────┬───────┘   │                    │ │
│                     │         │           │                    │ │
│                     │   ┌─────▼─────┐     │                    │ │
│                     │   │ processes │     │                    │ │
│                     │   │ (local)   │     │                    │ │
│                     │   └─────┬─────┘     │                    │ │
│                     │         │           │                    │ │
│                     │   ┌─────▼─────┐     │                    │ │
│                     │   │llama-server│    │                    │ │
│                     │   │(multiple)  │    │                    │ │
│                     │   └────────────┘    │                    │ │
│                     └──────────────────────┘                    │ │
└─────────────────────────────────────────────────────────────────┘
```

**Key Changes:**
- Introduction of `remote_client.py` abstraction layer
- Flask routes use remote_client (which delegates to HTTP API) instead of direct process calls
- Remote server runs its own Flask app with identical lifecycle/status APIs
- Model registry can be kept in sync via optional sync mechanism (out of scope for this design)
- Environment variables toggle between local and remote modes

---

## Process Management Call Sites

All call sites of process management functions:

### `processes.py` (definitions)
- `find_pid_by_port(port)` - line 34
- `pids_of_llama()` - line 141
- `_kill_tree(pid, model)` - line 190
- `check_port_active(port)` - line 260
- `_check_model_ready(port)` - line 271
- `build_cmd(model, port, meta)` - line 282

### `routes/lifecycle.py` (usage)
- Line 42, 50, 54, 108: `check_port_active(port)`
- Line 62: `build_cmd(model, port, meta)`
- Line 151: `pids_of_llama()`
- Line 161, 163, 164: `find_pid_by_port(port)`, `pids_of_llama()`
- Line 175: `_kill_tree(pid, model)`
- Line 189: `check_port_active(port)`

### `routes/status.py` (usage)
- Line 8: import `check_port_active, find_pid_by_port`
- Line 29: `check_port_active(port)` in `_status_for_model()`
- Line 54: `find_pid_by_port(port)`
- Line 65: `_check_model_ready(port)`
- Line 145: `check_port_active(port)` in `api_debug()`
- Line 165: `check_port_active(port)` in `api_next_port()`

### `routes/chat.py` (usage)
- Line 8: import `check_port_active`
- Line 41: `check_port_active(port_int)` in `api_chat()`

---

## remote_client.py Interface Specification

**Module:** `remote_client.py` (new)

**Purpose:** Provide drop-in replacements for local process management functions that communicate with a remote management server via HTTP.

**Environment Variables:**
- `REMOTE_MODE`: If set to `"1"`, `"true"`, or `"yes"`, remote mode is enabled. Default: unset (local mode).
- `LLAMA_CPP_SERVER_URL`: Base URL of remote management server (e.g., `http://192.168.1.100:5001`). Required when `REMOTE_MODE` is enabled.

**Interface Functions:**

```python
# process management API equivalent
def find_pid_by_port(port: int) -> int | None:
    """Get PID of model running on given port (remote: query server)."""
    # Remote: GET /api/status?port=11000 → returns model info including pid
    # Returns None if not found

def pids_of_llama() -> list[int]:
    """List all llama-server PIDs (remote: GET /api/status all)."""
    # Remote: aggregate PIDs from all running models

def _kill_tree(pid: int, model: str = "") -> bool:
    """Stop a model by PID (remote: POST /api/models/unload with model name)."""
    # Remote: use model name, not PID

def check_port_active(port: int) -> bool:
    """Check if port is listening (remote: GET /api/status?port=...)."""
    # Remote: check if model assigned to that port is running

def _check_model_ready(port: int) -> bool:
    """Check if model is ready to serve (remote: GET /api/models/<name>/ready)."""
    # Remote: can be combined with status check

def build_cmd(model: str, port: int | str, meta: dict[str, Any]) -> list[str]:
    """Construct command to start llama-server (remote: not used)."""
    # Remote mode: this function should never be called; raise error if invoked
```

**Additional Remote-Only Functions:**
```python
def start_model(model: str, meta: dict[str, Any]) -> tuple[bool, int | None]:
    """Start a model on remote server."""
    # POST /api/models/load with model config
    # Returns (success, remote_pid)

def get_model_status(model: str) -> dict:
    """Get detailed status for a model from remote server."""
    # GET /api/status/<model>

def get_all_models() -> dict[str, dict]:
    """Get remote server's model registry."""
    # GET /api/models
```

**Error Handling:**
- All remote calls should wrap `requests.exceptions.RequestException` and convert to appropriate return values (e.g., `None`, `False`, empty list).
- Add retry logic with exponential backoff (3 attempts, initial 0.5s, max 5s).
- Timeout per request: 5s for status checks, 30s for start/stop operations.
- Log failures with `log.error()` including endpoint and error details.
- On connection failure to remote server, return degraded response but do not crash.

**Implementation Pattern:**
```python
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

REMOTE_MODE = os.getenv("REMOTE_MODE", "").lower() in ("1", "true", "yes")
REMOTE_URL = os.getenv("LLAMA_CPP_SERVER_URL") if REMOTE_MODE else None

if REMOTE_MODE:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[502, 503, 504])
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))

def _remote_get(path: str, params: dict = None) -> dict | None:
    if not REMOTE_URL:
        raise RuntimeError("REMOTE_MODE enabled but LLAMA_CPP_SERVER_URL not set")
    try:
        resp = session.get(f"{REMOTE_URL}{path}", params=params, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Remote API GET {path} failed: {e}")
        return None

def _remote_post(path: str, json: dict) -> dict | None:
    if not REMOTE_URL:
        raise RuntimeError("REMOTE_MODE enabled but LLAMA_CPP_SERVER_URL not set")
    try:
        resp = session.post(f"{REMOTE_URL}{path}", json=json, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Remote API POST {path} failed: {e}")
        return None
```

---

## REST API Client Design (Remote Server Side)

**Base URL:** `http://<remote-host>:<port>/api`

### Endpoints

#### 1. `GET /api/models`
List all models known to the remote server's registry.

**Response:**
```json
{
  "models": {
    "llama-3-8b": {
      "model_path": "models/llama-3-8b/ggml-model-f16.gguf",
      "port": 11000,
      "status": "running",
      "pid": 12345
    },
    ...
  }
}
```

#### 2. `POST /api/models/load`
Start a model on the remote server.

**Request Body:**
```json
{
  "model": "llama-3-8b",
  "port": 11000,                    // optional, server assigns if omitted
  "cache_size": 32768,              // optional overrides
  "threads": 16,
  "threads_batch": 22,
  "timeout": 3600,
  "mlock": true,
  "flash_attn": "off",
  "no_mmap": true,
  "parallel": 4,
  "ubatch_size": 512
}
```

**Response:**
```json
{
  "status": "started",
  "model": "llama-3-8b",
  "port": 11000,
  "pid": 12345
}
```

Or error:
```json
{
  "error": "Model not found in registry",
  "status": "failed"
}
```

#### 3. `POST /api/models/unload`
Stop a running model.

**Request Body:**
```json
{
  "model": "llama-3-8b"
}
```

**Response:**
```json
{
  "status": "stopped",
  "model": "llama-3-8b",
  "pid": 12345
}
```

#### 4. `GET /api/status`
Get overall server status and all model statuses.

**Response:**
```json
{
  "server": "llama.cpp remote manager",
  "uptime": 3600,
  "models": [
    {
      "model": "llama-3-8b",
      "port": 11000,
      "status": "running",
      "pid": 12345,
      "file_exists": true,
      "file_size_gb": 7.5,
      ...
    }
  ]
}
```

Optionally filter by model: `GET /api/status/<model_name>`

#### 5. `GET /api/models/<model_name>/ready`
Check if a model's inference endpoint is ready (health check). Equivalent to `_check_model_ready()`.

**Response:**
```json
{
  "model": "llama-3-8b",
  "ready": true,
  "port": 11000
}
```

### Authentication & Security
- For now, assume trusted network (no auth). If needed in future, can add:
  - API key header: `Authorization: Bearer <token>`
  - Mutual TLS
- Should run over HTTPS in production if crossing network boundaries.

### Error Handling (Remote Server)
- Server returns proper HTTP status codes: 200 success, 400 bad request, 404 not found, 500 internal error
- Body includes `{"error": "description"}` for non-2xx

---

## Migration Plan: Local Functions → Remote API Calls

### Phase 1: Abstraction Layer (`remote_client.py`)
Create `remote_client.py` with two implementations:
- Local functions that wrap existing `processes.py` and `models.py` directly (default path)
- Remote functions that call REST API when `REMOTE_MODE=1`

### Phase 2: Update Call Sites
Replace direct imports from `processes` with imports from `remote_client`:

**In `routes/lifecycle.py`:**
```python
# OLD: from processes import find_pid_by_port, pids_of_llama, _kill_tree, check_port_active, _check_model_ready, build_cmd
# NEW:
from remote_client import check_port_active, find_pid_by_port, pids_of_llama, _kill_tree, start_model, _check_model_ready
# Note: build_cmd not needed if start_model handles it remotely
```

Modify `api_start()`:
```python
# OLD:
cmd = build_cmd(model, port, meta)
proc = subprocess.Popen(cmd, ...)

# NEW:
success, pid = start_model(model, meta)
port = meta["port"]  # remote server assigns/uses stored port
```

Modify `api_stop()`:
```python
# OLD:
pid = find_pid_by_port(port) or ...
success = _kill_tree(pid, model)

# NEW:
success = stop_model(model)  # remote_client.stop_model()
```

**In `routes/status.py`:**
Replace `processes` imports with `remote_client` equivalents:
```python
from remote_client import check_port_active, find_pid_by_port, _check_model_ready
```

**In `routes/chat.py`:**
Replace:
```python
from processes import check_port_active
```
with:
```python
from remote_client import check_port_active
```

Also update `target_url` in `api_chat()`:
```python
# OLD: target_url = f"http://127.0.0.1:{port_int}/v1/chat/completions"
# NEW (if remote mode):
# Option A: still proxy via local Flask to remote model port (model still runs on remote)
# Option B: remote server provides unified chat endpoint (e.g., /api/chat?model=X)
# For now, keep same pattern (remote server exposes llama-server on its own ports)
# But need to use REMOTE_URL to construct target:
target_url = f"{REMOTE_URL.rstrip('/')}/{port_int}/v1/chat/completions"
# However, this assumes remote server forwards ports. Better: remote server has
# a proxy endpoint. Design decision pending.
```

### Phase 3: Conditional Import in remote_client.py
```python
if REMOTE_MODE:
    # define remote API functions
else:
    # import and wrap local functions from processes.py
    from processes import (
        find_pid_by_port,
        pids_of_llama,
        _kill_tree,
        check_port_active,
        _check_model_ready,
    )
    # Start locally uses subprocess directly (reuse lifecycle.py logic or extract)
```

### Phase 4: Remote Server Implementation (separate)
- Create a second Flask app (maybe `remote_server.py`) that exposes the `/api/models/load`, `/api/models/unload`, `/api/status` endpoints
- Uses the same `processes.py` and `models.py` internally
- Can be deployed on the compute node

### Phase 5: Testing & Validation
- Test local mode unchanged
- Test remote mode with mock server
- Ensure environment variable fallback works

---

## Environment Variable Contract

| Variable | Type | Required | Purpose | Example |
|----------|------|----------|---------|---------|
| `REMOTE_MODE` | string (boolean-like) | No (default local) | Enable remote management | `1`, `true`, `yes` |
| `LLAMA_CPP_SERVER_URL` | string | Yes if `REMOTE_MODE` set | Base URL of remote server | `http://192.168.1.100:5001` |
| `REMOTE_TIMEOUT` | integer | No | Override default request timeout (seconds) | `30` |
| `REMOTE_RETRIES` | integer | No | Max retry attempts for failed requests | `3` |

**Validation:**
- On startup, if `REMOTE_MODE` is enabled but `LLAMA_CPP_SERVER_URL` is missing, Flask app should log error and fail to start (or fall back to local with warning? Likely fail to enforce explicit configuration).
- `REMOTE_MODE` check: case-insensitive, truthy values: `"1"`, `"true"`, `"yes"`, `"on"`.

---

## Design Decisions & Open Questions

1. **Chat Proxying:** Should the local Flask app proxy directly to remote `llama-server` ports, or should the remote server provide a unified chat endpoint?
   - Option A: Remote server exposes each model on its assigned port → local Flask still constructs `http://remote:port/v1/chat/completions`. Requires port forwarding/firewall rules for all model ports.
   - Option B: Remote server has a single `/api/chat` that routes to the correct model internally. Simpler (one port to expose). The local Flask could either proxy directly to remote `/api/chat` or let the client talk to remote server directly.
   - **Recommendation:** For remote mode, the Flask app operates as a pure management UI; chat requests go directly to the remote server's chat endpoint (configuration change on client side). Or the local Flask proxies to remote chat endpoint (keeping client API unchanged). We need to decide.

2. **Model Registry Synchronization:** Local `models.json` vs remote server's registry.
   - Should they be kept in sync? Probably the remote server is the source of truth for running models; local UI reads remote `/api/models` to populate the list.
   - But local `models.json` may contain additional metadata (repo, filename, expected_size) needed for downloading.
   - **Recommendation:** Keep local models.json as the configuration source. Remote mode uses the same model definitions but executes on remote. The remote server may have its own registry that must be seeded with the same models. Migration includes a sync step: POST `/api/models/register` to add model definitions to remote server.

3. **Port Management:** In remote mode, who assigns ports?
   - Local assigns ports in `models.py`. Remote server may assign differently. Need consistent mapping.
   - **Recommendation:** Local assigns ports as usual; these are passed to remote server on load. Remote server respects the requested port or reassigns and returns the actual port.

4. **Model Downloading:** Currently, the web UI can download models (huggingface integration). In remote mode, downloads would happen on the remote server or the local machine?
   - Remote server: local machine downloads models, then remote server pulls from local? Not ideal.
   - Alternative: downloads happen locally, model files are transferred to remote server (out of scope).
   - **Recommendation:** Remote mode assumes models are pre-loaded on the remote server (or the remote server manages downloading separately). The local UI's download functionality should be disabled or hidden in remote mode.

5. **Metrics:** Remote server's metrics endpoint (`/metrics`) may be on a different port. The Flask metrics collector (`metrics._fetch_metrics_from_model`) assumes localhost. Need to adjust.
   - Could use remote server to aggregate metrics and expose them via its management API.

---

## Validation & Next Steps

**Codebase Review Items:**
1. Verify all process management imports are centralized (they are: `processes.py`).
2. Confirm no other modules call `subprocess` directly for llama-server (grep).
3. Identify any assumptions about localhost/ports in chat proxy and metrics.
4. Ensure `build_cmd()` is only used in `lifecycle.py` (it is).
5. Check state tracking (`state.py`) is only used locally (it is, via `lifecycle.py` and `status.py`).

**Upon Approval:**
- Implement `remote_client.py` with local fallback
- Refactor route files to use `remote_client`
- Create `remote_server.py` (or extend main.py with remote endpoints) - separate bead
- Add integration tests
