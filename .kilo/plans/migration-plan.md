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

**Verify:** Unit tests pass for both modes (mock remote server).

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
    ...
    
    # --- Process startup (conditional) ---
    if REMOTE_MODE:
        success, remote_pid = start_model(model, meta)
        if success:
            time.sleep(2.5)
            if check_port_active(port):
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
        # Local mode: existing subprocess.Popen block
        cmd = build_cmd(model, port, meta)
        ...
```

**Change 3:** No change to `_kill_tree` usage—remote_client already handles.

**Change 4:** Remove any direct usage of `subprocess` outside the conditional (already encapsulated).

---

### Step 3: Update `routes/status.py`

**Change 1:** Update imports
```python
from remote_client import check_port_active, find_pid_by_port, _check_model_ready
```

**Change 2:** `_status_for_model()` remains unchanged; uses remote_client functions.

**Change 3:** No other changes required.

---

### Step 4: Update `routes/chat.py`

**Change 1:** Update import
```python
from remote_client import check_port_active
```

**Change 2:** Add remote host handling
```python
from remote_client import get_remote_model_host, REMOTE_MODE

...

if REMOTE_MODE:
    host = get_remote_model_host()
    target_url = f"http://{host}:{port_int}/v1/chat/completions"
else:
    target_url = f"http://127.0.0.1:{port_int}/v1/chat/completions"
```

This assumes remote model ports are directly accessible on the remote host (same port number as assigned locally). If remote server uses a different port mapping, we'll need more info (future enhancement).

---

### Step 5: Configure Environment

Add to `.env` or deployment config:
```bash
# For remote mode:
REMOTE_MODE=1
LLAMA_CPP_SERVER_URL=http://<remote-host>:5001
# Optional:
REMOTE_TIMEOUT=30
REMOTE_RETRIES=3
# For chat to work, ensure remote host allows direct access to model ports (e.g., 11000+)
```

Ensure `remote_client` reads these at import time. Flask routes import `remote_client` early.

---

### Step 6: Remote Server Implementation (Separate Bead)

This migration plan assumes the remote server will be implemented as a separate Flask application (e.g., `remote_server.py`) that:
- Uses the same `processes.py`, `models.py`, `state.py` internally
- Exposes the REST API endpoints described
- Runs on a different host/port (e.g., 5001) and can launch `llama-server` locally
- Has access to the same model files (shared filesystem or pre-synced)

Not in scope for this bead, but design must account for it.

---

### Step 7: Disable/Guard Download Features (Optional)

If remote mode is common, consider disabling Hugging Face download routes when `REMOTE_MODE` because:
- Downloads would happen locally, but models need to be on remote server
- Could lead to confusion

In `routes/huggingface.py` and `routes/downloads.py`:
```python
from remote_client import REMOTE_MODE
if REMOTE_MODE:
    # Return 503 or redirect
```

Alternatively, leave as-is but warn user.

---

### Step 8: Testing

1. **Local mode**: `unset REMOTE_MODE` → verify all functions unchanged.
2. **Remote mode with mock server**:
   - Use `responses` library or Flask test client to mock `/api/*` endpoints
   - Test each function: `find_pid_by_port(11000)`, `check_port_active`, `start_model`, `_kill_tree`
   - Simulate failures: connection errors, timeouts, 5xx
3. **Integration**: Run a real remote_server on a different port; test start/stop/status/chat.

---

## Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Port mismatch local vs remote | Chat fails | Ensure models.json ports are synchronized; remote server should respect requested ports and return actual port used |
| Remote server unreachable | UI shows models as stopped, start fails | Clear error messages; retry logic in client |
| Latency | UI slower to update status | Use longer timeouts, async UI polling (already exists) |
| Metrics collection | Metrics fetch fails for remote ports | Modify `routes/metrics.py` to handle remote host (future bead) |
| Model files missing on remote | Start fails | Pre-sync models; provide sync script (future bead) |
| Circular imports | Startup crash | Careful module structure in `remote_client.py` |

---

## Rollback

If remote mode causes issues:
1. Unset `REMOTE_MODE` → immediate fallback to local mode (no code changes)
2. If code changes were made (imports swapped), revert to `processes` imports (straightforward).

---

## Future Enhancements (Out of Scope)

- **Bidirectional sync**: Remote server could push status updates via WebSocket to UI (instead of polling)
- **Multi-remote**: Support multiple remote servers (model distribution)
- **Authentication**: API keys, mutual TLS
- **Model registry sync**: Automatic provisioning of models.json to remote
- **Metrics aggregation**: Remote server collects Prometheus metrics from all models and exposes via `/api/metrics`
- **Chat proxy optimization**: Use HTTP/2 or long-lived connections
- **Caching**: Cache remote status responses for a few seconds to reduce load

---

## Acceptance Criteria for This Design Bead

- [x] Architecture diagram (current vs target)
- [x] Complete list of process management call sites with file:line references
- [x] `remote_client.py` interface spec (function signatures, error handling, retry)
- [x] REST API client design (endpoints, request/response formats, auth)
- [x] Migration plan with code changes
- [x] Environment variable contract
- [x] Validation via codebase review (no implementation)

---

## Notes for Implementation Beads

1. Bead `web-mol-az0` (parent epic) expects remote mode integration.
2. `remote_client.py` should be created in project root.
3. All modifications must preserve local mode behavior when `REMOTE_MODE` unset.
4. Use `log = logging.getLogger("llamaweb.remote_client")` for module logs.
5. Do not remove `processes.py`; it remains the authoritative local implementation.
6. Consider adding unit tests for remote_client in `tests/` directory (if exists).
