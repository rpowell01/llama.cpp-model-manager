"""Lifecycle management: start and stop models."""
import logging
import time
import threading
import signal
import psutil
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
    warmup_model,
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
    add_log_entry,
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


def _stop_model(model: str, port: int) -> tuple[bool, int | None]:
    """Stop a single model gracefully.
    
    Args:
        model: Model name
        port: Model port
        
    Returns:
        Tuple of (success, pid)
    """
    # Find PID
    pid = find_pid_by_port(port)
    if not pid:
        # Try WSL pids
        with _wsl_pids_lock:
            pid = _wsl_pids.get(model)
    
    if not pid:
        # No PID found, just clean up state
        with _recent_stops_lock:
            _recent_stops[model] = time.time()
        with _start_timestamps_lock:
            _start_timestamps.pop(model, None)
        if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
            with _wsl_pids_lock:
                _wsl_pids.pop(model, None)
        return True, None
    
    success = _kill_tree(pid, model)
    log.info(f"Kill result for {model} (pid={pid}): success={success}")
    
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        with _wsl_pids_lock:
            old_pid = _wsl_pids.pop(model, None)
        log.info(f"Cleared stored PID for WSL model {model}: old_pid={old_pid}")
    
    with _recent_stops_lock:
        _recent_stops[model] = time.time()
    
    # Wait for port to be released
    for _ in range(30):
        time.sleep(0.1)
        if not check_port_active(port):
            break
    
    with _start_timestamps_lock:
        _start_timestamps.pop(model, None)
    
    return success, pid


def _stop_all_models(registry) -> dict[str, bool]:
    """Stop all running models gracefully.
    
    Returns:
        Dict mapping model name to success status.
    """
    results = {}
    models = registry.list_models()
    
    for m in models:
        model = m.name
        port = int(m.port)
        
        success, _ = _stop_model(model, port)
        results[model] = success
    
    return results


def _kill_all_llama_processes() -> int:
    """Force kill all remaining llama-server processes.
    
    Returns:
        Number of processes killed.
    """
    killed = 0
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            name = proc.info.get('name', '')
            cmdline = proc.info.get('cmdline', [])
            if 'llama-server' in name or (cmdline and any('llama-server' in c for c in cmdline)):
                try:
                    proc.kill()
                    killed += 1
                    log.info(f"Force killed llama-server process: pid={proc.info['pid']}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return killed


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
        
        # Refresh model cache to pick up any recent changes before starting
        from models import refresh_models_cache
        refresh_models_cache()
        
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
            "timeout": model_dict.timeout,
            "mlock": model_dict.mlock,
            "flash_attn": model_dict.flash_attn,
            "no_mmap": model_dict.no_mmap,
            "parallel": model_dict.parallel,
            "ubatch_size": model_dict.ubatch_size,
            # Agent/Tool parameters
            "jinja": getattr(model_dict, 'jinja', True),
            "reasoning_preserve": getattr(model_dict, 'reasoning_preserve', True),
            # CPU-only optimizations
            "gpu_layers": getattr(model_dict, 'gpu_layers', 0),
            "cache_type_k": getattr(model_dict, 'cache_type_k', 'fp16'),
            "cache_type_v": getattr(model_dict, 'cache_type_v', 'fp16'),
            "numa": getattr(model_dict, 'numa', True),
            "numa_value": getattr(model_dict, 'numa_value', 'distribute'),
            "cpu_affinity": getattr(model_dict, 'cpu_affinity', 'auto'),
            "cpu_strict": getattr(model_dict, 'cpu_strict', False),
            "batch_size": getattr(model_dict, 'batch_size', 512),
            "ctx_size": getattr(model_dict, 'ctx_size', 4096),
            # YaRN RoPE scaling
            "rope_freq_base": getattr(model_dict, 'rope_freq_base', 10000.0),
            "rope_freq_scale": getattr(model_dict, 'rope_freq_scale', 1.0),
            "rope_scaling_type": getattr(model_dict, 'rope_scaling_type', 'none'),
            "rope_scale": getattr(model_dict, 'rope_scale', 1.0),
            "yarn_orig_ctx": getattr(model_dict, 'yarn_orig_ctx', 0),
            "yarn_ext_factor": getattr(model_dict, 'yarn_ext_factor', -1.0),
            "yarn_attn_factor": getattr(model_dict, 'yarn_attn_factor', 1.0),
            "yarn_beta_slow": getattr(model_dict, 'yarn_beta_slow', 1.0),
            "yarn_beta_fast": getattr(model_dict, 'yarn_beta_fast', 32.0),
            # Speculative decoding parameters
            "spec_type": getattr(model_dict, 'spec_type', 'none'),
            "spec_draft_model": getattr(model_dict, 'spec_draft_model', ''),
            "spec_draft_hf_repo": getattr(model_dict, 'spec_draft_hf_repo', ''),
            "spec_draft_n_max": getattr(model_dict, 'spec_draft_n_max', 3),
            "spec_draft_n_min": getattr(model_dict, 'spec_draft_n_min', 0),
            "spec_draft_p_split": getattr(model_dict, 'spec_draft_p_split', 0.10),
            "spec_draft_p_min": getattr(model_dict, 'spec_draft_p_min', 0.00),
            "spec_draft_ngl": getattr(model_dict, 'spec_draft_ngl', 'auto'),
            "spec_ngram_size_n": getattr(model_dict, 'spec_ngram_size_n', 12),
            "spec_ngram_size_m": getattr(model_dict, 'spec_ngram_size_m', 48),
            "spec_ngram_min_hits": getattr(model_dict, 'spec_ngram_min_hits', 1),
            # Warmup parameters
            "warmup_enabled": getattr(model_dict, 'warmup_enabled', False),
            "warmup_message": getattr(model_dict, 'warmup_message', 'Hello'),
            "warmup_max_tokens": getattr(model_dict, 'warmup_max_tokens', 10),
            # Slot KV Cache Persistence
            "slot_save_path": getattr(model_dict, 'slot_save_path', ''),
            "cache_reuse": getattr(model_dict, 'cache_reuse', 4096),
            "swa_full": getattr(model_dict, 'swa_full', False),
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
        cmd_str = ' '.join(cmd)
        log.info(f"Starting model {model}: {cmd_str}")
        add_log_entry(f"▶ Start: {model} on port {port}", "green")
        add_log_entry(f"Command: {cmd_str}", "cyan")
        try:
            # Capture stdout/stderr for real-time logging
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                text=True,
                bufsize=1,  # Line buffered
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0,
            )
            pid = proc.pid
            if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
                with _wsl_pids_lock:
                    _wsl_pids[model] = pid
            
            # Start a thread to read subprocess output and stream to log buffer
            def _stream_output():
                try:
                    for line in proc.stdout:
                        line = line.rstrip()
                        if line:
                            add_log_entry(f"[{model}] {line}", "inherit")
                except Exception:
                    pass  # Ignore errors when reading from closed pipe
            
            output_thread = threading.Thread(target=_stream_output, daemon=True)
            output_thread.start()
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

        # Check if process is still running and model is ready to serve requests
        # We check port activity AND health endpoint for all platforms to ensure
        # the server is actually listening and ready
        from processes import _check_model_ready
        is_running = False
        for _ in range(80):  # 40 seconds max wait for large models
            if proc and proc.poll() is None:
                # Process is running, now check if model is ready (port + health endpoint)
                if check_port_active(port) and _check_model_ready(port):
                    is_running = True
                    break
            time.sleep(0.5)

        if is_running:
            start_success = True
            with _recent_stops_lock:
                _recent_stops.pop(model, None)
            # Clear start timestamp since model is confirmed running
            with _start_timestamps_lock:
                _start_timestamps.pop(model, None)
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

            # Run warmup if enabled
            if meta.get("warmup_enabled", False):
                warmup_message = meta.get("warmup_message", "Hello")
                warmup_max_tokens = meta.get("warmup_max_tokens", 10)
                log.info(f"Running warmup for {model} on port {port}")
                add_log_entry(f"🔥 Warmup: {model} on port {port}", "yellow")
                warmup_success = warmup_model(port, warmup_message, warmup_max_tokens)
                if warmup_success:
                    add_log_entry(f"✓ Warmup successful: {model}", "green")
                else:
                    add_log_entry(f"⚠ Warmup failed: {model}", "orange")

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

    success, pid = _stop_model(model, port)
    
    return jsonify({"status": "stopped", "pid": pid, "success": success})
