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
)
from models import InvalidModeError

log = logging.getLogger("llamaweb.processes")


def _get_wsl_host_ip() -> str | None:
    """
    Get the Windows host IP address from WSL's resolv.conf.
    Returns None if not found or not WSL.
    """
    try:
        resolv = Path("/etc/resolv.conf")
        if not resolv.exists():
            return None
        for line in resolv.read_text().splitlines():
            line = line.strip()
            if line.startswith("nameserver"):
                parts = line.split()
                if len(parts) >= 2:
                    ip = parts[1]
                    # Validate IPv4 address using ipaddress module
                    try:
                        ipaddress.IPv4Address(ip)
                        # Additional validation: ensure it's a private/reserved IP (Windows host)
                        # Windows host IPs in WSL are typically in the private range
                        ip_obj = ipaddress.IPv4Address(ip)
                        if ip_obj.is_private or ip_obj.is_reserved:
                            return ip
                    except ipaddress.AddressValueError:
                        pass  # Invalid IP, continue to next line
    except Exception:
        pass
    return None


def get_server_host() -> str:
    """
    Determine which host the client should use to connect to the server.
    For WSL2 running a Windows llama-server.exe, the server binds on the Windows
    host's IP (obtained from resolv.conf) and must be reachable from WSL. For all
    other configurations, use the configured default host (typically 127.0.0.1).
    
    Note: The host is resolved on each call to handle network changes (e.g., DHCP
    lease renewal in WSL+Windows mode) that could result in stale cached IPs.
    """
    host = DEFAULTS["host"]
    # When running in WSL with a Windows binary, the server runs on Windows
    # and listens on the Windows host IP. We need to connect to that IP.
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        wsl_host = _get_wsl_host_ip()
        if wsl_host:
            host = wsl_host
            log.info(f"WSL+Windows detected: using host {host} for client connections")
        else:
            log.warning("WSL+Windows detected but could not determine host IP; falling back to default")

    return host


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
    
    # In WSL+Windows mode, use PowerShell to find the PID
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        return _find_pid_by_port_wsl(port)
    
    # Standard Linux/Unix approach
    return _find_pid_by_port_unix(port)


def _find_pid_by_port_wsl(port: int) -> int | None:
    """Find PID using PowerShell for WSL+Windows mode."""
    ps_bin = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    
    try:
        # Use PowerShell to find process listening on the port
        ps_cmd = f'Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess'
        result = subprocess.run(
            [ps_bin, "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            if line.strip().isdigit():
                return int(line.strip())
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
    # On WSL with Windows binary, use PowerShell to find llama-server processes
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        try:
            ps_cmd = 'Get-Process -Name "llama-server" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id'
            ps_bin = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
            result = subprocess.run(
                [ps_bin, "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if line.strip().isdigit():
                        pids.append(int(line.strip()))
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
        # On WSL with Windows binary, use PowerShell to kill the process
        if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
            log.info(f"Killing Windows process {pid} via PowerShell (WSL mode)")
            ps_bin = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
            # First, try to find the process and kill it
            ps_cmd = f'''try {{ $p = Get-Process -Id {pid} -ErrorAction Stop; $p | Stop-Process -Force -ErrorAction Stop; Write-Output "success" }} catch {{ Write-Output "failed: $($_.Exception.Message)" }}'''
            result = subprocess.run(
                [ps_bin, "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=15,
            )
            log.info(f"Kill result for {pid}: returncode={result.returncode}, stdout={result.stdout.strip()}, stderr={result.stderr.strip()}")
            if "success" in result.stdout.lower():
                return True
            # Fallback: kill all llama-server processes
            log.info(f"Fallback: killing all llama-server processes")
            ps_cmd = '''try { Get-Process -Name "llama-server" -ErrorAction Stop | Stop-Process -Force -ErrorAction Stop; Write-Output "success" } catch { Write-Output "failed" }'''
            result = subprocess.run(
                [ps_bin, "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=15,
            )
            log.info(f"Kill all result: returncode={result.returncode}, stdout={result.stdout.strip()}")
            return "success" in result.stdout.lower()
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

    try:
        host = get_server_host()
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError as e:
        log.debug(f"Port check for {port} failed: {e}")
        return False


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def _check_model_ready(port: int) -> bool:
    """Check if the model at the given port is ready to serve requests by hitting /v1/models."""
    _validate_local_mode()
    host = get_server_host()
    url = f"http://{host}:{port}/v1/models"
    try:
        resp = requests.get(url, timeout=0.5)
        return resp.status_code == 200
    except Exception:
        return False


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def build_cmd(model: str, port: int | str, meta: dict[str, Any]) -> list[str]:
    """Build the command for starting a model."""
    _validate_local_mode()
    # Determine bind host: in WSL+Windows binary mode, bind to 0.0.0.0 so Windows host
    # accepts connections from WSL. In all other cases, use the configured default.
    bind_host = "0.0.0.0" if (WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe')) else DEFAULTS["host"]

    cmd: list[str] = [
        str(LLAMA_SERVER),
        "-m",
        convert_path_for_binary(BASE_DIR / meta["model_path"]),
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
        "-c",
        str(meta.get("cache_size", DEFAULTS["cache_size"])),
        "--timeout",
        str(meta.get("timeout", DEFAULTS["timeout"])),
    ]
    if meta.get("mlock", DEFAULTS["mlock"]):
        cmd += ["--mlock"]
    flash = meta.get("flash_attn", DEFAULTS["flash_attn"])
    if flash and flash != "off":
        cmd += ["--flash-attn", flash]
    if meta.get("no_mmap", DEFAULTS["no_mmap"]):
        cmd += ["--no-mmap"]
    if meta.get("parallel"):
        cmd += ["--parallel", str(meta["parallel"])]
    if meta.get("ubatch_size"):
        cmd += ["--ubatch-size", str(meta["ubatch_size"])]
    # Enable Prometheus metrics endpoint for performance monitoring
    cmd += ["--metrics"]

    log.info(f"Build command for {model}: {cmd}")
    return cmd
