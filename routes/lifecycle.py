"""Lifecycle management: start and stop models."""
import logging
import time
import threading
import signal
import psutil
from flask import Blueprint, jsonify, request, g
import subprocess
import os

import config
from config import BASE_DIR, DEFAULTS, WINDOWS, WSL, convert_path_for_binary, Config
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
from mnemonic_proxy import (
    create_proxy_manager,
    get_proxy_manager,
    list_proxy_managers,
    remove_proxy_manager,
    stop_all_proxies,
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
        if WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'):
            with _wsl_pids_lock:
                _wsl_pids.pop(model, None)
        return True, None
    
    success = _kill_tree(pid, model)
    log.info(f"Kill result for {model} (pid={pid}): success={success}")
    
    if WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'):
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
            "device": getattr(model_dict, 'device', ''),
            "cpu_moe": getattr(model_dict, 'cpu_moe', False),
            "n_cpu_moe": getattr(model_dict, 'n_cpu_moe', 0),
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
            # Ngram-mod parameters
            "spec_ngram_mod_n_match": getattr(model_dict, 'spec_ngram_mod_n_match', 24),
            "spec_ngram_mod_n_min": getattr(model_dict, 'spec_ngram_mod_n_min', 48),
            "spec_ngram_mod_n_max": getattr(model_dict, 'spec_ngram_mod_n_max', 64),
            # Draft model KV cache type
            "spec_draft_type_k": getattr(model_dict, 'spec_draft_type_k', 'fp16'),
            "spec_draft_type_v": getattr(model_dict, 'spec_draft_type_v', 'fp16'),
            # Warmup parameters
            "warmup_enabled": getattr(model_dict, 'warmup_enabled', False),
            "warmup_message": getattr(model_dict, 'warmup_message', 'Hello'),
            "warmup_max_tokens": getattr(model_dict, 'warmup_max_tokens', 10),
            # Slot KV Cache Persistence
            "slot_save_path": getattr(model_dict, 'slot_save_path', ''),
            "cache_reuse": getattr(model_dict, 'cache_reuse', 4096),
            "swa_full": getattr(model_dict, 'swa_full', False),
            "keep": getattr(model_dict, 'keep', 0),
            # Logging
            "log_verbosity": getattr(model_dict, 'log_verbosity', 3),
            # Chat template
            "chat_template_file": getattr(model_dict, 'chat_template_file', ''),
            # Generation / Sampling parameters
            "use_generation_params": getattr(model_dict, 'use_generation_params', True),
            "temperature": getattr(model_dict, 'temperature', 0.7),
            "top_k": getattr(model_dict, 'top_k', 40),
            "top_p": getattr(model_dict, 'top_p', 0.9),
            "min_p": getattr(model_dict, 'min_p', 0.05),
            "repeat_penalty": getattr(model_dict, 'repeat_penalty', 1.1),
            "presence_penalty": getattr(model_dict, 'presence_penalty', 0.0),
            "reasoning": getattr(model_dict, 'reasoning', 'auto'),
            "reasoning_effort": getattr(model_dict, 'reasoning_effort', 'default'),
            "reasoning_budget": getattr(model_dict, 'reasoning_budget', -1),
            "reasoning_budget_message": getattr(model_dict, 'reasoning_budget_message', ''),
        }

        # Check if already running
        active = check_port_active(port)
        if active:
            if WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'):
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
        
        # WSL + Windows binary workaround: use wrapper batch file to ensure
        # environment variables are properly set in Windows process context
        llama_server = config.LLAMA_SERVER
        # Fallback: if LLAMA_SERVER not configured, use known Windows path
        if llama_server is None:
            llama_server = Path("/mnt/c/llama.cpp-sycl/bin/llama-server.exe")
            log.warning(f"LLAMA_SERVER not configured, using fallback: {llama_server}")
        
        log.info(f"Debug: WSL={WSL}, LLAMA_SERVER={llama_server}, LLAMA_SERVER_TYPE={config.LLAMA_SERVER_TYPE}")
        if WSL and str(llama_server).endswith('.exe'):
            wrapper_path = BASE_DIR / "llama-server-wrapper.ps1"
            if wrapper_path.exists():
                # Convert WSL path to Windows path for PowerShell (use forward slashes)
                wrapper_windows = str(wrapper_path).replace("/mnt/c/", "C:/").replace("/", "/")
                llama_server_windows = str(llama_server).replace("/mnt/c/", "C:/").replace("/", "/")
                # Build command with llama-server path as first argument to PowerShell script
                cmd_win = list(cmd)
                cmd_win[0] = llama_server_windows
                # Build the full command string for powershell.exe
                wrapper_cmd = f'powershell.exe -ExecutionPolicy Bypass -File "{wrapper_windows}" ' + ' '.join(cmd_win)
                cmd = wrapper_cmd
                log.info(f"Using wrapper PowerShell script for Windows binary: {wrapper_windows}")
                log.info(f"Full command: {wrapper_cmd}")
        
        # Ensure Intel GPU uses OpenCL backend (needed for some Intel GPUs)
        # Set multiple environment variables to force OpenCL backend over Level Zero
        # Use direct assignment (not setdefault) to override any existing values
        env["ONEAPI_DEVICE_SELECTOR"] = "opencl:gpu"
        env["SYCL_DEVICE_FILTER"] = "opencl:gpu"
        # Disable Level Zero API in llama.cpp SYCL backend
        env["GGML_SYCL_USE_LEVEL_ZERO_API"] = "0"
        
        # Log relevant environment variables for debugging GPU device selection
        log.info(f"Environment for {model}: ONEAPI_DEVICE_SELECTOR={env.get('ONEAPI_DEVICE_SELECTOR')}, "
                 f"SYCL_DEVICE_FILTER={env.get('SYCL_DEVICE_FILTER')}, "
                 f"GGML_SYCL_USE_LEVEL_ZERO_API={env.get('GGML_SYCL_USE_LEVEL_ZERO_API')}, "
                 f"OCL_ICD_FILENAMES={env.get('OCL_ICD_FILENAMES')}")
        
        # Convert cmd to string if it's a list (for logging and Popen with shell)
        cmd_str = cmd if isinstance(cmd, str) else ' '.join(cmd)
        
        # Launch process
        log.info(f"Starting model {model}: {cmd_str}")
        add_log_entry(f"▶ Start: {model} on port {port}", "green")
        add_log_entry(f"Command: {cmd_str}", "cyan")
        try:
            # Capture stdout/stderr for real-time logging
            proc = subprocess.Popen(
                cmd_str,
                env=env,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                text=True,
                bufsize=1,  # Line buffered
                shell=True,  # Use shell to execute cmd.exe command string
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0,
            )
            pid = proc.pid
            if WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'):
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
                if check_port_active(port) and _check_model_ready(port, model):
                    is_running = True
                    break
            time.sleep(0.5)

        # If model is running, verify n_ctx matches expected ctx_size before proceeding
        # This ensures the proxy gets the correct window size
        if is_running:
            expected_ctx_size = meta.get("ctx_size", DEFAULTS["ctx_size"])
            log.info(f"Verifying n_ctx matches expected ctx_size={expected_ctx_size} for {model}")
            add_log_entry(f"🔍 Verifying context size: expected={expected_ctx_size}", "cyan")
            
            # Wait up to 30 seconds for n_ctx to match (model may need time to fully initialize)
            ctx_verified = False
            for attempt in range(60):  # 30 seconds max
                if proc and proc.poll() is not None:
                    log.warning(f"Process exited during n_ctx verification for {model}")
                    is_running = False
                    break
                
                try:
                    import requests
                    # Try multiple hosts (WSL+Windows mode)
                    hosts_to_try = []
                    if WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'):
                        wsl_host = get_server_host()
                        if wsl_host and wsl_host != "127.0.0.1":
                            hosts_to_try.append(wsl_host)
                        hosts_to_try.append("127.0.0.1")
                    else:
                        hosts_to_try.append(get_server_host())
                    
                    for host in hosts_to_try:
                        try:
                            resp = requests.get(f"http://{host}:{port}/props", timeout=2.0)
                            if resp.status_code == 200:
                                props = resp.json()
                                actual_n_ctx = props.get("n_ctx")
                                if actual_n_ctx is not None:
                                    if actual_n_ctx == expected_ctx_size:
                                        ctx_verified = True
                                        log.info(f"n_ctx verified: {actual_n_ctx} == {expected_ctx_size} for {model}")
                                        add_log_entry(f"✓ Context size verified: n_ctx={actual_n_ctx}", "green")
                                        break
                                    else:
                                        log.debug(f"n_ctx mismatch: got {actual_n_ctx}, expected {expected_ctx_size} (attempt {attempt+1}/60)")
                                break  # Got response, don't try other hosts
                        except Exception as e:
                            log.debug(f"Failed to check /props on {host}:{port}: {e}")
                            continue
                    
                    if ctx_verified:
                        break
                        
                    time.sleep(0.5)
                except Exception as e:
                    log.debug(f"Error during n_ctx verification: {e}")
                    time.sleep(0.5)
            
            if not ctx_verified:
                log.warning(f"n_ctx verification timed out for {model} (expected {expected_ctx_size})")
                add_log_entry(f"⚠ Context size verification timed out (expected {expected_ctx_size})", "orange")
                # Don't fail startup - model is running, just n_ctx didn't match in time
                # Proxy will use whatever n_ctx is actually set
            
            # Fetch the actual n_ctx from llama-server for proxy config
            actual_n_ctx = expected_ctx_size  # default fallback
            try:
                import requests
                hosts_to_try = []
                if WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'):
                    wsl_host = get_server_host()
                    if wsl_host and wsl_host != "127.0.0.1":
                        hosts_to_try.append(wsl_host)
                    hosts_to_try.append("127.0.0.1")
                else:
                    hosts_to_try.append(get_server_host())
                
                for host in hosts_to_try:
                    try:
                        resp = requests.get(f"http://{host}:{port}/props", timeout=2.0)
                        if resp.status_code == 200:
                            props = resp.json()
                            actual_n_ctx = props.get("n_ctx", expected_ctx_size)
                            log.info(f"Actual n_ctx from llama-server: {actual_n_ctx} for {model}")
                            break
                    except Exception as e:
                        log.debug(f"Failed to fetch /props from {host}:{port}: {e}")
                        continue
            except Exception as e:
                log.debug(f"Error fetching actual n_ctx: {e}")

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
            if WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'):
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

            # Start mnemonic-proxy if enabled for this model
            # Model has individual mnemonic_proxy_* fields, construct config dict
            mnemonic_proxy_enabled = getattr(model_dict, 'mnemonic_proxy_enabled', False)
            if mnemonic_proxy_enabled:
                proxy_port = getattr(model_dict, 'mnemonic_proxy_port', 8096)
                data_dir = getattr(model_dict, 'mnemonic_proxy_data_dir', '')
                slot_dir = getattr(model_dict, 'mnemonic_proxy_slot_dir', '')
                tokenizer_path = getattr(model_dict, 'mnemonic_proxy_tokenizer_path', '')
                config_override = getattr(model_dict, 'mnemonic_proxy_config_override', {})
                mcp_enabled = getattr(model_dict, 'mnemonic_proxy_mcp_enabled', False)
                mcp_port = getattr(model_dict, 'mnemonic_proxy_mcp_port', 8133)
                
                log.info(f"Starting mnemonic-proxy for {model} on port {proxy_port}")
                add_log_entry(f"🔗 mnemonic-proxy: {model} on port {proxy_port}", "blue")
                
                proxy_manager = create_proxy_manager(
                    model_name=model,
                    llama_port=port,
                    proxy_port=proxy_port,
                    data_dir=data_dir,
                    slot_dir=slot_dir,
                    tokenizer_path=tokenizer_path,
                    config_override=config_override,
                    mcp_enabled=mcp_enabled,
                    mcp_port=mcp_port,
                    verified_ctx_size=actual_n_ctx,
                )
                
                proxy_started = proxy_manager.start()
                if proxy_started:
                    add_log_entry(f"✓ mnemonic-proxy started: {model} on port {proxy_port}", "green")
                    log.info(f"mnemonic-proxy started successfully for {model} on port {proxy_port}")
                else:
                    add_log_entry(f"⚠ mnemonic-proxy failed to start: {model}", "orange")
                    log.warning(f"mnemonic-proxy failed to start for {model} on port {proxy_port}")

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
    
    # Stop mnemonic-proxy if running for this model
    remove_proxy_manager(model)
    add_log_entry(f"🔗 mnemonic-proxy stopped: {model}", "blue")
    
    return jsonify({"status": "stopped", "pid": pid, "success": success})


# ──────────────────────────────────────────────────────────────────────────────
# Mnemonic Proxy Lifecycle API Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@lifecycle_bp.route("/api/proxy/status/<model>", methods=["GET"])
def api_proxy_status(model):
    """Get mnemonic-proxy status for a model."""
    model = model.strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    registry = g.registry
    models = registry.list_models()
    model_dict = None
    for m in models:
        if m.name == model:
            model_dict = m
            break
    if not model_dict:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    
    # Get proxy manager
    proxy_manager = get_proxy_manager(model)
    if proxy_manager:
        return jsonify(proxy_manager.get_status())
    else:
        # Return default status if not created yet
        mnemonic_proxy_enabled = getattr(model_dict, 'mnemonic_proxy_enabled', False)
        if not mnemonic_proxy_enabled:
            return jsonify({
                "model_name": model,
                "running": False,
                "enabled": False,
                "message": "mnemonic-proxy not enabled for this model"
            })
        return jsonify({
            "model_name": model,
            "running": False,
            "enabled": True,
            "message": "mnemonic-proxy not started"
        })


@lifecycle_bp.route("/api/proxy/start/<model>", methods=["POST"])
def api_proxy_start(model):
    """Start mnemonic-proxy for a model."""
    model = model.strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    registry = g.registry
    models = registry.list_models()
    model_dict = None
    for m in models:
        if m.name == model:
            model_dict = m
            break
    if not model_dict:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    
    # Check if mnemonic-proxy is enabled for this model
    mnemonic_proxy_enabled = getattr(model_dict, 'mnemonic_proxy_enabled', False)
    if not mnemonic_proxy_enabled:
        return jsonify({"error": "mnemonic-proxy not enabled for this model"}), 400
    
    # Get llama-server port
    llama_port = int(model_dict.port)
    
    # Get proxy config from model
    proxy_port = getattr(model_dict, 'mnemonic_proxy_port', 8096)
    data_dir = getattr(model_dict, 'mnemonic_proxy_data_dir', '')
    slot_dir = getattr(model_dict, 'mnemonic_proxy_slot_dir', '')
    tokenizer_path = getattr(model_dict, 'mnemonic_proxy_tokenizer_path', '')
    config_override = getattr(model_dict, 'mnemonic_proxy_config_override', {})
    mcp_enabled = getattr(model_dict, 'mnemonic_proxy_mcp_enabled', False)
    mcp_port = getattr(model_dict, 'mnemonic_proxy_mcp_port', 8133)
    
    # Fetch actual n_ctx from llama-server for proxy config
    actual_n_ctx = 32768  # default fallback
    try:
        import requests
        hosts_to_try = []
        if WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'):
            wsl_host = get_server_host()
            if wsl_host and wsl_host != "127.0.0.1":
                hosts_to_try.append(wsl_host)
            hosts_to_try.append("127.0.0.1")
        else:
            hosts_to_try.append(get_server_host())
        
        for host in hosts_to_try:
            try:
                resp = requests.get(f"http://{host}:{llama_port}/props", timeout=2.0)
                if resp.status_code == 200:
                    props = resp.json()
                    actual_n_ctx = props.get("n_ctx", 32768)
                    log.info(f"Actual n_ctx from llama-server for proxy: {actual_n_ctx}")
                    break
            except Exception as e:
                log.debug(f"Failed to fetch /props from {host}:{llama_port}: {e}")
                continue
    except Exception as e:
        log.debug(f"Error fetching actual n_ctx for proxy: {e}")
    
    # Create and start proxy manager
    proxy_manager = create_proxy_manager(
        model_name=model,
        llama_port=llama_port,
        proxy_port=proxy_port,
        data_dir=data_dir,
        slot_dir=slot_dir,
        tokenizer_path=tokenizer_path,
        config_override=config_override,
        mcp_enabled=mcp_enabled,
        mcp_port=mcp_port,
        verified_ctx_size=actual_n_ctx,
    )
    
    success = proxy_manager.start()
    if success:
        add_log_entry(f"🔗 mnemonic-proxy started: {model} on port {proxy_port}", "green")
        return jsonify({"status": "started", "model": model, "proxy_port": proxy_port})
    else:
        add_log_entry(f"⚠ mnemonic-proxy failed to start: {model}", "orange")
        return jsonify({"error": "Failed to start mnemonic-proxy", "model": model}), 500


@lifecycle_bp.route("/api/proxy/stop/<model>", methods=["POST"])
def api_proxy_stop(model):
    """Stop mnemonic-proxy for a model."""
    model = model.strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    success = remove_proxy_manager(model)
    if success:
        add_log_entry(f"🔗 mnemonic-proxy stopped: {model}", "blue")
        return jsonify({"status": "stopped", "model": model})
    else:
        return jsonify({"status": "not_running", "model": model})


@lifecycle_bp.route("/api/proxy/logs/<model>", methods=["GET"])
def api_proxy_logs(model):
    """Get mnemonic-proxy logs for a model."""
    model = model.strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    # Get lines parameter (default 100)
    lines = request.args.get("lines", 100, type=int)
    
    proxy_manager = get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "mnemonic-proxy not running for this model", "model": model}), 404
    
    logs = proxy_manager.get_logs(lines=lines)
    return jsonify({"model": model, "logs": logs})


@lifecycle_bp.route("/api/proxy/metrics/<model>", methods=["GET"])
def api_proxy_metrics(model):
    """Get mnemonic-proxy metrics for a model."""
    model = model.strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    proxy_manager = get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "mnemonic-proxy not running for this model", "model": model}), 404
    
    # Get proxy port from manager
    proxy_port = proxy_manager.proxy_port
    
    try:
        import requests
        resp = requests.get(f"http://127.0.0.1:{proxy_port}/metrics", timeout=5)
        if resp.status_code == 200:
            # Parse Prometheus metrics format
            metrics = parse_prometheus_metrics(resp.text)
            return jsonify({"model": model, "metrics": metrics, "raw": resp.text})
        else:
            return jsonify({"error": f"Failed to fetch metrics: {resp.status_code}", "model": model}), 502
    except Exception as e:
        return jsonify({"error": f"Failed to fetch metrics: {str(e)}", "model": model}), 502


def parse_prometheus_metrics(text: str) -> dict:
    """Parse Prometheus metrics text format into a structured dict."""
    metrics = {}
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Format: metric_name{labels} value
        if ' ' in line:
            metric_part, value_str = line.rsplit(' ', 1)
            try:
                value = float(value_str)
            except ValueError:
                continue
            # Parse metric name and labels
            if '{' in metric_part:
                name, labels_part = metric_part.split('{', 1)
                labels_part = labels_part.rstrip('}')
                labels = {}
                for label in labels_part.split(','):
                    if '=' in label:
                        k, v = label.split('=', 1)
                        labels[k] = v.strip('"')
            else:
                name = metric_part
                labels = {}
            if name not in metrics:
                metrics[name] = []
            metrics[name].append({"labels": labels, "value": value})
    return metrics


@lifecycle_bp.route("/api/proxy/strata-context/<model>", methods=["GET"])
def api_proxy_strata_context(model):
    """Get current Strata context from mnemonic-proxy.
    
    Query parameters:
    - conversation_id: Optional conversation ID to get context for specific conversation.
      If not provided, returns the most recent conversation's context.
    """
    model = model.strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    proxy_manager = get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "mnemonic-proxy not running for this model", "model": model}), 404
    
    proxy_port = proxy_manager.proxy_port
    
    # Get optional conversation_id from query params
    conversation_id = request.args.get('conversation_id', '').strip()
    
    try:
        import requests
        
        # If conversation_id provided, use the store API to get exact context
        if conversation_id:
            resp = requests.get(
                f"http://127.0.0.1:{proxy_port}/v1/strata/conversations/{conversation_id}",
                timeout=10,
                headers={"Accept": "application/json"}
            )
            if resp.status_code == 200:
                data = resp.json()
                # Build strata_context from store data
                strata_context = {
                    "conversation_id": data.get("conversation_id"),
                    "archived": data.get("archived", 0),
                    "archived_tokens": data.get("archived_tokens", 0),
                    "masked": data.get("masked", 0),
                    "masked_tokens": data.get("masked_tokens", 0),
                    "segments": data.get("segments", 0),
                    "segment_list": data.get("segment_list", []),
                    "source": "store"
                }
                return jsonify({
                    "model": model,
                    "strata_context": strata_context,
                    "status_code": 200,
                    "conversation_id": conversation_id
                })
        
        # Otherwise, make a minimal request to get current Strata context
        # Note: This creates a new conversation each time (for backward compatibility)
        resp = requests.post(
            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0
            },
            timeout=10,
            headers={"Accept": "application/json"}
        )
        
        strata_context = None
        # Check response headers for X-Strata-Context
        strata_header = resp.headers.get('X-Strata-Context')
        if strata_header:
            # Parse header: seg=0 active=290 virtual=76 masked=0 recalls=0 cached=59
            parts = strata_header.split()
            strata_context = {}
            for part in parts:
                if '=' in part:
                    k, v = part.split('=', 1)
                    try:
                        strata_context[k] = int(v)
                    except ValueError:
                        strata_context[k] = v
        
        # Also check response body for strata_context
        if resp.status_code == 200:
            try:
                body = resp.json()
                if 'strata_context' in body:
                    strata_context = body['strata_context']
            except Exception:
                pass
        
        return jsonify({
            "model": model,
            "strata_context": strata_context,
            "status_code": resp.status_code,
            "conversation_id": None
        })
    except Exception as e:
        return jsonify({"error": f"Failed to fetch Strata context: {str(e)}", "model": model}), 502


@lifecycle_bp.route("/api/proxy/list", methods=["GET"])
def api_proxy_list():
    """List all mnemonic-proxy instances with their status."""
    proxies = list_proxy_managers()
    return jsonify({"proxies": proxies})


# ──────────────────────────────────────────────────────────────────────────────
# Mnemonic Proxy MCP Server API Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@lifecycle_bp.route("/api/proxy/mcp/start/<model>", methods=["POST"])
def api_proxy_mcp_start(model):
    """Start mnemonic-mcp server for a model."""
    model = model.strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    proxy_manager = get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "mnemonic-proxy not running for this model", "model": model}), 404
    
    if not proxy_manager.mcp_enabled:
        return jsonify({"error": "MCP server not enabled for this model"}), 400
    
    success = proxy_manager.start_mcp()
    if success:
        add_log_entry(f"🔗 mnemonic-mcp started: {model} on port {proxy_manager.mcp_port}", "green")
        return jsonify({"status": "started", "model": model, "mcp_port": proxy_manager.mcp_port})
    else:
        add_log_entry(f"⚠ mnemonic-mcp failed to start: {model}", "orange")
        return jsonify({"error": "Failed to start mnemonic-mcp", "model": model}), 500


@lifecycle_bp.route("/api/proxy/mcp/stop/<model>", methods=["POST"])
def api_proxy_mcp_stop(model):
    """Stop mnemonic-mcp server for a model."""
    model = model.strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    proxy_manager = get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "mnemonic-proxy not running for this model", "model": model}), 404
    
    success = proxy_manager.stop_mcp()
    if success:
        add_log_entry(f"🔗 mnemonic-mcp stopped: {model}", "blue")
        return jsonify({"status": "stopped", "model": model})
    else:
        return jsonify({"status": "not_running", "model": model})


@lifecycle_bp.route("/api/proxy/mcp/status/<model>", methods=["GET"])
def api_proxy_mcp_status(model):
    """Get mnemonic-mcp server status for a model."""
    model = model.strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    proxy_manager = get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "mnemonic-proxy not running for this model", "model": model}), 404
    
    return jsonify(proxy_manager.get_mcp_status())
