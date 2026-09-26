# Global state and logging utilities
# This module holds shared runtime state and the log_event function.

import threading
import time
import json
import logging
import queue
from pathlib import Path
from collections import deque
from config import BASE_DIR, LOG_FILE

log = logging.getLogger("llamaweb")

# Track recent stop operations to avoid race conditions
_recent_stops: dict[str, float] = {}  # model_name -> timestamp
_recent_stops_lock = threading.Lock()

# Track PIDs for WSL models (since PowerShell can't see Windows process info from Linux)
_wsl_pids: dict[str, int] = {}  # model_name -> pid
_wsl_pids_lock = threading.Lock()

# Track models that are currently starting
_start_timestamps: dict[str, float] = {}
_start_timestamps_lock = threading.Lock()

# Log buffer for real-time streaming (SSE)
# Stores tuples of (timestamp, message, color)
_log_buffer: deque = deque(maxlen=500)
_log_buffer_lock = threading.Lock()

# SSE client connections for log streaming
_sse_clients: list = []
_sse_clients_lock = threading.Lock()


def log_event(action: str, model: str, port: int | str, pid: int | str | None, status: str) -> None:
    """Append a structured event to the log file and emit an INFO log."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = json.dumps({"time": ts, "action": action, "model": model, "port": port, "pid": pid, "status": status})
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(entry + "\n")
    # Note: The logger instance will be obtained from the caller's module; do not log here.


def add_log_entry(message: str, color: str = "inherit") -> None:
    """Add a log entry to the buffer and notify SSE clients."""
    ts = time.strftime("%H:%M:%S", time.localtime())
    entry = {"timestamp": ts, "message": message, "color": color}
    
    with _log_buffer_lock:
        _log_buffer.append(entry)
    
    # Notify SSE clients
    _notify_sse_clients(entry)


def _notify_sse_clients(entry: dict) -> None:
    """Send log entry to all connected SSE clients."""
    import json
    data = f"data: {json.dumps(entry)}\n\n"
    
    with _sse_clients_lock:
        # Remove closed connections
        active_clients = []
        for client_queue in _sse_clients:
            try:
                client_queue.put_nowait(data)
                active_clients.append(client_queue)
            except Exception:
                pass  # Client disconnected
        _sse_clients[:] = active_clients


def register_sse_client() -> "queue.Queue":
    """Register a new SSE client and return its queue."""
    client_queue = queue.Queue(maxsize=100)
    with _sse_clients_lock:
        _sse_clients.append(client_queue)
    return client_queue


def unregister_sse_client(client_queue: "queue.Queue") -> None:
    """Unregister an SSE client."""
    with _sse_clients_lock:
        if client_queue in _sse_clients:
            _sse_clients.remove(client_queue)


def get_recent_logs(limit: int = 100) -> list:
    """Get recent log entries from buffer."""
    with _log_buffer_lock:
        return list(_log_buffer)[-limit:]