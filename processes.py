#!/usr/bin/env python3
"""
Process lifecycle management for llama-server.

This module provides functions to find, start, stop, and check the status of llama-server processes.

NOTE: This module is for LOCAL mode only. In remote mode, use the remote_client module
or ModelRegistry with a remote config.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

from config import (
    BASE_DIR,
    DEFAULTS,
    LLAMA_SERVER,
    WINDOWS,
    WSL,
    convert_path_for_binary,
    resolve_model_path,
)
from models import InvalidModeError

log = logging.getLogger("llamaweb.processes")


def _get_wsl_host_ip() -> str | None:
    """
    Get the Windows host IP address in WSL.
    
    Tries multiple methods to find the Windows host IP:
    1. WSL interface IP (shared networking mode - Windows and WSL share same IP)
    2. Default gateway (standard WSL2 behavior)
    3. Resolv.conf nameserver (fallback)
    
    Returns None if not found or not WSL.
    """
    def _get_default_gateway() -> str | None:
        """Get the default gateway IP using ip command."""
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse output like: "default via 192.168.86.1 dev eth2 proto kernel metric 55"
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] == "default" and parts[1] == "via":
                        gateway = parts[2]
                        # Validate it's a private IP
                        try:
                            ip_obj = ipaddress.IPv4Address(gateway)
                            if ip_obj.is_private:
                                return gateway
                        except ipaddress.AddressValueError:
                            pass
        except Exception:
            pass
        return None
    
    def _get_wsl_interface_ip() -> str | None:
        """Get the IP of the primary WSL interface (for shared networking mode)."""
        try:
            result = subprocess.run(
                ["ip", "-4", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse output to find non-loopback interfaces
                current_iface = None
                for line in result.stdout.splitlines():
                    # Check for interface name (line starts with number and colon)
                    if line.strip() and line[0].isdigit() and ": " in line:
                        # Extract interface name (e.g., "eth2:" from "5: eth2: ...")
                        iface_part = line.split(":", 1)[1].strip()
                        current_iface = iface_part.split()[0].rstrip(":") if iface_part else None
                    # Check for inet address
                    elif line.strip().startswith("inet ") and current_iface and current_iface != "lo":
                        parts = line.split()
                        if len(parts) >= 2:
                            ip = parts[1].split("/")[0]
                            # Skip link-local addresses
                            try:
                                ip_obj = ipaddress.IPv4Address(ip)
                                if ip_obj.is_private and not ip_obj.is_link_local:
                                    return ip
                            except ipaddress.AddressValueError:
                                pass
        except Exception:
            pass
        return None
    
    # Method 1: Try WSL interface IP (shared networking mode)
    # In shared mode, Windows and WSL share the same IP
    wsl_ip = _get_wsl_interface_ip()
    if wsl_ip:
        return wsl_ip
    
    # Method 2: Try default gateway (standard WSL2)
    gateway = _get_default_gateway()
    if gateway:
        return gateway
    
    # Method 3: Fallback to resolv.conf nameserver
    try:
        resolv = Path("/etc/resolv.conf")
        if resolv.exists():
            for line in resolv.read_text().splitlines():
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        ip = parts[1]
                        try:
                            ip_obj = ipaddress.IPv4Address(ip)
                            if ip_obj.is_private:
                                return ip
                        except ipaddress.AddressValueError:
                            pass
    except Exception:
        pass
    return None


def get_server_host() -> str:
    """
    Determine which host the client should use to connect to the server.
    For WSL2 running a Windows llama-server.exe, the server binds on the Windows
    host's IP (obtained from resolv.conf) and must be reachable from WSL. For all
    other configurations, use 127.0.0.1 for client connections (0.0.0.0 is a bind
    address, not a valid connect address).
    
    Note: The host is resolved on each call to handle network changes (e.g., DHCP
    lease renewal in WSL+Windows mode) that could result in stale cached IPs.
    """
    # When running in WSL with a Windows binary, the server runs on Windows
    # and listens on the Windows host IP. We need to connect to that IP.
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        wsl_host = _get_wsl_host_ip()
        if wsl_host:
            log.info(f"WSL+Windows detected: using host {wsl_host} for client connections")
            return wsl_host
        else:
            # Fallback: try 127.0.0.1 which may work if Windows has loopback enabled
            log.warning("WSL+Windows detected but could not determine host IP; falling back to 127.0.0.1")
            return "127.0.0.1"

    # For all other cases (native Windows, native Linux), client connects to localhost
    return "127.0.0.1"


def _validate_local_mode() -> None:
    """
    Validate that we're in local mode.
    
    Raises:
        InvalidModeError: If REMOTE_MODE is enabled
    """
    import os
    remote_mode = os.getenv("REMOTE_MODE", "").strip().lower() in ("true", "1", "yes", "on")
    if remote_mode:
        raise InvalidModeError("process lifecycle operations")


# Wrap existing functions with mode validation
# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def find_pid_by_port(port: int) -> int | None:
    """Find PID of process listening on the given port."""
    _validate_local_mode()
    
    # In WSL+Windows mode, use netstat to find the PID
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        return _find_pid_by_port_wsl(port)
    
    # Standard Linux/Unix approach
    return _find_pid_by_port_unix(port)


def _find_pid_by_port_wsl(port: int) -> int | None:
    """Find PID using netstat for WSL+Windows mode."""
    # Use netstat via cmd.exe to find the PID - more reliable than PowerShell
    try:
        cmd_path = "/mnt/c/Windows/System32/cmd.exe"
        if os.path.exists(cmd_path):
            result = subprocess.run(
                [cmd_path, "/c", f"netstat -ano 2>&1 | findstr :{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Parse netstat output: "TCP 0.0.0.0:PORT ... LISTENING PID"
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 5 and "LISTENING" in line:
                    # Last column is the PID
                    pid_str = parts[-1]
                    if pid_str.isdigit():
                        return int(pid_str)
    except Exception:
        pass
    return None


def _find_pid_by_port_unix(port: int) -> int | None:
    """Find PID using ss/lsof/netstat for Unix/Linux systems."""
    for binary in ("ss", "lsof"):
        path = shutil.which(binary)
        if not path:
            continue
        try:
            if binary == "ss":
                out = subprocess.check_output(
                    ["ss", "-tlnp", f"sport = :{port}"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=5,
                )
            else:
                out = subprocess.check_output(
                    ["lsof", "-ti", f"tcp:{port}"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=5,
                )
            for line in out.splitlines():
                if "pid=" in line:
                    pid_str = line.split("pid=")[1].split(",")[0]
                    return int(pid_str)
                if line.strip().isdigit():
                    return int(line.strip())
        except Exception:
            pass
    # netstat fallback
    netstat = shutil.which("netstat")
    if netstat:
        try:
            out = subprocess.check_output(
                [netstat, "-ano" if WINDOWS else "-plant"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    local = parts[1 if not WINDOWS else 1]
                    if local.endswith(f":{port}"):
                        candidate = parts[-1]
                        if candidate.isdigit():
                            return int(candidate)
        except Exception:
            pass
    return None


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def pids_of_llama() -> list[int]:
    """Find PIDs of all llama-server processes."""
    _validate_local_mode()
    pids: list[int] = []
    # On WSL with Windows binary, use tasklist via cmd.exe to find llama-server processes
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        try:
            cmd_path = "/mnt/c/Windows/System32/cmd.exe"
            if os.path.exists(cmd_path):
                result = subprocess.run(
                    [cmd_path, "/c", "tasklist 2>&1 | findstr /i llama"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                # Parse tasklist output: "llama-server.exe PID ..."
                for line in result.stdout.strip().splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and "llama-server" in parts[0].lower():
                        try:
                            pids.append(int(parts[1]))
                        except ValueError:
                            pass
                if pids:
                    return pids
        except Exception:
            pass

    if WINDOWS:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        for line in result.stdout.strip().splitlines():
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) >= 2 and parts[0].lower() == "llama-server.exe":
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
    else:
        ps = subprocess.run(["ps", "-eo", "pid,comm"], capture_output=True, text=True, timeout=5, check=False)
        for line in ps.stdout.splitlines()[1:]:
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and "llama-server" in parts[1]:
                try:
                    pids.append(int(parts[0]))
                except ValueError:
                    pass
    return pids


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def _kill_tree(pid: int, model: str = "") -> bool:
    """Kill a process and all its children. Returns True if successful."""
    _validate_local_mode()
    try:
        # On WSL with Windows binary, use taskkill via cmd.exe
        if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
            log.info(f"Killing Windows process {pid} via taskkill (WSL mode)")
            cmd_path = "/mnt/c/Windows/System32/cmd.exe"
            if os.path.exists(cmd_path):
                result = subprocess.run(
                    [cmd_path, "/c", f"taskkill /F /T /PID {pid} 2>&1"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                log.info(f"Kill result for {pid}: stdout={result.stdout.strip()}, stderr={result.stderr.strip()}")
                # Check if taskkill succeeded
                taskkill_succeeded = "successful" in result.stdout.lower() or result.returncode == 0
                
                # ALWAYS run fallback to ensure no orphaned llama-server processes
                log.info(f"Running fallback cleanup: killing all llama-server.exe processes")
                fallback_result = subprocess.run(
                    [cmd_path, "/c", "taskkill /F /IM llama-server.exe 2>&1"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                log.info(f"Fallback kill all result: stdout={fallback_result.stdout.strip()}")
                fallback_succeeded = "successful" in fallback_result.stdout.lower() or fallback_result.returncode == 0
                
                return taskkill_succeeded or fallback_succeeded
            # Fallback if cmd.exe not found
            log.info(f"cmd.exe not found, running fallback cleanup")
            result = subprocess.run(
                ["/mnt/c/Windows/System32/cmd.exe", "/c", "taskkill /F /IM llama-server.exe 2>&1"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            log.info(f"Fallback kill all result: stdout={result.stdout.strip()}")
            return "successful" in result.stdout.lower() or result.returncode == 0
        elif WINDOWS:
            # Use taskkill to kill process tree on Windows
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        else:
            # On Linux, kill the process group (negative PID)
            # First try to kill the process group
            try:
                os.killpg(pid, signal.SIGTERM)
                time.sleep(0.5)
                # Check if still alive
                try:
                    os.killpg(pid, 0)
                    # Still alive, use SIGKILL
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass  # Process already dead
                return True
            except (OSError, ProcessLookupError):
                # Process group doesn't exist, try killing just the process
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.5)
                    try:
                        os.kill(pid, 0)
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    return True
                except (OSError, ProcessLookupError):
                    return True  # Already dead
    except Exception as e:
        log.error(f"Failed to kill process {pid}: {e}")
        return False


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def check_port_active(port: int) -> bool:
    """Check if a port is active (has a listener)."""
    _validate_local_mode()
    import socket

    # Determine hosts to try - in WSL+Windows mode, try multiple options
    hosts_to_try = []
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        # In WSL+Windows mode, the server binds to 0.0.0.0 on Windows
        # Try the Windows host IP first (via get_server_host), then localhost as fallback
        wsl_host = get_server_host()
        if wsl_host and wsl_host != "127.0.0.1":
            hosts_to_try.append(wsl_host)
        hosts_to_try.append("127.0.0.1")
    else:
        hosts_to_try.append(get_server_host())

    # Use longer timeout for WSL environments
    timeout = 0.5 if WSL else 0.2

    for host in hosts_to_try:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                log.debug(f"Port {port} active on {host}")
                return True
        except OSError as e:
            log.debug(f"Port check for {port} on {host} failed: {e}")
            continue

    return False


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def _check_model_ready(port: int) -> bool:
    """Check if the model at the given port is ready to serve requests by hitting /v1/models."""
    _validate_local_mode()
    
    # Determine hosts to try - in WSL+Windows mode, try multiple options
    hosts_to_try = []
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        # In WSL+Windows mode, the server binds to 0.0.0.0 on Windows
        # Try the Windows host IP first (via get_server_host), then localhost as fallback
        wsl_host = get_server_host()
        if wsl_host and wsl_host != "127.0.0.1":
            hosts_to_try.append(wsl_host)
        hosts_to_try.append("127.0.0.1")
    else:
        hosts_to_try.append(get_server_host())

    for host in hosts_to_try:
        url = f"http://{host}:{port}/v1/models"
        try:
            # Increased timeout to 5 seconds for slower model startup
            resp = requests.get(url, timeout=5.0)
            if resp.status_code == 200:
                return True
        except Exception as e:
            log.debug(f"Health check failed for {url}: {e}")
            continue

    return False


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def warmup_model(port: int, message: str = "Hello", max_tokens: int = 10) -> bool:
    """Send a warmup request to the model to verify it's responsive and prime KV cache.
    
    Args:
        port: The port the model is running on
        message: The message to send for warmup
        max_tokens: Maximum tokens to generate in response
        
    Returns:
        True if warmup succeeded, False otherwise
    """
    _validate_local_mode()
    
    # Determine hosts to try - in WSL+Windows mode, try multiple options
    hosts_to_try = []
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        wsl_host = get_server_host()
        if wsl_host and wsl_host != "127.0.0.1":
            hosts_to_try.append(wsl_host)
        hosts_to_try.append("127.0.0.1")
    else:
        hosts_to_try.append(get_server_host())

    payload = {
        "model": "default",
        "messages": [{"role": "user", "content": message}],
        "max_tokens": max_tokens,
        "stream": False,
    }

    for host in hosts_to_try:
        url = f"http://{host}:{port}/v1/chat/completions"
        try:
            # Increased timeout to 30 seconds for slow model warmup (prompt processing + generation)
            resp = requests.post(url, json=payload, timeout=30.0)
            if resp.status_code == 200:
                log.info(f"Warmup successful for port {port} on {host}")
                return True
            else:
                log.warning(f"Warmup returned {resp.status_code} for {url}: {resp.text[:200]}")
        except Exception as e:
            log.debug(f"Warmup failed for {url}: {e}")
            continue

    log.warning(f"Warmup failed for port {port} on all hosts")
    return False


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def build_cmd(model: str, port: int | str, meta: dict[str, Any]) -> list[str]:
    """Build the command for starting a model."""
    _validate_local_mode()
    # Determine bind host: in WSL+Windows binary mode, bind to 0.0.0.0 so Windows host
    # accepts connections from WSL. In all other cases, use the configured default.
    bind_host = "0.0.0.0" if (WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe')) else DEFAULTS["host"]

    # Handle both relative and absolute model paths using helper (respects MODEL_DIR env var)
    model_path_str = meta.get("model_path", "")
    model_path = resolve_model_path(model_path_str)

    cmd: list[str] = [
        str(LLAMA_SERVER),
        "-m",
        convert_path_for_binary(model_path),
        "--alias",
        model,
        "--host",
        bind_host,
        "--port",
        str(port),
        "--threads",
        str(meta.get("threads", DEFAULTS["threads"])),
        "--threads-batch",
        str(meta.get("threads_batch", DEFAULTS["threads_batch"])),
        "--timeout",
        str(meta.get("timeout", DEFAULTS["timeout"])),
    ]
    # Handle load-mode for mlock and mmap (new llama.cpp API)
    # llama.cpp expects "mmap+mlock" order (mmap first)
    load_modes = []
    # Enable mmap by default unless explicitly disabled
    if not meta.get("no_mmap", DEFAULTS["no_mmap"]):
        load_modes.append("mmap")
    if meta.get("mlock", DEFAULTS["mlock"]):
        load_modes.append("mlock")
    # If neither mlock nor mmap, use "none"
    if load_modes:
        # Use '+' as separator for llama.cpp load-mode (e.g., "mmap+mlock")
        cmd += ["--load-mode", "+".join(load_modes)]
    else:
        cmd += ["--load-mode", "none"]
    
    flash = meta.get("flash_attn", DEFAULTS["flash_attn"])
    if flash and flash != "off":
        cmd += ["--flash-attn", flash]
    
    # Handle parallel: if "auto", use --kv-unified instead of --parallel
    parallel_val = meta.get("parallel")
    if parallel_val == "auto":
        cmd += ["--kv-unified"]
    elif parallel_val:
        cmd += ["--parallel", str(parallel_val)]
    
    if meta.get("ubatch_size"):
        cmd += ["--ubatch-size", str(meta["ubatch_size"])]
    
    # Agent/Tool parameters
    if meta.get("jinja", DEFAULTS["jinja"]):
        cmd += ["--jinja"]
    if meta.get("reasoning_preserve", DEFAULTS["reasoning_preserve"]):
        cmd += ["--reasoning-preserve"]
    
    # CPU-only optimizations
    gpu_layers = meta.get("gpu_layers", DEFAULTS["gpu_layers"])
    if gpu_layers is not None:
        cmd += ["-ngl", str(gpu_layers)]
    
    cache_type_k = meta.get("cache_type_k", DEFAULTS["cache_type_k"])
    if cache_type_k and cache_type_k != "fp16":
        cmd += ["--cache-type-k", cache_type_k]
    
    cache_type_v = meta.get("cache_type_v", DEFAULTS["cache_type_v"])
    if cache_type_v and cache_type_v != "fp16":
        cmd += ["--cache-type-v", cache_type_v]
    
    # NUMA requires a value: distribute, isolate, or numactl
    numa = meta.get("numa", DEFAULTS["numa"])
    if numa:
        numa_value = meta.get("numa_value", "distribute")
        cmd += ["--numa", numa_value]
    
    cpu_affinity = meta.get("cpu_affinity", DEFAULTS["cpu_affinity"])
    if cpu_affinity and cpu_affinity != "auto":
        cmd += ["--cpu-affinity", cpu_affinity]
    
    if meta.get("cpu_strict", DEFAULTS["cpu_strict"]):
        cmd += ["--cpu-strict", "1"]
    
    # Slot KV Cache Persistence
    slot_save_path = meta.get("slot_save_path", DEFAULTS["slot_save_path"])
    if slot_save_path:
        cmd += ["--slot-save-path", slot_save_path]
    
    cache_reuse = meta.get("cache_reuse", DEFAULTS["cache_reuse"])
    if cache_reuse:
        cmd += ["--cache-reuse", str(cache_reuse)]
    
    # Enable --swa-full only if explicitly requested (for sliding window attention models)
    # and slot_save_path is provided with cache_reuse > 0
    slot_save_path = meta.get("slot_save_path", DEFAULTS["slot_save_path"])
    use_swa_full = meta.get("swa_full", False)
    if use_swa_full and slot_save_path and cache_reuse:
        cmd += ["--swa-full"]
    
    batch_size = meta.get("batch_size", DEFAULTS["batch_size"])
    if batch_size:
        cmd += ["-b", str(batch_size)]
    
    # ctx_size uses -c (context size) - this also controls KV cache size
    ctx_size = meta.get("ctx_size", DEFAULTS["ctx_size"])
    if ctx_size:
        cmd += ["-c", str(ctx_size)]
    
    
    rope_freq_base = meta.get("rope_freq_base", DEFAULTS["rope_freq_base"])
    if rope_freq_base and rope_freq_base != 10000.0:
        cmd += ["--rope-freq-base", str(rope_freq_base)]
    
    rope_freq_scale = meta.get("rope_freq_scale", DEFAULTS["rope_freq_scale"])
    if rope_freq_scale and rope_freq_scale != 1.0:
        cmd += ["--rope-freq-scale", str(rope_freq_scale)]
    
    # YaRN RoPE scaling parameters
    rope_scaling_type = meta.get("rope_scaling_type", DEFAULTS["rope_scaling_type"])
    if rope_scaling_type and rope_scaling_type != "none":
        cmd += ["--rope-scaling", rope_scaling_type]
    
    rope_scale = meta.get("rope_scale", DEFAULTS["rope_scale"])
    if rope_scale and rope_scale != 1.0:
        cmd += ["--rope-scale", str(rope_scale)]
    
    yarn_orig_ctx = meta.get("yarn_orig_ctx", DEFAULTS["yarn_orig_ctx"])
    if yarn_orig_ctx and yarn_orig_ctx > 0:
        cmd += ["--yarn-orig-ctx", str(yarn_orig_ctx)]
    
    yarn_ext_factor = meta.get("yarn_ext_factor", DEFAULTS["yarn_ext_factor"])
    if yarn_ext_factor and yarn_ext_factor >= 0:
        cmd += ["--yarn-ext-factor", str(yarn_ext_factor)]
    
    yarn_attn_factor = meta.get("yarn_attn_factor", DEFAULTS["yarn_attn_factor"])
    if yarn_attn_factor and yarn_attn_factor != 1.0:
        cmd += ["--yarn-attn-factor", str(yarn_attn_factor)]
    
    yarn_beta_slow = meta.get("yarn_beta_slow", DEFAULTS["yarn_beta_slow"])
    if yarn_beta_slow and yarn_beta_slow != 1.0:
        cmd += ["--yarn-beta-slow", str(yarn_beta_slow)]
    
    yarn_beta_fast = meta.get("yarn_beta_fast", DEFAULTS["yarn_beta_fast"])
    if yarn_beta_fast and yarn_beta_fast != 32.0:
        cmd += ["--yarn-beta-fast", str(yarn_beta_fast)]

    # Enable Prometheus metrics endpoint for performance monitoring
    cmd += ["--metrics"]

    # Speculative decoding parameters
    spec_type = meta.get("spec_type", DEFAULTS["spec_type"])
    if spec_type and spec_type != "none":
        cmd += ["--spec-type", spec_type]
        
        # Parse spec types (comma-separated)
        spec_types = [t.strip() for t in spec_type.split(",") if t.strip()]
        
        # Determine which parameter groups are needed
        has_draft_type = any(t.startswith("draft-") for t in spec_types)
        has_ngram_type = any(t.startswith("ngram-") for t in spec_types)
        
        # Draft model parameters (used by draft-* types)
        if has_draft_type:
            spec_draft_model = meta.get("spec_draft_model", DEFAULTS["spec_draft_model"])
            if spec_draft_model:
                draft_path = resolve_model_path(spec_draft_model)
                cmd += ["--spec-draft-model", convert_path_for_binary(draft_path)]
            
            spec_draft_hf_repo = meta.get("spec_draft_hf_repo", DEFAULTS["spec_draft_hf_repo"])
            if spec_draft_hf_repo:
                cmd += ["--spec-draft-hf", spec_draft_hf_repo]
            
            spec_draft_n_max = meta.get("spec_draft_n_max", DEFAULTS["spec_draft_n_max"])
            if spec_draft_n_max > 0:
                cmd += ["--spec-draft-n-max", str(spec_draft_n_max)]
            
            spec_draft_n_min = meta.get("spec_draft_n_min", DEFAULTS["spec_draft_n_min"])
            if spec_draft_n_min > 0:
                cmd += ["--spec-draft-n-min", str(spec_draft_n_min)]
            
            spec_draft_p_split = meta.get("spec_draft_p_split", DEFAULTS["spec_draft_p_split"])
            if spec_draft_p_split > 0:
                cmd += ["--spec-draft-p-split", str(spec_draft_p_split)]
            
            spec_draft_p_min = meta.get("spec_draft_p_min", DEFAULTS["spec_draft_p_min"])
            if spec_draft_p_min > 0:
                cmd += ["--spec-draft-p-min", str(spec_draft_p_min)]
            
            spec_draft_ngl = meta.get("spec_draft_ngl", DEFAULTS["spec_draft_ngl"])
            if spec_draft_ngl and spec_draft_ngl != "auto":
                cmd += ["--spec-draft-ngl", str(spec_draft_ngl)]
            
            # Draft model sampling parameters
            spec_draft_temp = meta.get("spec_draft_temp", DEFAULTS["spec_draft_temp"])
            if spec_draft_temp > 0:
                cmd += ["--spec-draft-temp", str(spec_draft_temp)]
            
            spec_draft_top_p = meta.get("spec_draft_top_p", DEFAULTS["spec_draft_top_p"])
            if spec_draft_top_p < 1.0:
                cmd += ["--spec-draft-top-p", str(spec_draft_top_p)]
            
            spec_draft_min_p = meta.get("spec_draft_min_p", DEFAULTS["spec_draft_min_p"])
            if spec_draft_min_p > 0:
                cmd += ["--spec-draft-min-p", str(spec_draft_min_p)]
            
            spec_draft_typical = meta.get("spec_draft_typical", DEFAULTS["spec_draft_typical"])
            if spec_draft_typical < 1.0:
                cmd += ["--spec-draft-typical", str(spec_draft_typical)]
        
        # Ngram parameters (used by ngram-* types)
        if has_ngram_type:
            spec_ngram_size_n = meta.get("spec_ngram_size_n", DEFAULTS["spec_ngram_size_n"])
            if spec_ngram_size_n > 0:
                # Correct argument names for ngram-simple spec type in newer llama.cpp
                cmd += ["--spec-ngram-simple-size-n", str(spec_ngram_size_n)]
            
            spec_ngram_size_m = meta.get("spec_ngram_size_m", DEFAULTS["spec_ngram_size_m"])
            if spec_ngram_size_m > 0:
                # Correct argument names for ngram-simple spec type in newer llama.cpp
                cmd += ["--spec-ngram-simple-size-m", str(spec_ngram_size_m)]
            
            spec_ngram_min_hits = meta.get("spec_ngram_min_hits", DEFAULTS["spec_ngram_min_hits"])
            if spec_ngram_min_hits > 1:
                cmd += ["--spec-ngram-simple-min-hits", str(spec_ngram_min_hits)]

    log.info(f"Build command for {model}: {cmd}")
    return cmd
