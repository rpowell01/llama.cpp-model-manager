# Process Management Call Sites

## Summary Table

| Function | Defined In | Called In | Total Call Sites |
|----------|------------|-----------|------------------|
| `find_pid_by_port` | processes.py:34 | lifecycle.py (3), status.py (2) | 5 |
| `pids_of_llama` | processes.py:141 | lifecycle.py (3) | 3 |
| `_kill_tree` | processes.py:190 | lifecycle.py (1) | 1 |
| `check_port_active` | processes.py:260 | lifecycle.py (4), status.py (4), chat.py (1) | 9 |
| `_check_model_ready` | processes.py:271 | lifecycle.py (1), status.py (2) | 3 |
| `build_cmd` | processes.py:282 | lifecycle.py (1) | 1 |

---

## Detailed Call Sites

### `find_pid_by_port(port)`

**File: `routes/lifecycle.py`**
- **Line 50**: Inside `api_start()`, after checking port active, try to get PID to determine if already running
  ```python
  pid = find_pid_by_port(port)
  if pid:
      return jsonify({"status": "running", "pid": pid, "port": port})
  ```
- **Line 161**: Inside `api_stop()`, find PID to stop
  ```python
  pid = find_pid_by_port(port)
  log.info(f"Stop request for {model}: port={port}, pid={pid}")
  ```
- **Line 164**: Fallback in `api_stop()` if no PID from port, search all llama-server PIDs
  ```python
  pids = pids_of_llama()
  ```

**File: `routes/status.py`**
- **Line 54**: In `_status_for_model()`, determine running status and include PID
  ```python
  pid = find_pid_by_port(port)
  if pid:
      status = "running"
  else:
      status = "running (unknown pid)"
  ```
- **Line 145**: In `api_debug()`, include PID in debug info
  ```python
  pid = find_pid_by_port(port)
  result = {"model": model_name, "port": port, "active": active, ... }
  ```

---

### `pids_of_llama()`

**File: `routes/lifecycle.py`**
- **Line 151**: In `api_stop()` for WSL mode where port→PID unreliable
  ```python
  pids = pids_of_llama()
  if pids:
      pid = pids[0]
  ```
- **Line 164**: In `api_stop()` fallback when port active but no PID found
  ```python
  if not pid and check_port_active(port):
      pids = pids_of_llama()
      if pids:
          pid = pids[0]
  ```

---

### `_kill_tree(pid, model)`

**File: `routes/lifecycle.py`**
- **Line 175**: In `api_stop()`, kill the process tree
  ```python
  success = _kill_tree(pid, model)
  log.info(f"Kill result for {model} (pid={pid}): success={success}")
  ```
- Also clears `_wsl_pids` and updates `_recent_stops` after this call.

---

### `check_port_active(port)`

**File: `routes/lifecycle.py`**
- **Line 42**: In `api_start()`, check if model already running on port
- **Line 108**: In `api_start()` WSL path, poll port activity after spawn
- **Line 163**: In `api_stop()`, detect if port still active after kill attempt
- **Line 189**: In `api_stop()`, wait loop to confirm port released

**File: `routes/status.py`**
- **Line 29**: In `_status_for_model()`, determine if port is active
- **Line 145**: In `api_debug()`, include `active` flag
- **Line 165**: In `api_next_port()`, find next free port (skip active ones)

**File: `routes/chat.py`**
- **Line 41**: In `api_chat()`, verify model is running before proxying request

---

### `_check_model_ready(port)`

**File: `routes/lifecycle.py`**
- **Line 65**: In `api_start()` WSL path, verify model responds on `/v1/models` endpoint
  ```python
  if _check_model_ready(port):
      status = "running"
  else:
      status = "starting"
  ```

**File: `routes/status.py`**
- **Line 65**: In `_status_for_model()`, if model is starting and port active, check readiness to transition to "running"
- **Line 145** (actually line 145-146 are in `api_debug`, but `_check_model_ready` is not used elsewhere in that file)

---

### `build_cmd(model, port, meta)`

**File: `routes/lifecycle.py`**
- **Line 62**: In `api_start()`, construct command list for `subprocess.Popen`
  ```python
  cmd = build_cmd(model, port, meta)
  log.info(...)
  proc = subprocess.Popen(cmd, ...)
  ```

---

### Other Process Management Patterns

**Direct subprocess usage:**
- `lifecycle.py` lines 69-89: `subprocess.Popen()` with platform-specific flags
- `lifecycle.py` lines 219-257: `_kill_tree()` implementation uses `os.killpg`, `taskkill`, or PowerShell

**State tracking (`state.py`):**
- `_recent_stops`, `_wsl_pids`, `_start_timestamps` are used in lifecycle and status to coordinate lifecycle state.

---

## Call Graph (Simplified)

```
api_start():
  check_port_active() → 
  build_cmd() →
  subprocess.Popen() →
  check_port_active() (poll) →
  _check_model_ready() (optional) →
  return status

api_stop():
  find_pid_by_port() →
  _kill_tree() →
  check_port_active() (wait loop)

api_chat():
  check_port_active() →
  proxy to localhost:port

_status_for_model():
  check_port_active() →
  find_pid_by_port() →
  _check_model_ready() (if starting) →
  compile status dict

api_debug():
  check_port_active() →
  find_pid_by_port()

api_next_port():
  check_port_active() (loop)

api_status():
  _status_for_model() for each model
```

---

## Migration Impact Simulator

If we replace these functions with remote_client wrappers:

| Replacement | Local Implementation | Remote Implementation |
|-------------|---------------------|----------------------|
| `find_pid_by_port` | scan OS processes | GET /api/status?port → returns pid field or 404 |
| `pids_of_llama` | scan OS processes | GET /api/status → aggregate all pids |
| `_kill_tree` | os.killpg/taskkill/PowerShell | POST /api/models/unload (by model name) |
| `check_port_active` | socket.connect to localhost | GET /api/status?port → model.running? |
| `_check_model_ready` | HTTP GET /v1/models on port | GET /api/models/<name>/ready |
| `build_cmd` | construct cmd list | **NOT USED** in remote mode (server handles) |

**Special handling for `start_model`:**
- New function not directly replacing anything. It combines:
  - Build command (conceptually, on server)
  - subprocess.Popen (server side)
  - Port validation (server assigns)
- Local code would call `remote_client.start_model(model, meta)` instead of `build_cmd()` + `subprocess.Popen()`.

---

## Notes on Remote Mode Refactoring

1. **Extract start logic from `lifecycle.py` into a helper** that can be called by both local and remote client:
   ```python
   def _start_model_local(model: str, meta: dict) -> tuple[bool, int | None]:
       # current api_start() start sequence (without Flask response)
   ```
   Then `remote_client` in local mode could call this helper, avoiding duplication.

2. **`state.py` tracking becomes irrelevant in remote mode** because:
   - `_recent_stops` is used to debounce status checks; remote server can manage its own state.
   - `_wsl_pids` not needed; remote server handles its PIDs.
   - `_start_timestamps` for startup tracking; remote server could report timestamps in status.
   - **Decision:** Keep state.py for local mode; remote_client functions should not touch it.

3. **`build_cmd` usage** is localized to one place (line 62). It's safe to remove from remote_client and have `api_start()` use conditional:
   ```python
   if REMOTE_MODE:
       success, pid = remote_client.start_model(model, meta)
   else:
       cmd = build_cmd(model, port, meta)
       ...
   ```
   Or we could have `remote_client.start_model()` in both modes that abstracts this.

4. **Error handling**: In remote mode, network errors should be caught and converted to appropriate Flask responses (500). Already the routes have try/except; remote_client should return distinguishable failure results.
