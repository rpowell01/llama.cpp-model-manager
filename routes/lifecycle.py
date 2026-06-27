"""Lifecycle management: start and stop models."""
import logging
import time
from flask import Blueprint, jsonify, request, g
import subprocess
import os

from config import BASE_DIR, DEFAULTS, WINDOWS, WSL, LLAMA_SERVER, convert_path_for_binary, Config
from models import ModelRegistry, InvalidModeError
from processes import (
    find_pid_by_port,
    _kill_tree,
    check_port_active,
    _check_model_ready,
    build_cmd,
    pids_of_llama,
)
from state import (
    _recent_stops,
    _recent_stops_lock,
    _wsl_pids,
    _wsl_pids_lock,
    _start_timestamps,
    _start_timestamps_lock,
    log_event,
)

log = logging.getLogger("llamaweb")

# Global model registry instance
_model_registry = None


def get_model_registry() -> ModelRegistry:
    """Get or create the model registry instance."""
    global _model_registry
    if _model_registry is None:
        _model_registry = ModelRegistry(Config())
    return _model_registry


def inject_registry_to_context():
    """Middleware to inject ModelRegistry into request context."""
    g.registry = get_model_registry()


def validate_remote_connectivity():
    """Before request hook to validate remote server connectivity if in remote mode."""
    registry = getattr(g, 'registry', None)
    if registry and registry.is_remote_mode:
        try:
            registry._remote_client.get_status()
        except Exception as e:
            log.warning(f"Remote server connectivity check failed: {e}")
            return jsonify({"error": "Remote server unreachable"}), 503


lifecycle_bp = Blueprint("lifecycle", __name__)

# Register before_request handlers
lifecycle_bp.before_request(inject_registry_to_context)
lifecycle_bp.before_request(validate_remote_connectivity)


@lifecycle_bp.route("/api/start", methods=["POST"])
def api_start():
    """Start a model. In remote mode, delegates to remote API with 202 Accepted."""
    registry = g.registry
    
    # Check if we're in remote mode
    if registry.is_remote_mode:
        data = request.get_json(force=True) or {}
        model = data.get("model", "").strip()
        if not model:
            return jsonify({"error": "Missing 'model'"}), 400
        # In remote mode, models are pre-loaded on the remote server
        # We just verify connectivity and return 202 Accepted
        try:
            registry._remote_client.get_status()
            # Return 202 Accepted immediately for async operation in remote mode
            return jsonify({"status": "loading", "model": model}), 202
        except Exception as e:
            log.exception("Failed to connect to remote server")
            return jsonify({"error": "Remote server unreachable"}), 503
    
    # Local mode: use process management
    start_success = False
    model = None
    proc = None
    try:
        data = request.get_json(force=True) or {}
        model = data.get("model", "").strip()
        if not model:
            return jsonify({"error": "Missing 'model'"}), 400
        
        # Get models from registry (local mode uses local cache)
        models = registry.list_models()
        model_dict = None
        for m in models:
            if m.name == model:
                model_dict = m
                break
        
        if not model_dict:
            return jsonify({"error": f"Unknown model '{model}'"}), 404
        
        port = int(model_dict.port)
        meta = {
            "port": model_dict.port,
            "model_path": model_dict.model_path if hasattr(model_dict, 'model_path') else "",
            "threads": model_dict.threads,
            "threads_batch": model_dict.threads_batch,
            "cache_size": model_dict.cache_size,
            "timeout": model_dict.timeout,
            "mlock": model_dict.mlock,
            "flash_attn": model_dict.flash_attn,
            "no_mmap": model_dict.no_mmap,
            "parallel": model_dict.parallel,
            "ubatch_size": model_dict.ubatch_size,
        }

        # Check if already running
        active = check_port_active(port)
        if active:
            if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
                pid = _wsl_pids.get(model)
                if pid:
                    return jsonify({"status": "running", "pid": pid, "port": port})
            else:
                pid = find_pid_by_port(port)
                if pid:
                    return jsonify({"status": "running", "pid": pid, "port": port})
            # Port active but no PID found? Treat as running.
            return jsonify({"status": "running", "pid": None, "port": port})

        # Mark startup timestamp
        with _start_timestamps_lock:
            _start_timestamps[model] = time.time()

        # Build command
        cmd = build_cmd(model, port, meta)
        env = os.environ.copy()

        # Launch process
        log.info(f"Starting model {model}: {' '.join(cmd)}")
        try:
            if WINDOWS or (WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe')):
                proc = subprocess.Popen(
                    cmd,
                    env=env,
                    cwd=BASE_DIR,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0,
                )
                pid = proc.pid
                if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
                    with _wsl_pids_lock:
                        _wsl_pids[model] = pid
            else:
                proc = subprocess.Popen(
                    cmd,
                    env=env,
                    cwd=BASE_DIR,
                    start_new_session=True,
                )
                pid = proc.pid
        except Exception as e:
            with _start_timestamps_lock:
                _start_timestamps.pop(model, None)
            log.exception("Failed to start model %s", model)
            return jsonify({"error": "Failed to start server"}), 500

        if not pid:
            with _start_timestamps_lock:
                _start_timestamps.pop(model, None)
            return jsonify({"error": "Failed to start server: could not get PID"}), 500

        # Wait briefly for the process to start
        time.sleep(5)

        # Check if process is still running and port is active
        # We check port activity for all platforms to ensure the server is actually listening
        is_running = False
        for _ in range(20):
            if proc and proc.poll() is None:
                # Process is running, now check if port is active
                if check_port_active(port):
                    is_running = True
                    break
            time.sleep(0.5)

        if is_running:
            start_success = True
            with _recent_stops_lock:
                _recent_stops.pop(model, None)
            # On WSL with Windows binary, the stored PID is a Linux subprocess PID,
            # but we need the actual Windows process PID for killing.
            # Look up the Windows PID from the port and update the stored PID.
            if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
                windows_pid = find_pid_by_port(port)
                if windows_pid:
                    log.info(f"Updating WSL PID for {model}: Linux PID={pid} -> Windows PID={windows_pid}")
                    with _wsl_pids_lock:
                        _wsl_pids[model] = windows_pid
                    pid = windows_pid
            log.info(f"Start successful: model={model}, pid={pid}, port={port}, is_running={is_running}")
            log_event("start", model, port, pid, "running")
            return jsonify({"status": "running", "pid": pid, "port": port})
        else:
            log_event("start", model, port, pid, "exited_early")
            return jsonify({"error": "Server exited immediately", "status": "exited"}), 500
    except Exception as e:
        log.exception("Failed to start server for %s", model)
        return jsonify({"error": f"Failed to start server: {str(e)}"}), 500
    finally:
        if not start_success:
            with _start_timestamps_lock:
                _start_timestamps.pop(model, None)


@lifecycle_bp.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop a model. In remote mode, models are pre-loaded and cannot be stopped."""
    registry = g.registry
    
    # Check if we're in remote mode
    if registry.is_remote_mode:
        data = request.get_json(force=True) or {}
        model = data.get("model", "").strip()
        if not model:
            return jsonify({"error": "Missing 'model'"}), 400
        # In remote mode, models are pre-loaded and cannot be stopped via API
        # The remote server manages its own model lifecycle
        return jsonify({"status": "stopped", "model": model})
    
    # Local mode: use process management
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing 'model'"}), 400
    models = registry.list_models()
    model_dict = None
    for m in models:
        if m.name == model:
            model_dict = m
            break
    if not model_dict:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    port = int(model_dict.port)

    # On WSL with Windows binary, we need the Windows PID, not the Linux subprocess PID.
    # Always use find_pid_by_port first, then fallback to stored PID as last resort.
    pid = None
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        # First try to find the Windows PID from the port
        pid = find_pid_by_port(port)
        if pid:
            log.info(f"Stop request for {model}: found Windows PID {pid} from port {port} (WSL mode)")
        else:
            # Fallback: try stored PID (might be Linux PID, which won't work)
            with _wsl_pids_lock:
                stored_pid = _wsl_pids.get(model)
            if stored_pid:
                log.info(f"Stop request for {model}: stored PID={stored_pid} (may be Linux PID, will attempt kill)")
                pid = stored_pid
            log.info(f"Stop request for {model}: port={port}, pid={pid}")
    else:
        pid = find_pid_by_port(port)
        log.info(f"Stop request for {model}: port={port}, pid={pid}")
        if not pid:
            pids = pids_of_llama()
            log.info(f"No PID from port, found llama-server PIDs: {pids}")
            if pids:
                pid = pids[0]

    if not pid:
        log.info(f"No PID found for {model}, returning stopped")
        with _recent_stops_lock:
            _recent_stops[model] = time.time()
        with _start_timestamps_lock:
            _start_timestamps.pop(model, None)
        return jsonify({"status": "stopped"})

    success = _kill_tree(pid, model)
    log.info(f"Kill result for {model} (pid={pid}): success={success}")

    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        with _wsl_pids_lock:
            old_pid = _wsl_pids.pop(model, None)
        log.info(f"Cleared stored PID for WSL model {model}: old_pid={old_pid}")
    else:
        old_pid = None

    with _recent_stops_lock:
        _recent_stops[model] = time.time()

    # Wait for port to be released
    for _ in range(30):
        time.sleep(0.1)
        if not check_port_active(port):
            break

    if success:
        log_event("stop", model, port, pid, "stopped")
    else:
        log_event("stop", model, port, pid, "kill_failed")

    with _start_timestamps_lock:
        _start_timestamps.pop(model, None)

    return jsonify({"status": "stopped", "pid": pid, "success": success})
