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
import threading
import time
from pathlib import Path
from typing import Any

import requests

import webapp.config as config
from webapp.config import (
    BASE_DIR,
    DEFAULTS,
    WINDOWS,
    WSL,
    convert_path_for_binary,
    resolve_model_path,
    get_platform_backend_dir,
)
from webapp.logit_bias import resolve_overthinking_logit_bias
from webapp.models import InvalidModeError

log = logging.getLogger("llamaweb.processes")

# Cache for WSL host IP to avoid repeated log messages
_wsl_host_cache: str | None = None
_wsl_host_logged: bool = False

# Cache for Intel environment variables
_intel_env_cache: dict[str, str] | None = None


def _get_intel_env() -> dict[str, str]:
    """
    Get Intel oneAPI environment variables needed for SYCL/OpenVINO builds.
    Returns a dict of environment variables (LD_LIBRARY_PATH, etc.) or empty dict if not available.
    Caches the result to avoid repeated shell invocations.
    """
    global _intel_env_cache
    if _intel_env_cache is not None:
        return _intel_env_cache
    
    _intel_env_cache = {}
    
    # Check if Intel oneAPI is installed
    setvars_path = "/opt/intel/oneapi/setvars.sh"
    if not os.path.exists(setvars_path):
        return _intel_env_cache
    
    try:
        # Source the Intel environment and capture the variables
        result = subprocess.run(
            ["bash", "-c", f"source {setvars_path} --force 2>/dev/null && env"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    # Only capture relevant Intel variables
                    if key in ("LD_LIBRARY_PATH", "LIBRARY_PATH", "CPATH", "PKG_CONFIG_PATH", 
                               "INTEL_LICENSE_FILE", "ONEAPI_ROOT", "CL_CONFIG_CPU_FORCE_PRIVATE_MEM_SIZE"):
                        _intel_env_cache[key] = value
    except Exception:
        pass  # Ignore errors, return empty dict
    
    # Detect Intel Vulkan ICD for Vulkan backend support
    # Intel Vulkan ICD is typically at /usr/share/vulkan/icd.d/intel_icd.json
    # or /usr/share/vulkan/icd.d/intel_hasvk_icd.json (for Hasvk)
    intel_vulkan_icd_paths = [
        "/usr/share/vulkan/icd.d/intel_icd.json",
        "/usr/share/vulkan/icd.d/intel_hasvk_icd.json",
    ]
    for icd_path in intel_vulkan_icd_paths:
        if os.path.exists(icd_path):
            # Check if the ICD is for Intel GPU
            try:
                import json
                with open(icd_path, 'r') as f:
                    icd_data = json.load(f)
                    if "ICD" in icd_data and "library_path" in icd_data["ICD"]:
                        library_path = icd_data["ICD"]["library_path"]
                        if "intel" in library_path.lower() or "hasvk" in library_path.lower():
                            _intel_env_cache["VK_ICD_FILENAMES"] = icd_path
                            break
            except Exception:
                pass
    
    return _intel_env_cache


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
    
    # Method 1: Default gateway (standard WSL2 NAT mode).
    # In standard WSL2 the Windows host is the default gateway, and the
    # Windows llama-server (bound to 0.0.0.0) is reachable from WSL there.
    # This is the correct host for the common NAT configuration.
    gateway = _get_default_gateway()
    if gateway:
        return gateway

    # Method 2: Try WSL interface IP (shared networking mode).
    # In shared networking mode Windows and WSL share the same IP, so the
    # WSL interface IP is also the Windows host IP. Only valid in that mode,
    # so it is tried after the gateway (standard WSL2).
    wsl_ip = _get_wsl_interface_ip()
    if wsl_ip:
        return wsl_ip

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


def get_server_host(platform: str = "auto") -> str:
    """
    Determine which host the client should use to connect to the server.
    For WSL2 running a Windows llama-server.exe, the server binds on the Windows
    host's IP (obtained from resolv.conf) and must be reachable from WSL. For all
    other configurations, use 127.0.0.1 for client connections (0.0.0.0 is a bind
    address, not a valid connect address).
    
    Args:
        platform: "windows", "linux", or "auto". When "auto", the global
                  config.LLAMA_SERVER is used to detect WSL+Windows mode.
    
    Note: The host is resolved on each call to handle network changes (e.g., DHCP
    lease renewal in WSL+Windows mode) that could result in stale cached IPs.
    """
    global _wsl_host_cache, _wsl_host_logged
    
    # When running in WSL with a Windows binary, the server runs on Windows
    # and listens on the Windows host IP. We need to connect to that IP.
    if platform == "auto":
        platform = "windows" if (WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe')) else "linux"
    is_wsl_windows = WSL and platform == "windows"
    
    if is_wsl_windows:
        wsl_host = _get_wsl_host_ip()
        if wsl_host:
            if not _wsl_host_logged:
                log.info(f"WSL+Windows detected: using host {wsl_host} for client connections")
                _wsl_host_logged = True
            return wsl_host
        else:
            # Fallback: try 127.0.0.1 which may work if Windows has loopback enabled
            if not _wsl_host_logged:
                log.warning("WSL+Windows detected but could not determine host IP; falling back to 127.0.0.1")
                _wsl_host_logged = True
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
def find_pid_by_port(port: int, platform: str = "auto") -> int | None:
    """Find PID of process listening on the given port.

    Args:
        port: Port number to check
        platform: "windows", "linux", or "auto".

    "auto" checks **both** namespaces and returns whichever one actually has a
    listener. This matters because a model's configured platform can be changed
    while it is stopped, so the process still holding the port may live in the
    namespace of the *previous* configuration. Resolving only the configured
    namespace would miss it and report the stop as successful without killing
    anything.
    """
    _validate_local_mode()

    if platform == "windows":
        return _find_pid_by_port_wsl(port)
    if platform == "linux":
        return _find_pid_by_port_unix(port)

    for finder in (_find_pid_by_port_wsl, _find_pid_by_port_unix):
        try:
            pid = finder(port)
        except Exception:
            pid = None
        if pid:
            return pid
    return None


def find_pids_by_port(port: int) -> list[tuple[int, str]]:
    """Find every PID listening on a port, across both namespaces.

    Returns:
        List of (pid, platform) tuples where platform is "windows" or "linux".
    """
    _validate_local_mode()
    found: list[tuple[int, str]] = []
    for platform, finder in (("windows", _find_pid_by_port_wsl), ("linux", _find_pid_by_port_unix)):
        try:
            pid = finder(port)
        except Exception:
            pid = None
        if pid:
            found.append((pid, platform))
    return found


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
    """Find PIDs of all llama-server processes, in both namespaces.

    On WSL this deliberately checks the Linux *and* the Windows process tables,
    because a leftover llama-server may belong to either the current or the
    previous platform configuration.
    """
    _validate_local_mode()
    pids: list[int] = []

    # Windows processes (only meaningful under WSL)
    if WSL:
        try:
            cmd_path = "/mnt/c/Windows/System32/cmd.exe"
            if os.path.exists(cmd_path):
                result = subprocess.run(
                    [cmd_path, "/c", "tasklist /FO CSV /NH 2>&1"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                for line in result.stdout.strip().splitlines():
                    parts = [p.strip('"') for p in line.split(",")]
                    if len(parts) >= 2 and "llama-server" in parts[0].lower():
                        try:
                            pids.append(int(parts[1]))
                        except ValueError:
                            pass
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


def _get_model_platform(model: str = "") -> str:
    """Resolve a model's llama_cpp_platform from models.json ("windows"/"linux"/"auto")."""
    if not model:
        return "auto"
    try:
        from webapp.models import get_models
        meta = get_models().get(model)
        if meta:
            return meta.get("llama_cpp_platform", "auto") or "auto"
    except Exception:
        pass
    return "auto"


def _is_windows_process(pid: int, model: str = "") -> bool:
    """Determine whether a PID refers to a Windows (not Linux) process.

    Detection order:
      1. Explicit platform from the model's config (most reliable on WSL).
      2. Membership in _wsl_pids (stores Windows PIDs for WSL+Windows models).
      3. /proc/<pid>/exe path heuristic (Windows exes appear under /mnt/c).
    """
    if not WSL:
        return False

    if model and _get_model_platform(model) == "windows":
        return True

    try:
        from webapp.state import _wsl_pids, _wsl_pids_lock
        with _wsl_pids_lock:
            if pid in _wsl_pids.values():
                return True
    except ImportError:
        pass

    try:
        exe_str = str(Path(f"/proc/{pid}/exe").resolve())
        if exe_str.startswith("/mnt/c/") and exe_str.endswith(".exe"):
            return True
    except (OSError, ProcessLookupError):
        pass  # Not a Linux-visible PID: typically a Windows PID

    return False


def _windows_process_alive(pid: int) -> bool:
    """Check whether a Windows PID is still running (via tasklist)."""
    cmd_path = "/mnt/c/Windows/System32/cmd.exe"
    if not os.path.exists(cmd_path):
        return False
    try:
        result = subprocess.run(
            [cmd_path, "/c", f'tasklist /FI "PID eq {pid}" /NH'],
            capture_output=True, text=True, timeout=10,
        )
        out = result.stdout.strip()
        if not out or "INFO:" in out or "No tasks" in out:
            return False
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) == pid:
                return True
        return False
    except Exception:
        return False


def _wait_windows_process_gone(pid: int, timeout: float = 10.0,
                               interval: float = 0.5) -> bool:
    """Poll until a Windows PID no longer exists.

    taskkill returns before the process has actually exited; a llama-server
    holding a large mlock'd model can take a long time to release its memory
    and close its listening socket.

    Returns:
        True if the process disappeared within the timeout.
    """
    deadline = time.monotonic() + timeout
    while True:
        if not _windows_process_alive(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _linux_process_alive(pid: int) -> bool:
    """Check whether a Linux PID is still running."""
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def _kill_tree(pid: int, model: str = "", platform: str = "auto") -> bool:
    """Kill a process and all its children. Returns True only if it is really gone.

    Args:
        pid: Process ID (a Linux PID, or a Windows PID when platform == "windows")
        model: Model name, used to resolve the platform when not given explicitly
        platform: "windows", "linux", or "auto" (auto-detected)
    """
    _validate_local_mode()
    if platform == "auto":
        platform = _get_model_platform(model)
    is_windows_process = (platform == "windows") or (WSL and _is_windows_process(pid, model))

    try:
        if is_windows_process:
            log.info(f"Killing Windows process {pid} via taskkill (model={model}, platform={platform})")
            cmd_path = "/mnt/c/Windows/System32/cmd.exe"
            if not os.path.exists(cmd_path):
                log.error("cmd.exe not found; cannot kill Windows process")
                return False

            commands = [
                f"taskkill /F /T /PID {pid}",
                f"taskkill /F /PID {pid}",
                "taskkill /F /IM llama-server.exe",
            ]
            for attempt, command in enumerate(commands):
                try:
                    result = subprocess.run(
                        [cmd_path, "/c", command + " 2>&1"],
                        capture_output=True, text=True, timeout=15,
                    )
                    log.info(
                        f"taskkill attempt {attempt + 1} for pid={pid}: "
                        f"rc={result.returncode}, out={result.stdout.strip()}"
                    )
                except Exception as e:
                    log.warning(f"taskkill attempt {attempt + 1} failed: {e}")

                # taskkill reports success before the process has fully exited.
                # A llama-server holding a large mlock'd model can take many
                # seconds to tear down, so poll until the PID really disappears.
                if _wait_windows_process_gone(pid, timeout=10.0):
                    log.info(f"Windows process {pid} confirmed gone after attempt {attempt + 1}")
                    return True

            return not _windows_process_alive(pid)

        if WINDOWS:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0

        # Native Linux: kill only this process and its children (never the
        # process group, which would take down the Flask web server).
        import psutil
        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return True
        children = parent.children(recursive=True)
        for p in children + [parent]:
            try:
                p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        gone, alive = psutil.wait_procs(children + [parent], timeout=3)
        for p in alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if alive:
            psutil.wait_procs(alive, timeout=2)
        return not _linux_process_alive(pid)
    except ImportError:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                return True
            return not _linux_process_alive(pid)
        except (OSError, ProcessLookupError):
            return True  # Already dead
    except Exception as e:
        log.error(f"Failed to kill process {pid}: {e}")
        return False


def _port_has_listener(port: int, platform: str = "auto") -> bool:
    """Authoritative check for whether something is still listening on a port.

    Uses netstat on the Windows side and ss/lsof on the Linux side, which is far
    more reliable than a socket connect across the WSL/Windows boundary (a
    terminating process can leave the socket bound while refusing connections,
    and WSL port-forwarding can report transient failures).

    With platform="auto" BOTH namespaces are checked, because the process still
    holding the port may belong to the previous platform configuration.

    Args:
        port: Port number.
        platform: "windows", "linux", or "auto" (both).
    """
    # Port 0 is invalid - no process can listen on it
    if not port or port <= 0 or port > 65535:
        return False
    
    if WSL:
        if platform == "windows":
            return _windows_port_listener_pid(port) is not None
        if platform == "linux":
            return _find_pid_by_port_unix(port) is not None
        # auto: a listener in either namespace blocks the port
        if _windows_port_listener_pid(port) is not None:
            return True
        if _find_pid_by_port_unix(port) is not None:
            return True
        # Fall through to the socket check if both namespace probes failed
        # (e.g. cmd.exe unavailable) so we never report "free" incorrectly.
    return check_port_active(port, platform)


def _windows_port_listener_pid(port: int) -> int | None:
    """Return the Windows PID listening on a port, or None."""
    cmd_path = "/mnt/c/Windows/System32/cmd.exe"
    if not os.path.exists(cmd_path):
        return None
    try:
        result = subprocess.run(
            [cmd_path, "/c", f"netstat -ano 2>&1 | findstr :{port}"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if "LISTENING" not in line.upper():
                continue
            parts = line.split()
            # "TCP 0.0.0.0:PORT 0.0.0.0:0 LISTENING PID"
            if len(parts) >= 5 and parts[-1].isdigit():
                return int(parts[-1])
        return None
    except Exception:
        return None


def wait_port_free(port: int, platform: str = "auto", timeout: float = 15.0,
                   interval: float = 0.25) -> bool:
    """Wait until nothing is listening on a port.

    A llama-server holding a large mlock'd model can take several seconds to
    release its socket after being killed, so callers must poll rather than
    sampling once.

    Returns:
        True if the port became free within the timeout.
    """
    deadline = time.monotonic() + timeout
    while True:
        if not _port_has_listener(port, platform):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def check_port_active(port: int, platform: str = "auto") -> bool:
    """Check if a port is active (has a listener)."""
    _validate_local_mode()
    import socket

    # Determine hosts to try - in WSL+Windows mode, try multiple options
    hosts_to_try = []
    is_wsl_windows = WSL and (
        platform == "windows"
        or (platform == "auto" and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'))
    )
    if is_wsl_windows:
        # In WSL+Windows mode, the server binds to 0.0.0.0 on Windows
        # Try the Windows host IP first (via get_server_host), then localhost as fallback
        wsl_host = get_server_host()
        if wsl_host and wsl_host != "127.0.0.1":
            hosts_to_try.append(wsl_host)
        hosts_to_try.append("127.0.0.1")
    else:
        hosts_to_try.append(get_server_host())

    log.debug(f"check_port_active({port}): hosts_to_try={hosts_to_try}")

    # Use longer timeout for WSL environments
    timeout = 0.5 if WSL else 0.2

    for host in hosts_to_try:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                log.debug(f"Port {port} active on {host}")
                return True
        except (OSError, socket.gaierror) as e:
            log.debug(f"Port check for {port} on {host} failed: {e}")
            continue

    return False


# Cache for model list per port to avoid repeated HTTP requests
_model_list_cache: dict[int, tuple[float, list[str]]] = {}
_model_list_cache_lock = threading.Lock()
_MODEL_LIST_CACHE_TTL = 2.0  # seconds

# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def _check_model_ready(port: int, model_name: str | None = None) -> bool:
    """Check if the model at the given port is ready to serve requests by hitting /v1/models.
    
    If model_name is provided, verifies that the specific model is loaded on that port.
    Uses caching to avoid repeated HTTP requests for the same port.
    """
    _validate_local_mode()
    
    # Resolve the model's platform up front so every check below uses the right
    # process namespace (Windows PIDs/netstat vs. Linux sockets).
    platform = _get_model_platform(model_name or "")

    # First, do a fast port check - if nothing is listening, no model can be ready.
    # For WSL+Windows models this uses netstat on the Windows side, which is far
    # more reliable than a socket connect across the WSL/Windows boundary.
    if not _port_has_listener(port, platform):
        return False
    
    # If no specific model name requested, just return True since port is active
    if model_name is None:
        return True
    
    # Check cache first
    import time
    current_time = time.time()
    with _model_list_cache_lock:
        if port in _model_list_cache:
            cached_time, model_list = _model_list_cache[port]
            if current_time - cached_time < _MODEL_LIST_CACHE_TTL:
                return model_name in model_list
    
    # Determine hosts to try - in WSL+Windows mode, try multiple options
    hosts_to_try = []
    is_wsl_windows = WSL and (
        platform == "windows"
        or (platform == "auto" and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'))
    )
    if is_wsl_windows:
        wsl_host = get_server_host("windows")
        if wsl_host and wsl_host != "127.0.0.1":
            hosts_to_try.append(wsl_host)
        hosts_to_try.append("127.0.0.1")
    else:
        hosts_to_try.append(get_server_host(platform))

    for host in hosts_to_try:
        url = f"http://{host}:{port}/v1/models"
        try:
            # Short timeout for health check - we already know port is active
            resp = requests.get(url, timeout=2.0)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    models = data.get("data", [])
                    model_list = [m.get("id") for m in models if m.get("id")]
                    # Update cache
                    with _model_list_cache_lock:
                        _model_list_cache[port] = (current_time, model_list)
                    return model_name in model_list
                except Exception:
                    # If we can't parse JSON, fall back to just checking status code
                    with _model_list_cache_lock:
                        _model_list_cache[port] = (current_time, [])
                    return False
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
    if WSL and config.LLAMA_SERVER and str(config.LLAMA_SERVER).endswith('.exe'):
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


def _find_llama_tokenizer(llama_server_binary: Path) -> Path:
    """Find the tokenizer companion from the same llama.cpp installation."""
    executable = "llama-tokenize.exe" if llama_server_binary.suffix.lower() == ".exe" else "llama-tokenize"
    tokenizer = llama_server_binary.with_name(executable)
    if not tokenizer.is_file():
        raise RuntimeError(
            f"Overthinking penalty requires {executable} beside {llama_server_binary.name}; "
            f"expected it at {tokenizer}"
        )
    return tokenizer


def _persist_overthinking_resolution(model: str, meta: dict[str, Any]) -> None:
    """Persist resolved IDs and the display value without replacing user settings."""
    from webapp.models import get_models, save_models

    models = get_models()
    model_key = next((key for key in models if key.lower() == model.lower()), None)
    if model_key is None:
        return
    stored = models[model_key]
    changed = False
    for field in ("logit_bias", "overthinking_penalty_cache"):
        if stored.get(field) != meta.get(field):
            stored[field] = meta.get(field)
            changed = True
    if changed:
        save_models(models)


# REMOTE_MODE: This function is for LOCAL mode only.
# In remote mode, process lifecycle is managed by the remote llama.cpp server.
def build_cmd(model: str, port: int | str, meta: dict[str, Any]) -> list[str]:
    """Build the command for starting a model."""
    _validate_local_mode()
    
    from webapp.config import LLAMA_SERVER_TYPE, LLAMA_SERVER_USES_SERVE, WSL, get_platform_backend_dir, convert_path_for_binary, DEFAULTS
    
    # Get model's platform and backend selection (default to "auto" for backward compatibility)
    llama_cpp_platform = meta.get("llama_cpp_platform", "auto")
    llama_cpp_backend = meta.get("llama_cpp_backend", "auto")
    
    # Determine the llama-server directory based on platform/backend selection
    # If "auto", fall back to global auto-detection (LLAMA_SERVER)
    llama_server_dir = ""
    if llama_cpp_platform != "auto" and llama_cpp_backend != "auto":
        llama_server_dir = get_platform_backend_dir(llama_cpp_platform, llama_cpp_backend)
    
    # Find the appropriate binary in the selected directory
    llama_server_binary = None
    uses_serve_subcommand = False
    
    if llama_server_dir:
        # Use the configured directory for this platform/backend combination
        if WSL and llama_cpp_platform == "windows":
            # In WSL, convert Windows path (C:/...) to WSL path (/mnt/c/...)
            # so we can check if the binary exists in the WSL filesystem
            llama_server_dir_wsl = llama_server_dir.replace("C:/", "/mnt/c/").replace("/", "/")
            server_dir = Path(llama_server_dir_wsl)
        else:
            server_dir = Path(llama_server_dir)
        
        if llama_cpp_platform == "windows":
            # Windows binary: llama-server.exe
            # In WSL, check for the binary at the converted path
            if WSL:
                candidate = server_dir / "llama-server.exe"
                if candidate.exists():
                    llama_server_binary = candidate
            else:
                candidate = server_dir / "llama-server.exe"
                if candidate.exists():
                    llama_server_binary = candidate
                else:
                    candidate = server_dir / "llama-server"
                    if candidate.exists():
                        llama_server_binary = candidate
        else:  # linux
            # Linux binary: llama-server (new SYCL) or llama (old with serve subcommand)
            candidate = server_dir / "llama-server"
            if candidate.exists():
                llama_server_binary = candidate
                uses_serve_subcommand = False
            else:
                candidate = server_dir / "llama"
                if candidate.exists():
                    llama_server_binary = candidate
                    uses_serve_subcommand = True
    
    # Fall back to global auto-detection if no specific directory configured or binary not found
    if llama_server_binary is None:
        from webapp.config import LLAMA_SERVER, LLAMA_SERVER_TYPE, LLAMA_SERVER_USES_SERVE
        llama_server_binary = config.LLAMA_SERVER
        uses_serve_subcommand = LLAMA_SERVER_USES_SERVE
        llama_cpp_platform = LLAMA_SERVER_TYPE or "auto"
    
    # Determine binary type for bind host logic
    is_windows_binary = llama_cpp_platform == "windows"
    is_linux_binary = llama_cpp_platform == "linux"
    
    # Check if we're using Linux SYCL backend (requires Intel oneAPI wrapper)
    is_linux_sycl = (llama_cpp_backend == "sycl" and llama_cpp_platform == "linux")
    
    # Determine bind host:
    #  - WSL + Windows binary: bind 0.0.0.0 so the Windows host accepts
    #    connections coming from WSL.
    #  - WSL + Linux binary: bind 127.0.0.1 only. Binding 0.0.0.0 makes WSL2
    #    register a port-forwarding rule that RESERVES the port on the Windows
    #    side, and that reservation outlives the process. It then blocks any
    #    later Windows llama-server.exe from binding the same port, which is
    #    exactly the "couldn't bind HTTP server socket" seen when switching a
    #    model from linux to windows. The Flask app and mnemonic-proxy both run
    #    inside WSL, so 127.0.0.1 is sufficient.
    #  - Otherwise: use DEFAULTS["host"] (0.0.0.0) unless overridden by model config
    if "bind_host" in meta:
        bind_host = meta["bind_host"]
    elif WSL and is_windows_binary:
        bind_host = "0.0.0.0"
    elif WSL and is_linux_binary:
        bind_host = "127.0.0.1"
    else:
        bind_host = DEFAULTS["host"]

    # Handle both relative and absolute model paths using helper (respects MODEL_DIR env var)
    model_path_str = meta.get("model_path", "")
    model_path = resolve_model_path(model_path_str)

    # Determine if we're using a Windows binary for path conversion
    is_windows_binary_for_paths = is_windows_binary

    # Build command based on binary type
    if uses_serve_subcommand:
        # Linux llama binary uses "llama serve" subcommand (old llama.cpp)
        cmd: list[str] = [
            str(llama_server_binary),
            "serve",
            "-m",
            convert_path_for_binary(model_path, is_windows_binary_for_paths),
            "--alias",
            model,
            "--host",
            bind_host,
            "--port",
            str(port),
            "--reuse-port" if meta.get("reuse_port", DEFAULTS["reuse_port"]) else "--no-reuse-port",
            "--threads",
            str(meta.get("threads", DEFAULTS["threads"])),
            "--threads-batch",
            str(meta.get("threads_batch", DEFAULTS["threads_batch"])),
            "--timeout",
            str(meta.get("timeout", DEFAULTS["timeout"])),
        ]
    else:
        # Windows llama-server.exe or Linux llama-server (new SYCL version)
        cmd: list[str] = [
            str(llama_server_binary),
            "-m",
            convert_path_for_binary(model_path, is_windows_binary_for_paths),
            "--alias",
            model,
            "--host",
            bind_host,
            "--port",
            str(port),
            "--reuse-port" if meta.get("reuse_port", DEFAULTS["reuse_port"]) else "--no-reuse-port",
            "--threads",
            str(meta.get("threads", DEFAULTS["threads"])),
            "--threads-batch",
            str(meta.get("threads_batch", DEFAULTS["threads_batch"])),
            "--timeout",
            str(meta.get("timeout", DEFAULTS["timeout"])),
        ]
    
    # For Linux SYCL, prepend the wrapper script to set up Intel oneAPI environment
    if is_linux_sycl:
        wrapper_path = BASE_DIR / "webapp" / "scripts" / "llama-server-sycl-wrapper.sh"
        if wrapper_path.exists():
            # Replace the binary path with the wrapper, passing original binary as first arg
            cmd = [str(wrapper_path), str(llama_server_binary)] + cmd[1:]
            log.info(f"Using SYCL wrapper for Linux SYCL backend: {wrapper_path}")
        else:
            log.warning(f"SYCL wrapper not found at {wrapper_path}, running without Intel oneAPI env")
    
    # WSL workaround: systemd-run wrapper removed - doesn't work with Linux binary in WSL
    # The Linux binary in WSL has memory allocation issues that systemd-run cannot resolve
    # Use Windows binary for WSL instead (default behavior)
    if False and WSL and is_linux_binary and not uses_serve_subcommand:
        pass
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

    # Log verbosity
    log_verbosity = meta.get("log_verbosity")
    if log_verbosity is not None:
        cmd += ["--log-verbosity", str(log_verbosity)]
    
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
    
    # Generation / Sampling parameters (only include if use_generation_params is True)
    use_gen_params = meta.get("use_generation_params", DEFAULTS["use_generation_params"])
    if use_gen_params:
        cmd += ["--temp", str(meta.get("temperature", DEFAULTS["temperature"]))]
        cmd += ["--top-k", str(meta.get("top_k", DEFAULTS["top_k"]))]
        cmd += ["--top-p", str(meta.get("top_p", DEFAULTS["top_p"]))]
        cmd += ["--min-p", str(meta.get("min_p", DEFAULTS["min_p"]))]
        cmd += ["--repeat-penalty", str(meta.get("repeat_penalty", DEFAULTS["repeat_penalty"]))]
        cmd += ["--presence-penalty", str(meta.get("presence_penalty", DEFAULTS["presence_penalty"]))]
        
        reasoning = meta.get("reasoning", DEFAULTS["reasoning"])
        if reasoning:
            cmd += ["--reasoning", reasoning]
        
        reasoning_effort = meta.get("reasoning_effort", DEFAULTS["reasoning_effort"])
        if reasoning_effort:
            cmd += ["--reasoning-effort", reasoning_effort]
        
        reasoning_budget = meta.get("reasoning_budget", DEFAULTS["reasoning_budget"])
        if reasoning_budget is not None:
            cmd += ["--reasoning-budget", str(reasoning_budget)]
        
        reasoning_budget_message = meta.get("reasoning_budget_message", DEFAULTS["reasoning_budget_message"])
        if reasoning_budget_message:
            cmd += ["--reasoning-budget-message", reasoning_budget_message]

    # Overthinking penalty: resolve each exact entry independently with the
    # tokenizer from the selected llama.cpp build. Token/bias pairs are
    # emitted as a single --logit-bias flag with comma-separated values.
    # llama.cpp expects: --logit-bias TOKEN_ID±BIAS,TOKEN_ID±BIAS,...
    if meta.get("overthinking_penalty_enabled", DEFAULTS["overthinking_penalty_enabled"]):
        tokenizer_path = _find_llama_tokenizer(Path(llama_server_binary))
        logit_bias, skipped = resolve_overthinking_logit_bias(
            meta,
            model_path,
            convert_path_for_binary(model_path, is_windows_binary_for_paths),
            tokenizer_path,
        )
        if skipped:
            log.warning(
                "Skipping overthinking penalty entries that do not resolve to one token for %s: %s",
                model,
                ", ".join(f"{word!r} ({len(tokens)} tokens)" for word, tokens in skipped.items()),
            )
        # logit_bias is a single --logit-bias flag with comma-separated values
        if logit_bias:
            cmd += logit_bias.split()
        _persist_overthinking_resolution(model, meta)
    
    # CPU-only optimizations
    gpu_layers = meta.get("gpu_layers", DEFAULTS["gpu_layers"])
    
    # Device selection (for SYCL/GPU backends) - if device=none, force gpu_layers=0
    device = meta.get("device", DEFAULTS["device"])
    if device == "none":
        gpu_layers = 0
    
    if gpu_layers is not None:
        cmd += ["-ngl", str(gpu_layers)]
    
    # Add --device none when using CPU-only mode
    if device and gpu_layers == 0:
        cmd += ["--device", device]
    
    # MoE CPU offloading
    cpu_moe = meta.get("cpu_moe", DEFAULTS["cpu_moe"])
    n_cpu_moe = meta.get("n_cpu_moe", DEFAULTS["n_cpu_moe"])
    moe_cache_mib = meta.get("moe_cache_mib", DEFAULTS["moe_cache_mib"])
    if cpu_moe:
        # --cpu-moe means offload ALL MoE experts to CPU
        # Mutually exclusive with --n-cpu-moe
        cmd += ["--cpu-moe"]
    elif n_cpu_moe and n_cpu_moe > 0:
        # --n-cpu-moe N means offload N MoE experts to CPU
        # Only used when cpu_moe is false
        cmd += ["--n-cpu-moe", str(n_cpu_moe)]
    # --moe-cache-mib N: GPU cache size in MiB for MoE experts kept in CPU
    # Only used when n_cpu_moe > 0 or cpu_moe is true
    if moe_cache_mib and moe_cache_mib > 0:
        cmd += ["--moe-cache-mib", str(moe_cache_mib)]
    
    cache_type_k = meta.get("cache_type_k", DEFAULTS["cache_type_k"])
    if cache_type_k and cache_type_k != "f16":
        cmd += ["--cache-type-k", cache_type_k]
    
    cache_type_v = meta.get("cache_type_v", DEFAULTS["cache_type_v"])
    if cache_type_v and cache_type_v != "f16":
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
        # Convert path for Windows binary if running in WSL (same as model path)
        slot_save_path = convert_path_for_binary(Path(slot_save_path), is_windows_binary_for_paths)
        cmd += ["--slot-save-path", slot_save_path]
    
    # Chat template file
    chat_template_file = meta.get("chat_template_file", DEFAULTS["chat_template_file"])
    if chat_template_file:
        cmd += ["--chat-template-file", chat_template_file]
    
    # Multimodal projector parameters
    mmproj = meta.get("mmproj", DEFAULTS["mmproj"])
    if mmproj:
        cmd += ["--mmproj", convert_path_for_binary(mmproj, is_windows_binary_for_paths)]
        
        mmproj_auto = meta.get("mmproj_auto", DEFAULTS["mmproj_auto"])
        if mmproj_auto:
            cmd += ["--mmproj-auto"]
        else:
            cmd += ["--no-mmproj-auto"]
        
        mmproj_offload = meta.get("mmproj_offload", DEFAULTS["mmproj_offload"])
        if mmproj_offload:
            cmd += ["--mmproj-offload"]
        else:
            cmd += ["--no-mmproj-offload"]
    
    cache_reuse = meta.get("cache_reuse", DEFAULTS["cache_reuse"])
    if cache_reuse:
        cmd += ["--cache-reuse", str(cache_reuse)]
    
    # --keep parameter: number of tokens to keep from the initial prompt
    # -1 means keep all tokens, 0 means disabled (default), positive N means keep N tokens
    keep = meta.get("keep", DEFAULTS["keep"])
    if keep is not None and keep != 0:
        cmd += ["--keep", str(keep)]
    
    # Enable --swa-full only if explicitly requested (for sliding window attention models)
    # The flag is independent of slot_save_path and cache_reuse
    slot_save_path = meta.get("slot_save_path", DEFAULTS["slot_save_path"])
    use_swa_full = meta.get("swa_full", False)
    if use_swa_full:
        cmd += ["--swa-full"]
    
    batch_size = meta.get("batch_size", DEFAULTS["batch_size"])
    if batch_size:
        cmd += ["-b", str(batch_size)]
    
    # ctx_size uses -c (context size) - this also controls KV cache size.
    # llama.cpp expects -c in TOKENS, which is the unit used by models.json.
    ctx_size = meta.get("ctx_size", DEFAULTS["ctx_size"])
    if ctx_size:
        cmd += ["-c", str(ctx_size)]
    
    
    # RoPE / YaRN scaling parameters.
    # llama.cpp only applies these when a RoPE scaling method is active, so when
    # the scaling type is "none" none of the other rope/yarn flags are emitted.
    rope_scaling_type = str(meta.get("rope_scaling_type", DEFAULTS["rope_scaling_type"]) or "").strip().lower()
    if rope_scaling_type and rope_scaling_type != "none":
        rope_freq_base = meta.get("rope_freq_base", DEFAULTS["rope_freq_base"])
        if rope_freq_base and rope_freq_base != 10000.0:
            cmd += ["--rope-freq-base", str(rope_freq_base)]

        rope_freq_scale = meta.get("rope_freq_scale", DEFAULTS["rope_freq_scale"])
        if rope_freq_scale and rope_freq_scale != 1.0:
            cmd += ["--rope-freq-scale", str(rope_freq_scale)]

        cmd += ["--rope-scaling", rope_scaling_type]

        rope_scale = meta.get("rope_scale", DEFAULTS["rope_scale"])
        if rope_scale and rope_scale != 1.0:
            cmd += ["--rope-scale", str(rope_scale)]

        yarn_orig_ctx = meta.get("yarn_orig_ctx", DEFAULTS["yarn_orig_ctx"])
        if yarn_orig_ctx and yarn_orig_ctx > 0:
            # llama.cpp expects --yarn-orig-ctx in TOKENS, matching models.json.
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

    # Warmup flag (llama.cpp --warmup / --no-warmup)
    if meta.get("warmup_enabled", DEFAULTS["warmup_enabled"]):
        cmd += ["--warmup"]
    else:
        cmd += ["--no-warmup"]

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
                cmd += ["--spec-draft-model", convert_path_for_binary(draft_path, is_windows_binary_for_paths)]
            
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
        
        # Ngram parameters - handle different ngram types separately
        # Check for specific ngram types
        has_ngram_simple = "ngram-simple" in spec_types
        has_ngram_mod = "ngram-mod" in spec_types
        has_ngram_map_k = "ngram-map-k" in spec_types
        has_ngram_map_k4v = "ngram-map-k4v" in spec_types
        has_ngram_cache = "ngram-cache" in spec_types
        
        # Ngram-simple parameters
        if has_ngram_simple:
            spec_ngram_size_n = meta.get("spec_ngram_size_n", DEFAULTS["spec_ngram_size_n"])
            if spec_ngram_size_n > 0:
                cmd += ["--spec-ngram-simple-size-n", str(spec_ngram_size_n)]
            
            spec_ngram_size_m = meta.get("spec_ngram_size_m", DEFAULTS["spec_ngram_size_m"])
            if spec_ngram_size_m > 0:
                cmd += ["--spec-ngram-simple-size-m", str(spec_ngram_size_m)]
            
            spec_ngram_min_hits = meta.get("spec_ngram_min_hits", DEFAULTS["spec_ngram_min_hits"])
            if spec_ngram_min_hits > 1:
                cmd += ["--spec-ngram-simple-min-hits", str(spec_ngram_min_hits)]
        
        # Ngram-mod parameters
        if has_ngram_mod:
            spec_ngram_mod_n_match = meta.get("spec_ngram_mod_n_match", DEFAULTS["spec_ngram_mod_n_match"])
            if spec_ngram_mod_n_match > 0:
                cmd += ["--spec-ngram-mod-n-match", str(spec_ngram_mod_n_match)]
            
            spec_ngram_mod_n_min = meta.get("spec_ngram_mod_n_min", DEFAULTS["spec_ngram_mod_n_min"])
            if spec_ngram_mod_n_min > 0:
                cmd += ["--spec-ngram-mod-n-min", str(spec_ngram_mod_n_min)]
            
            spec_ngram_mod_n_max = meta.get("spec_ngram_mod_n_max", DEFAULTS["spec_ngram_mod_n_max"])
            if spec_ngram_mod_n_max > 0:
                cmd += ["--spec-ngram-mod-n-max", str(spec_ngram_mod_n_max)]
        
        # Draft model KV cache type parameters (for draft-* types)
        if has_draft_type:
            spec_draft_type_k = meta.get("spec_draft_type_k", DEFAULTS["spec_draft_type_k"])
            if spec_draft_type_k and spec_draft_type_k != "f16":
                cmd += ["--spec-draft-type-k", spec_draft_type_k]
            
            spec_draft_type_v = meta.get("spec_draft_type_v", DEFAULTS["spec_draft_type_v"])
            if spec_draft_type_v and spec_draft_type_v != "f16":
                cmd += ["--spec-draft-type-v", spec_draft_type_v]

    # Custom model start parameter string
    custom_start_param = meta.get("custom_model_start_param") or ""
    if custom_start_param:
        # Split by whitespace and add each token to the command
        for param in custom_start_param.split():
            cmd.append(param)
    
    log.info(f"Build command for {model}: {cmd}")
    return cmd
