"""Status and health check endpoints."""
import time
from flask import Blueprint, render_template, jsonify, Response
from typing import Generator

from config import BASE_DIR, WINDOWS, WSL, LLAMA_SERVER, DEFAULTS, FLASK_PORT
from models import get_models
from processes import check_port_active, find_pid_by_port
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



def _status_for_model(model: str, meta: dict) -> dict:
    """Compute comprehensive model status."""
    port = int(meta.get("port", 11000))
    model_path_str = meta.get("model_path", "")
    model_path = BASE_DIR / model_path_str if model_path_str else None
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
                elif active:
                    status = "running"
            elif active:
                pid = find_pid_by_port(port)
                if pid:
                    status = "running"
                else:
                    status = "running (unknown pid)"
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


status_bp = Blueprint("status", __name__)

@status_bp.route("/")
def index():
    models = get_models()
    statuses = [_status_for_model(name, meta) for name, meta in models.items()]
    return render_template('index.html', statuses=statuses, host=DEFAULTS["host"], flask_port=FLASK_PORT, cache_version="v2")

@status_bp.route("/api/status")
def api_status():
    models = get_models()
    results = [_status_for_model(name, meta) for name, meta in models.items()]
    response = jsonify(results)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@status_bp.route("/api/debug/<model_name>")
def api_debug(model_name):
    models = get_models()
    if model_name not in models:
        return jsonify({"error": f"Unknown model '{model_name}'"}), 404
    meta = models[model_name]
    port = int(meta["port"])
    active = check_port_active(port)
    result = {
        "model": model_name,
        "port": port,
        "active": active,
        "WSL": WSL,
        "LLAMA_SERVER": str(LLAMA_SERVER) if LLAMA_SERVER else None,
        "stored_pid": _wsl_pids.get(model_name) if WSL else None,
        "recent_stop": _recent_stops.get(model_name),
    }
    return jsonify(result)

@status_bp.route("/api/next-port")
def api_next_port():
    start = 11000
    port = start
    # Get ports already assigned to models
    models = get_models()
    assigned_ports = {int(m.get("port", 0)) for m in models.values() if m.get("port")}
    # Find next available port (not in use by running process AND not assigned to a model)
    while check_port_active(port) or port in assigned_ports:
        port += 1
    return jsonify({"port": port})
