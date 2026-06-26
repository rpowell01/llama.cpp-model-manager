#!/usr/bin/env python3
"""
Process lifecycle management for llama-server.

This module provides functions to find, start, stop, and check the status of llama-server processes.

NOTE: This module is for LOCAL mode only. In remote mode, use the remote_client module
or ModelRegistry with a remote config.
"""

from __future__ import annotations

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
    # On WSL with Windows binary, use PowerShell to find the PID
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        try:
            ps_bin = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
            # Use ss/netstat on Linux to find the port, then PowerShell to verify
            # First try ss/lsof
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
            # Try netstat as fallback
            netstat = shutil.which("netstat")
            if netstat:
                try:
                    out = subprocess.check_output(
                        [netstat, "-plant"],
                        stderr=subprocess.DEVNULL,
                        text=True,
                        timeout=5,
                    )
                    for line in out.splitlines()[1:]:
                        parts = line.split()
                        if len(parts) >= 4:
                            local = parts[1]
                            if local.endswith(f":{port}"):
                                candidate = parts[-1]
                                if candidate.isdigit():
                                    return int(candidate)
                except Exception:
                    pass
        except Exception:
            pass

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
    # netstat fallback (Windows / Linux)
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
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError as e:
        log.debug(f"Port check for {port} failed: {e}")
        return False


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def _check_model_ready(port: int) -> bool:
    """Check if the model at the given port is ready to serve requests by hitting /v1/models."""
    _validate_local_mode()
    host = DEFAULTS["host"]
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
    cmd: list[str] = [
        str(LLAMA_SERVER),
        "-m",
        convert_path_for_binary(BASE_DIR / meta["model_path"]),
        "--alias",
        model,
        "--host",
        DEFAULTS["host"],
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
