"""Status and health check endpoints."""
import time
import requests
import logging
from pathlib import Path
from flask import Blueprint, render_template, jsonify, g, Response
from typing import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed

import webapp.config as config
from webapp.config import BASE_DIR, WINDOWS, WSL, DEFAULTS, FLASK_PORT, Config, convert_path_for_binary, resolve_model_path
from webapp.models import get_models, ModelRegistry
from webapp.processes import check_port_active, find_pid_by_port, _check_model_ready
from webapp.routes.system import get_hardware_info
from webapp.state import (
    _recent_stops,
    _recent_stops_lock,
    _wsl_pids,
    _wsl_pids_lock,
    _start_timestamps,
    _start_timestamps_lock,
)
from webapp.downloads import download_status, download_lock
from webapp.metrics import _fetch_metrics_from_model
from webapp.utils import estimate_gguf_size
from webapp.mnemonic_proxy import list_proxy_managers, get_proxy_manager

log = logging.getLogger("llamaweb")

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

# Thread pool for parallel model status checks
_status_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="model-status-")

# File existence cache with TTL
_file_exists_cache = {}
_FILE_EXISTS_TTL = 10  # seconds

# Mnemonic proxy health cache with TTL
_proxy_health_cache = {}
_PROXY_HEALTH_TTL = 5  # seconds

# Port listener cache with TTL (to avoid repeated netstat calls for same port)
_port_listener_cache = {}
_PORT_LISTENER_TTL = 5  # seconds


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
    
    # Mnemonic-proxy status (local proxy manager)
    proxy_status = {}
    proxy_manager = get_proxy_manager(model)
    if proxy_manager:
        proxy_status = proxy_manager.get_status()
    
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
        "mnemonic_proxy": proxy_status,
        **{k: v for k, v in meta.items() if k not in ["file_size_gb", "file_exists"]},
    }


def _get_cached_file_exists(model_path: Path) -> tuple[bool, float | None]:
    """Get cached file existence and size, or compute and cache."""
    global _file_exists_cache
    if model_path is None:
        return False, None
    
    path_str = str(model_path)
    now = time.time()
    
    if path_str in _file_exists_cache:
        cached = _file_exists_cache[path_str]
        if now - cached["timestamp"] < _FILE_EXISTS_TTL:
            return cached["exists"], cached["size_gb"]
    
    # Compute fresh
    exists = model_path.is_file()
    size_gb = None
    if exists:
        try:
            size_bytes = model_path.stat().st_size
            size_gb = round(size_bytes / (1024 ** 3), 2)
        except OSError:
            pass
    
    _file_exists_cache[path_str] = {"exists": exists, "size_gb": size_gb, "timestamp": now}
    return exists, size_gb


def _probe_proxy_health(proxy_port: int) -> dict:
    """Probe the mnemonic-proxy service directly.

    This deliberately does not depend on the in-process proxy manager. A
    service that is listening and healthy must be reported as running even if
    the Flask process was restarted or the manager's tracked PID is stale.

    The journal endpoint is used as the liveness check because it is served by
    the proxy itself. ``/health`` also waits for the upstream llama-server and
    can time out while the proxy service itself is still serving requests.
    """
    status = {"running": False, "message": "mnemonic-proxy not running"}
    try:
        resp = requests.get(
            f"http://127.0.0.1:{proxy_port}/v1/strata/journal",
            timeout=2.0,
            params={"limit": 1},
        )
        if resp.status_code == 200:
            status["running"] = True
            status["message"] = "mnemonic-proxy healthy"
        else:
            status["message"] = f"mnemonic-proxy journal returned HTTP {resp.status_code}"
            return status
    except requests.exceptions.Timeout:
        status["message"] = "mnemonic-proxy health check timed out"
        return status
    except requests.exceptions.ConnectionError:
        status["message"] = "mnemonic-proxy not running"
        return status
    except Exception as exc:
        status["message"] = f"mnemonic-proxy health check failed: {exc}"
        return status

    # Report upstream health as supplementary detail. Do not use a timeout
    # here to mark the proxy itself as stopped.
    try:
        health_resp = requests.get(f"http://127.0.0.1:{proxy_port}/health", timeout=1.0)
        if health_resp.status_code == 200:
            health = health_resp.json()
            status["health"] = health
            if health.get("status") != "ok":
                status["message"] = f"mnemonic-proxy running; engine status: {health.get('status', 'unknown')}"
    except Exception:
        pass

    return status


def _probe_mcp_health(mcp_port: int) -> dict:
    """Probe the mnemonic-mcp server using an MCP initialize request."""
    status = {"running": False, "message": "mnemonic-mcp not running"}
    try:
        resp = requests.post(
            f"http://127.0.0.1:{mcp_port}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "status-check", "version": "1.0"},
                },
            },
            timeout=2.0,
        )
        if resp.status_code == 200:
            status["running"] = True
            status["message"] = "mnemonic-mcp healthy"
        else:
            status["message"] = f"mnemonic-mcp initialize returned HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        status["message"] = "mnemonic-mcp health check timed out"
    except requests.exceptions.ConnectionError:
        status["message"] = "mnemonic-mcp not running"
    except Exception as exc:
        status["message"] = f"mnemonic-mcp health check failed: {exc}"
    return status


def _get_cached_proxy_health(model: str, proxy_port: int) -> dict:
    """Get cached proxy health status, or compute and cache."""
    global _proxy_health_cache
    cache_key = f"{model}:{proxy_port}"
    now = time.time()

    if cache_key in _proxy_health_cache:
        cached = _proxy_health_cache[cache_key]
        if now - cached["timestamp"] < _PROXY_HEALTH_TTL:
            return cached["status"]

    status = _probe_proxy_health(proxy_port)
    _proxy_health_cache[cache_key] = {"status": status, "timestamp": now}
    return status


def _get_cached_port_listener(port: int, platform: str) -> bool:
    """Get cached port listener status, or compute and cache."""
    global _port_listener_cache
    cache_key = f"{port}:{platform}"
    now = time.time()
    
    if cache_key in _port_listener_cache:
        cached = _port_listener_cache[cache_key]
        if now - cached["timestamp"] < _PORT_LISTENER_TTL:
            return cached["has_listener"]
    
    # Compute fresh - import here to avoid circular imports
    from webapp.processes import _port_has_listener
    has_listener = _port_has_listener(port, platform)
    
    _port_listener_cache[cache_key] = {"has_listener": has_listener, "timestamp": now}
    return has_listener


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
    
    # Use cached file existence check
    exists, cached_size_gb = _get_cached_file_exists(model_path)
    
    status = "stopped"
    pid = None

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
            # Use model's platform/backend settings instead of global config
            llama_cpp_platform = meta.get("llama_cpp_platform", "auto")
            llama_cpp_backend = meta.get("llama_cpp_backend", "auto")
            is_wsl_windows_binary = WSL and llama_cpp_platform == "windows"
            
            if is_wsl_windows_binary:
                with _wsl_pids_lock:
                    stored_pid = _wsl_pids.get(model)
                if stored_pid:
                    pid = stored_pid
                    status = "running"
            
            # Use cached port listener check first (avoids repeated netstat calls)
            port_has_listener = _get_cached_port_listener(port, llama_cpp_platform)
            
            if port_has_listener:
                # Port has a listener - check if the specific model is ready
                active = _check_model_ready(port, model)
                if active:
                    if not pid:
                        pid = find_pid_by_port(port, llama_cpp_platform)
                    status = "running"
            # Check if model is starting
            with _start_timestamps_lock:
                if model in _start_timestamps:
                    if active:
                        # Verify readiness via health endpoint (already checked above)
                        status = "running"
                        _start_timestamps.pop(model, None)
                    else:
                        status = "starting"

    # File size if exists - use cached size if available
    size_gb = meta.get("file_size_gb")
    if exists and size_gb is None:
        size_gb = cached_size_gb

    # Partial download detection
    partial_download = 0
    if exists and model_path:
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

    # Mnemonic-proxy status
    # Start with config from model meta, then overlay runtime status from proxy manager
    base_proxy_config = meta.get("mnemonic_proxy", {})
    proxy_status = {
        "enabled": base_proxy_config.get("enabled", False),
        "proxy_port": base_proxy_config.get("port", 8096),
        "data_dir": base_proxy_config.get("data_dir", ""),
        "slot_dir": base_proxy_config.get("slot_dir", ""),
        "tokenizer_path": base_proxy_config.get("tokenizer_path", ""),
        "config_override": base_proxy_config.get("config_override", {}),
        "mcp_enabled": base_proxy_config.get("mcp_enabled", False),
        "mcp_port": base_proxy_config.get("mcp_port", 8133) if base_proxy_config.get("mcp_enabled") else None,
        "mcp": {"enabled": False, "running": False, "port": None, "pid": None, "uptime_seconds": 0},
        "model_name": model,
    }
    
    proxy_manager = get_proxy_manager(model)
    if proxy_manager:
        runtime_status = proxy_manager.get_status()
        # Overlay runtime values, preserving config from meta
        runtime_status.pop("model_name", None)
        runtime_status.pop("proxy_port", None)
        # Use manager PID/uptime when available, but determine running state from
        # the service's own health endpoint. The manager's tracked PID can be
        # stale (for example after a Flask restart or an unclean proxy exit),
        # while the actual service remains healthy on its port.
        manager_running = bool(runtime_status.get("running"))
        proxy_port = int(base_proxy_config.get("port", 8096) or 8096)
        health = _get_cached_proxy_health(model, proxy_port)
        proxy_status["running"] = bool(health.get("running"))
        proxy_status["message"] = health.get("message", "mnemonic-proxy not running")
        proxy_status["pid"] = runtime_status.get("pid") if manager_running else None
        proxy_status["uptime_seconds"] = runtime_status.get("uptime_seconds", 0) if manager_running else 0
        proxy_status["llama_port"] = runtime_status.get("llama_port", 9931)

        # Probe MCP directly as well; do not rely solely on manager state.
        mcp_enabled = bool(base_proxy_config.get("mcp_enabled", False))
        mcp_port = int(base_proxy_config.get("mcp_port", 8133) or 8133)
        runtime_mcp = runtime_status.get("mcp", {}) or {}
        if mcp_enabled:
            mcp_health = _probe_mcp_health(mcp_port)
            proxy_status["mcp"] = {
                "enabled": True,
                "running": bool(mcp_health.get("running")),
                "port": mcp_port,
                "pid": runtime_mcp.get("pid") if mcp_health.get("running") else None,
                "uptime_seconds": runtime_mcp.get("uptime_seconds", 0) if mcp_health.get("running") else 0,
                "message": mcp_health.get("message", "mnemonic-mcp not running"),
            }
        else:
            proxy_status["mcp"] = {
                "enabled": False,
                "running": False,
                "port": None,
                "pid": None,
                "uptime_seconds": 0,
                "message": "mnemonic-mcp not enabled",
            }
    else:
        # No proxy manager (e.g., after Flask restart). Probe the services
        # directly so the API always reports their actual health.
        mnemonic_proxy_enabled = base_proxy_config.get("enabled", False)
        if mnemonic_proxy_enabled:
            proxy_port = int(base_proxy_config.get("port", 8096) or 8096)
            cached_health = _get_cached_proxy_health(model, proxy_port)
            proxy_status["running"] = cached_health.get("running", False)
            proxy_status["message"] = cached_health.get("message", "mnemonic-proxy not running")

            mcp_enabled = bool(base_proxy_config.get("mcp_enabled", False))
            mcp_port = int(base_proxy_config.get("mcp_port", 8133) or 8133)
            if mcp_enabled:
                mcp_health = _probe_mcp_health(mcp_port)
                proxy_status["mcp"] = {
                    "enabled": True,
                    "running": bool(mcp_health.get("running")),
                    "port": mcp_port,
                    "pid": None,
                    "uptime_seconds": 0,
                    "message": mcp_health.get("message", "mnemonic-mcp not running"),
                }

    # Build result dict, excluding mnemonic_proxy from meta spread so our proxy status isn't overwritten
    # Also exclude "model" to prevent meta from overwriting the explicit model name
    extra_meta = {k: v for k, v in meta.items() if k not in ["file_size_gb", "file_exists", "mnemonic_proxy", "model"]}
    return {
        "model": model,
        "port": port,
        "status": status,
        "pid": pid,
        "file_exists": exists,
        "file_size_gb": size_gb,
        "model_path": model_path_str,
        "model_path_for_binary": convert_path_for_binary(model_path) if model_path else "",
        "download_status": dl_status,
        "partial_download": partial_download,
        "pp": metrics.get("pp"),
        "tg": metrics.get("tg"),
        "pg": metrics.get("pg"),
        "mnemonic_proxy": proxy_status,
        **extra_meta,
    }


def _get_model_statuses_parallel(models: dict, registry: ModelRegistry) -> list:
    """Get statuses for all models in parallel."""
    futures = {}
    for name, meta in models.items():
        future = _status_executor.submit(_status_for_model, name, meta, registry)
        futures[future] = name
    
    results = []
    for future in as_completed(futures):
        try:
            result = future.result(timeout=10)  # 10 second timeout per model
            results.append(result)
        except Exception as e:
            model_name = futures[future]
            log = __import__('logging').getLogger("llamaweb")
            log.warning(f"Failed to get status for model {model_name}: {e}")
            # Return minimal status on error
            results.append({
                "model": model_name,
                "port": models[model_name].get("port", 0),
                "status": "error",
                "error": str(e),
            })
    
    # Sort results by model name for consistent ordering
    results.sort(key=lambda x: x.get("model", "").lower())
    return results


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
                "ctx_size": model.ctx_size,
                "yarn_orig_ctx": model.yarn_orig_ctx,
                "rating_speed": model.rating_speed,
                "rating_coding": model.rating_coding,
                "comment": model.comment,
                "speculative_decoding_model": getattr(model, "speculative_decoding_model", False),
            })
    else:
        models = get_models()
        statuses = _get_model_statuses_parallel(models, registry)
    
    # Get hardware info for display (cached)
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
            # Get local proxy status for this model
            proxy_status = {}
            proxy_manager = get_proxy_manager(model.name)
            if proxy_manager:
                proxy_status = proxy_manager.get_status()
            
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
                "mnemonic_proxy": proxy_status,
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
                "ctx_size": model.ctx_size,
                "yarn_orig_ctx": model.yarn_orig_ctx,
                "rating_speed": model.rating_speed,
                "rating_coding": model.rating_coding,
                "comment": model.comment,
                "speculative_decoding_model": getattr(model, "speculative_decoding_model", False),
            })
    else:
        models = get_models()
        results = _get_model_statuses_parallel(models, registry)
    
    # Validate that each model row has unique identifying data (regression check for web-47ob)
    if results and not registry.is_remote_mode:
        _validate_status_uniqueness(results)
    
    # Add mode and remote health info to response
    from webapp.config import PREFER_LINUX_BINARY
    response_data = {
        "models": results,
        "mode": "remote" if registry.is_remote_mode else "local",
        "remote_healthy": g.get('remote_healthy', True),
        "prefer_linux_binary": PREFER_LINUX_BINARY,
        "wsl": WSL,
    }
    
    response = jsonify(response_data)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def _validate_status_uniqueness(results: list) -> None:
    """Validate that each model row has unique identifying data.
    
    This is a regression check for bug web-47ob where all model rows
    were serialized with identical port/pid/proxy state due to loop-carried
    variables in the serializer.
    
    Logs a warning if duplicates are detected but does not fail the request.
    """
    if not results:
        return
    
    # Track seen values for each field
    seen_pids = {}
    seen_ports = {}
    seen_proxy_pids = {}
    seen_model_names = {}
    
    for idx, row in enumerate(results):
        model_name = row.get("model")
        pid = row.get("pid")
        port = row.get("port")
        proxy = row.get("mnemonic_proxy", {})
        proxy_pid = proxy.get("pid") if isinstance(proxy, dict) else None
        
        # Check model name uniqueness
        if model_name in seen_model_names:
            log.warning(
                f"Status serialization bug detected: duplicate model name '{model_name}' "
                f"at indices {seen_model_names[model_name]} and {idx}. "
                f"This indicates loop-carried variable bug in _status_for_model."
            )
        else:
            seen_model_names[model_name] = idx
        
        # Check pid uniqueness (only for running models)
        if pid is not None:
            if pid in seen_pids:
                log.warning(
                    f"Status serialization bug detected: duplicate pid {pid} "
                    f"for models '{seen_pids[pid]}' and '{model_name}' (indices {seen_pids[pid]} and {idx}). "
                    f"All models should have unique pids unless they genuinely share a process."
                )
            else:
                seen_pids[pid] = model_name
        
        # Check port uniqueness (only for running models with listeners)
        if port is not None and row.get("status") == "running":
            if port in seen_ports:
                log.warning(
                    f"Status serialization bug detected: duplicate port {port} "
                    f"for models '{seen_ports[port]}' and '{model_name}' (indices {seen_ports[port]} and {idx}). "
                    f"Each running model should have its own port."
                )
            else:
                seen_ports[port] = model_name
        
        # Check proxy pid uniqueness
        if proxy_pid is not None:
            if proxy_pid in seen_proxy_pids:
                log.warning(
                    f"Status serialization bug detected: duplicate proxy pid {proxy_pid} "
                    f"for models '{seen_proxy_pids[proxy_pid]}' and '{model_name}' (indices {seen_proxy_pids[proxy_pid]} and {idx}). "
                    f"Each mnemonic-proxy should have its own pid."
                )
            else:
                seen_proxy_pids[proxy_pid] = model_name


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
            "LLAMA_SERVER": str(config.LLAMA_SERVER) if config.LLAMA_SERVER else None,
            "stored_pid": None,
            "recent_stop": _recent_stops.get(model_name),
            "mode": "remote",
            "remote_healthy": g.get('remote_healthy', True),
        })
    
    # Local mode
    active = _check_model_ready(port, model_name)
    result = {
        "model": model_name,
        "port": port,
        "active": active,
        "WSL": WSL,
        "LLAMA_SERVER": str(config.LLAMA_SERVER) if config.LLAMA_SERVER else None,
        "prefer_linux_binary": __import__('config').PREFER_LINUX_BINARY,
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
