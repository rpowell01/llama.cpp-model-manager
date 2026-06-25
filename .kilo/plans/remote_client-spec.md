# Migration Plan: Local → Remote Mode

## Step-by-Step Code Changes

### Step 0: Prerequisites
- Ensure `processes.py`, `models.py`, `state.py` are stable (they are).
- Create `remote_client.py` according to interface spec.
- Enable detailed logging for remote client debug.

---

### Step 1: Implement `remote_client.py` (no integration yet)

Create the module with:
- Environment variable detection
- Local fallback imports from `processes`
- Remote implementation for all functions
- `start_model()` remote-only helper
- Retry logic and error handling

**Verify:** Unit tests pass for both local and remote modes (mock remote server).

---

### Step 2: Update `routes/lifecycle.py`

**Change 1:** Update imports
```python
# OLD
from processes import find_pid_by_port, pids_of_llama, _kill_tree, check_port_active, _check_model_ready, build_cmd
# NEW
from remote_client import find_pid_by_port, pids_of_llama, _kill_tree, check_port_active, _check_model_ready, build_cmd, start_model
```

**Change 2:** Simplify `api_start()` with conditional
```python
def api_start():
    ...
    model = data.get("model", "").strip()
    ...
    meta = models[model]
    port = int(meta["port"])

    # Check if already running
    active = check_port_active(port)
    if active:
        # existing logic using find_pid_by_port remains unchanged
        ...

    # --- Process startup (conditional) ---
    if REMOTE_MODE:
        # Remote mode: call remote API
        success, remote_pid = start_model(model, meta)
        if success:
            # In remote mode, we don't have proc.poll() to check; rely on immediate success + port check
            # The remote server should start the model and return when launch initiated (not necessarily ready)
            # We can optionally poll check_port_active or _check_model_ready
            time.sleep(2.5)  # keep existing delay for consistency
            if check_port_active(port):  # or _check_model_ready(port) for stricter check
                with _recent_stops_lock:
                    _recent_stops.pop(model, None)
                log_event("start", model, port, remote_pid, "running")
                return jsonify({"status": "running", "pid": remote_pid, "port": port})
            else:
                log_event("start", model, port, remote_pid, "exited_early")
                return jsonify({"error": "Server exited immediately", "status": "exited"}), 500
        else:
            log_event("start", model, port, None, "failed")
            return jsonify({"error": "Failed to start remote model"}), 500
    else:
        # Local mode: existing subprocess.Popen block (lines 60-100 approximately)
        cmd = build_cmd(model, port, meta)
        ...
        # rest of current implementation
```

**Change 3:** In `api_stop()`, use remote_client._kill_tree which already switches
```python
# No change to: success = _kill_tree(pid, model)
# But _kill_tree now delegates to remote_client.stop_model() if remote
```

**Change 4:** Remove any direct calls to `subprocess` or `build_cmd` outside remote conditional (already covered).

---

### Step 3: Update `routes/status.py`

**Change 1:** Update imports
```python
# OLD
from processes import check_port_active, find_pid_by_port
# NEW
from remote_client import check_port_active, find_pid_by_port, _check_model_ready
```

**Change 2:** `_status_for_model()` no changes needed; uses the imported functions which now are remote-aware.

**Change 3:** Ensure `_check_model_ready` call uses remote_client's version (already imported).

**Change 4:** In `api_next_port()`, the logic uses `check_port_active(port)` and `assigned_ports`. No remote-specific changes needed, but note that `check_port_active` in remote mode checks remote server's model list, not actual OS port. This is okay because assigned_ports is from local models.json which should match remote's assignments. However, if remote reassigns ports, the local `port` field in models.json might be stale. 
**Mitigation:** After successful `start_model()` remote call, update `meta["port"]` with the returned port and `save_models()`. Similarly, `api_next_port()` should maybe query remote `/api/status` to get actual active ports. But for now, keep local truth; remote mode expects port assignments to be prenegotiated. We'll assume static assignments.

---

### Step 4: Update `routes/chat.py`

**Change 1:** Update import
```python
# OLD
from processes import check_port_active
# NEW
from remote_client import check_port_active
```

**Change 2:** Optionally adjust `target_url` for remote mode
- Current: `target_url = f"http://127.0.0.1:{port_int}/v1/chat/completions"`
- Remote mode needs to point to remote server's model port.
- Two options:
  A. Keep same URL but DNS/hosts entry points `127.0.0.1` to remote machine (not realistic)
  B. Use configuration: `REMOTE_URL` to construct base URL, then append port path
  C. Have remote server expose a unified chat endpoint that doesn't require port numbers

**Decision:** For minimal change, we can have remote_client expose a helper `get_model_base_url()` or we can pass a prefix. Simpler: in remote mode, the chat URL becomes `{REMOTE_URL.rstrip('/')}/{port_int}/v1/chat/completions` if the remote server forwards that path to the correct model. But that requires the remote server to have a reverse proxy setup.

Alternatively, we could change remote server to run a single chat endpoint: `/api/chat` that routes based on model name. Then local chat code would:
```python
if REMOTE_MODE:
    target_url = f"{REMOTE_URL}/api/chat"
    payload["model"] = model  # send model name
else:
    target_url = f"http://127.0.0.1:{port_int}/v1/chat/completions"
```

**This decision impacts remote server API design.** Let's decide to keep the current chat proxy unchanged and instead modify the remote server to expose each model on its assigned port (i.e., remote server runs the llama-server instances on their ports as usual). Then local chat code simply changes the host:
```python
from config import DEFAULTS
host = DEFAULTS["host"]  # 127.0.0.1 normally, but could override via env REMOTE_HOST?
```
Not sufficient.

Better: `remote_client` could provide a function `get_chat_url(model, port)` that returns the correct base. Or we modify `routes/chat.py`:
```python
if REMOTE_MODE:
    remote_base = REMOTE_URL.rstrip('/')
    target_url = f"{remote_base}/{port_int}/v1/chat/completions"
else:
    target_url = f"http://127.0.0.1:{port_int}/v1/chat/completions"
```
But this assumes the remote server's management API and llama-server ports are the same? Actually, the remote server (management Flask) runs on port 5001, while llama-server runs on 11000+, both on the remote host. So the chat URL should be `http://remote-host:port/v1/chat/completions`, not through management Flask.

Thus, we need to know the remote host's network address for the model ports. The `LLAMA_CPP_SERVER_URL` points to the management API, not necessarily the model ports. But if the remote server is on the same host as the management API, we can derive model host:port.

**Proposal:** `LLAMA_CPP_SERVER_URL` should be the base URL for both management API and model ports (i.e., the remote host's base address). Management API at `<base>/api/...`, model endpoints at `<base_without_specified_port>/<port>/v1/...`. But the management API runs on a specific port (e.g., 5001). So the URL looks like:
- Management API: `http://remote:5001/api/models/load`
- Chat: `http://remote:11000/v1/chat/completions`

Thus, we need to separate:
- `LLAMA_CPP_MANAGEMENT_URL` = `http://remote:5001`
- `LLAMA_CPP_MODEL_HOST` = `remote` (or `remote:port`? Actually models use direct ports)

Simplify: Use `LLAMA_CPP_SERVER_URL` for management only. For chat, we need to know the remote host (or IP) and the fact that model ports are directly accessible. We can store the mapping of model→port from models.json (local). So chat URL becomes `http://{REMOTE_MODEL_HOST}:{port}/v1/chat/completions`.

Define another env var: `REMOTE_MODEL_HOST` (or derive from LLAMA_CPP_SERVER_URL host part). Derivation:
```python
from urllib.parse import urlparse
REMOTE_MODEL_HOST = os.getenv("REMOTE_MODEL_HOST")
if not REMOTE_MODEL_HOST and REMOTE_URL:
    parsed = urlparse(REMOTE_URL)
    REMOTE_MODEL_HOST = parsed.hostname or "127.0.0.1"
    # If management port is different, we still use hostname for model ports
```

Then in `routes/chat.py`:
```python
if REMOTE_MODE:
    from remote_client import get_remote_model_host
    host = get_remote_model_host()  # returns hostname (no port)
    target_url = f"http://{host}:{port_int}/v1/chat/completions"
else:
    target_url = f"http://127.0.0.1:{port_int}/v1/chat/completions"
```

This keeps chat proxying unchanged except host.

---

## Detailed Remote Server API Specification

**Base URL:** `LLAMA_CPP_SERVER_URL`, e.g., `http://192.168.1.100:5001`

**Initialize:** Remote server must load same `models.json` (or have its own registry). For now, assume it has its own `models.json` that matches the local one (sync out of scope).

### Endpoint: `GET /api/status`

Query parameters:
- `port` (optional): filter to model with this port

Response 200:
```json
[
  {
    "model": "llama-3-8b",
    "port": 11000,
    "status": "running",
    "pid": 12345,
    "file_exists": true,
    "file_size_gb": 7.5,
    "model_path": "models/llama-3-8b/gguf/model.gguf",
    "repo": "meta-llama/Llama-3.1-8B",
    "filename": "model.gguf",
    "expected_size": 8053063680,
    ...
  }
]
```

If `?port=11000` given, returns single-element array (or could return the object directly; keep array for consistency or support both).

### Endpoint: `POST /api/models/load`

Request body:
```json
{
  "model": "llama-3-8b",
  "port": 11000,           // optional; if omitted, use model's configured port or assign sequentially
  "cache_size": 32768,
  "threads": 16,
  "threads_batch": 22,
  "timeout": 3600,
  "mlock": false,
  "flash_attn": "off",
  "no_mmap": true,
  "parallel": 2,
  "ubatch_size": 256
}
```

Response 200:
```json
{
  "status": "started",
  "model": "llama-3-8b",
  "port": 11000,
  "pid": 12345
}
```

Response 400/500:
```json
{
  "error": "Model not found in registry or file missing"
}
```

### Endpoint: `POST /api/models/unload`

Request body:
```json
{
  "model": "llama-3-8b"
}
```

Response 200:
```json
{
  "status": "stopped",
  "model": "llama-3-8b",
  "pid": 12345
}
```

### Endpoint: `GET /api/models`

List all models in the registry (not necessarily running).
Response:
```json
{
  "models": {
    "llama-3-8b": { "port": 11000, "file_exists": true, ... },
    "llama-2-7b": { "port": 11001, "file_exists": false, ... }
  }
}
```

---

## Rollback & Fallback Strategy

- If `REMOTE_MODE` is not set, system operates exactly as before (zero change).
- If `REMOTE_MODE` is set but `remote_client` encounters an exception (e.g., import error), Flask should fail to start (explicit crash) rather than silently ignore.
- At runtime, if remote server becomes unreachable:
  - `check_port_active()` returns `False` → UI shows model as stopped
  - `api_chat()` fails with `"Model is not running"` or proxy error 500
  - `api_start()` fails with `"Failed to start remote model"`
- Admin can restart remote server; no state corruption on local side.

---

## Open Questions & Assumptions

1. **Port Consistency**: Local models.json port assignments must match remote server's assignments. If remote server reassigns, we need a sync mechanism. For now, assume identical static configuration.
2. **Metrics**: Remote server's `/metrics` endpoint (prometheus) is on each model port. The local metrics collector (`routes/metrics.py`) queries these ports. In remote mode, those ports are remote; we either:
   - Expose remote metrics endpoints to the local Flask via direct host:port access (must be reachable)
   - Or remote server aggregates metrics and exposes via management API `/api/metrics`.
   We'll need to modify `metrics.py` to use `remote_client` as well (future bead).
3. **Model Downloads**: Hugging Face download endpoints in `routes/huggingface.py` and `routes/downloads.py` assume local file writing. In remote mode, these should be disabled or redirected to remote file system (complex). For now, we can disable those routes when `REMOTE_MODE` (return 503 Service Unavailable).
4. **Config Sync**: The local Flask's config.py determines `LLAMA_SERVER` binary location etc. In remote mode, these are irrelevant for lifecycle (but still used for chat target host?). We'll need to separate config concerns.

---

## Estimated Effort

| Task | Complexity | Notes |
|------|------------|-------|
| `remote_client.py` implementation | Medium | ~200 lines, careful error handling |
| Update `routes/lifecycle.py` | Medium | ~30 line changes, test both modes |
| Update `routes/status.py` | Low | just import change |
| Update `routes/chat.py` | Low-Medium | add remote host derivation (10 lines) |
| Remote server Flask app | Medium-High | New file: `remote_server.py` (separate bead) |
| Tests | Medium | Mock server, integration |
| Documentation | Low | Update README with env vars |
| **Total** | **Medium** | Can be done in one bead if remote server is out of scope (this bead is just analysis) |

This bead only requires analysis deliverables; implementation is later beads.

---

## Validation Checklist

Before considering migration complete:
- [ ] All process management calls go through `remote_client`
- [ ] `processes.py` is not imported by any route (except maybe `remote_client` in local mode)
- [ ] Unit tests pass for both modes (use mocking)
- [ ] Manual test: start/stop model in local mode → works
- [ ] Manual test: start/stop model in remote mode with test server → works
- [ ] Chat proxy works in both modes (adjust target URL)
- [ ] Status page correctly shows remote model statuses
- [ ] Error handling: remote server down → graceful degradation
- [ ] Logging: remote API calls appear with request/response details at DEBUG level

---

## Appendix: Sample remote_client.py Skeleton

```python
#!/usr/bin/env python3
"""
remote_client.py: Abstraction for local process management or remote REST API.
"""

from __future__ import annotations
import logging
import os
from typing import Any

log = logging.getLogger("llamaweb.remote_client")

# Environment detection
REMOTE_MODE = os.getenv("REMOTE_MODE", "").lower() in ("1", "true", "yes", "on")
REMOTE_URL = os.getenv("LLAMA_CPP_SERVER_URL") if REMOTE_MODE else None
REMOTE_TIMEOUT = int(os.getenv("REMOTE_TIMEOUT", "30"))
REMOTE_RETRIES = int(os.getenv("REMOTE_RETRIES", "3"))

if REMOTE_MODE and not REMOTE_URL:
    raise RuntimeError("REMOTE_MODE is enabled but LLAMA_CPP_SERVER_URL is not set")

# Local fallback: import from processes
if not REMOTE_MODE:
    from processes import (
        find_pid_by_port as _local_find_pid_by_port,
        pids_of_llama as _local_pids_of_llama,
        _kill_tree as _local_kill_tree,
        check_port_active as _local_check_port_active,
        _check_model_ready as _local_check_model_ready,
        build_cmd as _local_build_cmd,
    )
    # Also import state? No, state is used by routes directly.
else:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retries = Retry(total=REMOTE_RETRIES, backoff_factor=0.5, status_forcelist=[502,503,504])
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))

    def _remote_get(path: str, params: dict = None) -> dict | None:
        try:
            url = REMOTE_URL.rstrip("/") + path
            resp = session.get(url, params=params, timeout=REMOTE_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            log.error(f"Remote GET {path} failed: {e}")
            return None

    def _remote_post(path: str, json: dict) -> dict | None:
        try:
            url = REMOTE_URL.rstrip("/") + path
            resp = session.post(url, json=json, timeout=REMOTE_TIMEOUT * 2)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            log.error(f"Remote POST {path} failed: {e}")
            return None

# Public API implementations
def find_pid_by_port(port: int) -> int | None:
    if REMOTE_MODE:
        data = _remote_get("/api/status", params={"port": port})
        if data and len(data) > 0:
            return data[0].get("pid")
        return None
    return _local_find_pid_by_port(port)

def pids_of_llama() -> list[int]:
    if REMOTE_MODE:
        data = _remote_get("/api/status")
        if data:
            return [m.get("pid") for m in data if m.get("status") == "running" and m.get("pid")]
        return []
    return _local_pids_of_llama()

def check_port_active(port: int) -> bool:
    if REMOTE_MODE:
        data = _remote_get("/api/status", params={"port": port})
        if data and len(data) > 0:
            return data[0].get("status") == "running"
        return False
    return _local_check_port_active(port)

def _check_model_ready(port: int) -> bool:
    if REMOTE_MODE:
        data = _remote_get("/api/status", params={"port": port})
        if data and len(data) > 0:
            return data[0].get("status") == "running"
        return False
    return _local_check_model_ready(port)

def _kill_tree(pid: int, model: str = "") -> bool:
    if REMOTE_MODE:
        # remote uses model name, ignore pid
        if not model:
            log.warning("_kill_tree called with empty model in remote mode")
            return False
        resp = _remote_post("/api/models/unload", {"model": model})
        return resp is not None and resp.get("status") == "stopped"
    return _local_kill_tree(pid, model)

def build_cmd(model: str, port: int | str, meta: dict[str, Any]) -> list[str]:
    if REMOTE_MODE:
        raise RuntimeError("build_cmd() is not used in remote mode")
    return _local_build_cmd(model, port, meta)

def start_model(model: str, meta: dict[str, Any]) -> tuple[bool, int | None]:
    """Start a model on remote server. Local mode not supported (use direct subprocess)."""
    if not REMOTE_MODE:
        raise RuntimeError("start_model() is only available in remote mode")
    resp = _remote_post("/api/models/load", meta)
    if resp and resp.get("status") == "started":
        return True, resp.get("pid")
    return False, None

# Optional helper for chat routing
def get_remote_model_host() -> str:
    """Get hostname (no port) for remote model endpoints."""
    if not REMOTE_URL:
        raise RuntimeError("Not in remote mode")
    from urllib.parse import urlparse
    parsed = urlparse(REMOTE_URL)
    return parsed.hostname or "127.0.0.1"
```

Implementation complete except remote server.
