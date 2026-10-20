"""Status and health check endpoints."""
import time
from pathlib import Path
from flask import Blueprint, render_template, jsonify, g, Response
from typing import Generator

from config import BASE_DIR, WINDOWS, WSL, LLAMA_SERVER, DEFAULTS, FLASK_PORT, Config, resolve_model_path
from models import get_models, ModelRegistry
from processes import check_port_active, find_pid_by_port
from routes.system import get_hardware_info
from state import (
    _recent_stops,
    _recent_stops_lock,
    _wsl_pids,
    _wsl_pids_lock,
    _start_timestamps,
    _start_timestamps_lock,
)
from downloads import download_status, download_lock
from metrics import _fetch_metrics_from_model
from utils import estimate_gguf_size

# Global model registry instance for status checks
_status_registry = None


def get_status_registry() -> ModelRegistry:
    """Get or create the model registry instance for status checks."""
    global _status_registry
    if _status_registry is None:
        _status_registry = ModelRegistry(Config())
    return _status_registry


def inject_registry_to_context():
    """Middleware to inject ModelRegistry into request context."""
    if 'registry' not in g:
        g.registry = get_status_registry()


def validate_remote_connectivity():
    """Before request hook to validate remote server connectivity if in remote mode."""
    registry = g.get('registry')
    if registry and registry.is_remote_mode:
        try:
            registry._remote_client.get_status()
            g.remote_healthy = True
        except Exception as e:
            log = __import__('logging').getLogger("llamaweb")
            log.warning(f"Remote server connectivity check failed: {e}")
            g.remote_healthy = False
    else:
        g.remote_healthy = True  # N/A in local mode


status_bp = Blueprint("status", __name__)

# Register before_request handlers
status_bp.before_request(inject_registry_to_context)
status_bp.before_request(validate_remote_connectivity)


def _status_for_model_remote(model: str, meta: dict, registry: ModelRegistry) -> dict:
    """Compute model status in remote mode using the API."""
    port = int(meta.get("port", 11000))
    
    # Get model status from remote API
    try:
        remote_model = registry._remote_client.get_model(model)
    except Exception as e:
        log = __import__('logging').getLogger("llamaweb")
        log.warning(f"Failed to get remote model status for {model}: {e}")
        # Remote server unreachable - mark as unknown status
        return {
            "model": model,
            "port": port,
            "status": "unknown",
            "pid": None,
            "file_exists": True,
            "file_size_gb": meta.get("file_size_gb"),
            "model_path": meta.get("model_path", ""),
            "download_status": None,
            "partial_download": 0,
            "pp": None,
            "tg": None,
            "pg": None,
            **{k: v for k, v in meta.items() if k not in ["file_size_gb", "file_exists"]},
        }
    
    status = "unknown"
    pid = None
    
    if remote_model:
        status = "running"
        port = remote_model.port
    else:
        status = "stopped"
    
    # File size info is not available from remote API
    size_gb = meta.get("file_size_gb")
    
    # Download status
    dl_status = None
    with download_lock:
        dl_status = download_status.get(model)
    
    return {
        "model": model,
        "port": port,
        "status": status,
        "pid": pid,
        "file_exists": True,  # Assume exists in remote mode
        "file_size_gb": size_gb,
        "model_path": meta.get("model_path", ""),
        "download_status": dl_status,
        "partial_download": 0,
        "pp": None,
        "tg": None,
        "pg": None,
        **{k: v for k, v in meta.items() if k not in ["file_size_gb", "file_exists"]},
    }


def _status_for_model(model: str, meta: dict, registry: ModelRegistry | None = None) -> dict:
    """Compute comprehensive model status."""
    if registry is None:
        registry = g.get('registry') or get_status_registry()
    
    # In remote mode, delegate to remote API
    if registry.is_remote_mode:
        return _status_for_model_remote(model, meta, registry)
    
    # Local mode: use local process management
    port = int(meta.get("port", 11000))
    model_path_str = meta.get("model_path", "")
    # Handle both relative (to BASE_DIR) and absolute paths using helper (respects MODEL_DIR env var)
    model_path = resolve_model_path(model_path_str) if model_path_str else None
    exists = model_path.is_file() if model_path else False
    active = check_port_active(port)
    pid = None
    status = "stopped"

    if WSL:
        with _wsl_pids_lock:
            stored_pid_debug = _wsl_pids.get(model)
        # Logging intentionally omitted for brevity

    # Check if model was recently stopped (within last 10 seconds)
    with _recent_stops_lock:
        recent_stop_time = _recent_stops.get(model, 0)
        if recent_stop_time and time.time() - recent_stop_time < 10:
            status = "stopped"
        else:
            # First check stored PID for WSL (most reliable)
            if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
                with _wsl_pids_lock:
                    stored_pid = _wsl_pids.get(model)
                if stored_pid:
                    pid = stored_pid
                    status = "running"
            # For all modes, if port is active, try to find the PID
            if active:
                if not pid:
                    pid = find_pid_by_port(port)
                status = "running"
            # Check if model is starting
            with _start_timestamps_lock:
                if model in _start_timestamps:
                    if active:
                        # Verify readiness via health endpoint
                        from processes import _check_model_ready
                        if _check_model_ready(port):
                            status = "running"
                            _start_timestamps.pop(model, None)
                        else:
                            status = "starting"
                    else:
                        status = "starting"

    # File size if exists
    size_gb = meta.get("file_size_gb")
    if exists and size_gb is None and model_path:
        try:
            size_bytes = model_path.stat().st_size
            size_gb = round(size_bytes / (1024 ** 3), 2)
        except OSError:
            pass

    # Partial download detection
    partial_download = 0
    if model_path and model_path.exists():
        try:
            actual_size = model_path.stat().st_size
            expected_size = meta.get("expected_size", 0)
            if expected_size and actual_size > 0 and actual_size < expected_size * 0.9:
                partial_download = actual_size
        except OSError:
            pass

    # Download status
    dl_status = None
    with download_lock:
        dl_status = download_status.get(model)

    # Metrics
    metrics = {}
    if status.startswith("running"):
        metrics = _fetch_metrics_from_model(port)

    return {
        "model": model,
        "port": port,
        "status": status,
        "pid": pid,
        "file_exists": exists,
        "file_size_gb": size_gb,
        "model_path": model_path_str,
        "download_status": dl_status,
        "partial_download": partial_download,
        "pp": metrics.get("pp"),
        "tg": metrics.get("tg"),
        "pg": metrics.get("pg"),
        **{k: v for k, v in meta.items() if k not in ["file_size_gb", "file_exists"]},
    }


@status_bp.route("/")
def index():
    registry = g.get('registry') or get_status_registry()
    
    if registry.is_remote_mode:
        remote_models = registry.list_models()
        statuses = []
        for model in remote_models:
            statuses.append({
                "model": model.name,
                "port": model.port,
                "status": "running",
                "pid": None,
                "file_exists": True,
                "file_size_gb": model.file_size_gb,
                "model_path": model.model_path,
                "download_status": None,
                "partial_download": 0,
                "pp": None,
                "tg": None,
                "pg": None,
                "repo": model.repo,
                "filename": model.filename,
                "expected_size": model.expected_size,
                "threads": model.threads,
                "threads_batch": model.threads_batch,
                "timeout": model.timeout,
                "mlock": model.mlock,
                "flash_attn": model.flash_attn,
                "no_mmap": model.no_mmap,
                "parallel": model.parallel,
                "ubatch_size": model.ubatch_size,
                "rating_speed": model.rating_speed,
                "rating_coding": model.rating_coding,
                "comment": model.comment,
                "speculative_decoding_model": getattr(model, "speculative_decoding_model", False),
            })
    else:
        models = get_models()
        statuses = [_status_for_model(name, meta) for name, meta in models.items()]
    
    # Get hardware info for display
    hardware_info = get_hardware_info()
    
    return render_template('index.html', 
                           statuses=statuses, 
                           host=DEFAULTS["host"], 
                           flask_port=FLASK_PORT, 
                           cache_version="v2",
                           hardware_info=hardware_info)


@status_bp.route("/api/status")
def api_status():
    registry = g.get('registry') or get_status_registry()
    
    # In remote mode, fetch models from remote server
    if registry.is_remote_mode:
        remote_models = registry.list_models()
        results = []
        for model in remote_models:
            # Convert remote Model to status dict format
            results.append({
                "model": model.name,
                "port": model.port,
                "status": "running",
                "pid": None,
                "file_exists": True,
                "file_size_gb": model.file_size_gb,
                "model_path": model.model_path,
                "download_status": None,
                "partial_download": 0,
                "pp": None,
                "tg": None,
                "pg": None,
                "repo": model.repo,
                "filename": model.filename,
                "expected_size": model.expected_size,
                "threads": model.threads,
                "threads_batch": model.threads_batch,
                "timeout": model.timeout,
                "mlock": model.mlock,
                "flash_attn": model.flash_attn,
                "no_mmap": model.no_mmap,
                "parallel": model.parallel,
                "ubatch_size": model.ubatch_size,
                "rating_speed": model.rating_speed,
                "rating_coding": model.rating_coding,
                "comment": model.comment,
                "speculative_decoding_model": getattr(model, "speculative_decoding_model", False),
            })
    else:
        models = get_models()
        results = [_status_for_model(name, meta) for name, meta in models.items()]
    
    # Add mode and remote health info to response
    response_data = {
        "models": results,
        "mode": "remote" if registry.is_remote_mode else "local",
        "remote_healthy": g.get('remote_healthy', True),
    }
    
    response = jsonify(response_data)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@status_bp.route("/api/debug/<model_name>")
def api_debug(model_name):
    registry = g.get('registry') or get_status_registry()
    models = get_models()
    if model_name not in models:
        return jsonify({"error": f"Unknown model '{model_name}'"}), 404
    meta = models[model_name]
    port = int(meta["port"])
    
    # In remote mode, use remote API for status
    if registry.is_remote_mode:
        remote_model = registry._remote_client.get_model(model_name)
        return jsonify({
            "model": model_name,
            "port": remote_model.port if remote_model else port,
            "active": remote_model is not None,
            "WSL": WSL,
            "LLAMA_SERVER": str(LLAMA_SERVER) if LLAMA_SERVER else None,
            "stored_pid": None,
            "recent_stop": _recent_stops.get(model_name),
            "mode": "remote",
            "remote_healthy": g.get('remote_healthy', True),
        })
    
    # Local mode
    active = check_port_active(port)
    result = {
        "model": model_name,
        "port": port,
        "active": active,
        "WSL": WSL,
        "LLAMA_SERVER": str(LLAMA_SERVER) if LLAMA_SERVER else None,
        "stored_pid": _wsl_pids.get(model_name) if WSL else None,
        "recent_stop": _recent_stops.get(model_name),
        "mode": "local",
    }
    return jsonify(result)


@status_bp.route("/api/next-port")
def api_next_port():
    registry = g.get('registry') or get_status_registry()
    
    # In remote mode, we can't check local port availability
    if registry.is_remote_mode:
        # Return a placeholder port for remote mode
        return jsonify({"port": 0, "note": "Port assignment handled by remote server"})
    
    # Local mode: find next available port
    start = 11000
    port = start
    # Get ports already assigned to models
    models = get_models()
    assigned_ports = {int(m.get("port", 0)) for m in models.values() if m.get("port")}
    # Find next available port (not in use by running process AND not assigned to a model)
    while check_port_active(port) or port in assigned_ports:
        port += 1
    return jsonify({"port": port})
