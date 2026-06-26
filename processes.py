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
                            ipaddress.IPv4Address(ip)
                            if ipaddress.IPv4Address(ip).is_private:
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
            # Fallback: try 127.0.0.1 which may work if Windows has loopback enabled
            log.warning("WSL+Windows detected but could not determine host IP; falling back to 127.0.0.1")
            host = "127.0.0.1"

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
                if "successful" in result.stdout.lower() or result.returncode == 0:
                    return True
            # Fallback: kill all llama-server processes
            log.info(f"Fallback: killing all llama-server processes")
            result = subprocess.run(
                [cmd_path, "/c", "taskkill /F /IM llama-server.exe 2>&1"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            log.info(f"Kill all result: stdout={result.stdout.strip()}")
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
        # Try the Windows host IP first, then localhost as fallback
        wsl_host = _get_wsl_host_ip()
        if wsl_host:
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
        # Try the Windows host IP first, then localhost as fallback
        wsl_host = _get_wsl_host_ip()
        if wsl_host:
            hosts_to_try.append(wsl_host)
        hosts_to_try.append("127.0.0.1")
    else:
        hosts_to_try.append(get_server_host())

    for host in hosts_to_try:
        url = f"http://{host}:{port}/v1/models"
        try:
            resp = requests.get(url, timeout=0.5)
            if resp.status_code == 200:
                return True
        except Exception:
            continue

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
