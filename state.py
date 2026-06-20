# Global state and logging utilities
# This module holds shared runtime state and the log_event function.

import threading
import time
import json
import logging
from pathlib import Path
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


def log_event(action: str, model: str, port: int | str, pid: int | str | None, status: str) -> None:
    """Append a structured event to the log file and emit an INFO log."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = json.dumps({"time": ts, "action": action, "model": model, "port": port, "pid": pid, "status": status})
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(entry + "\n")
    # Note: The logger instance will be obtained from the caller's module; do not log here.
