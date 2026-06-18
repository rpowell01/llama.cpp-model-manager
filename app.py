#!/usr/bin/env python3
"""
llama.cpp web frontend
----------------------
Lists local GGUF models, starts / stops the llama-server for each one,
and checks which are currently reachable on their ports.

Run:
    python app.py [--host 0.0.0.0] [--port 5000]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Generator
from flask import Flask, jsonify, render_template_string, request, Response
import requests  # For Hugging Face API calls

app = Flask(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
MODELS_JSON = BASE_DIR / "models.json"
LOG_FILE = BASE_DIR / "running_models.log"
ENV_FILE = BASE_DIR / ".env"

DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 11000,
    "cache_size": 32768,
    "threads": 16,
    "threads_batch": 22,
    "timeout": 3600,
    "mlock": True,
    "flash_attn": "off",
    "no_mmap": True,
    "parallel": 4,
    "ubatch_size": 512,
}

WINDOWS = platform.system() == "Windows"
# Check for WSL (Windows Subsystem for Linux) - must be done before LLAMA_SERVER assignment
WSL = False
if not WINDOWS:
    try:
        proc_version = Path("/proc/version")
        if proc_version.exists() and "microsoft" in proc_version.read_text().lower():
            WSL = True
    except Exception:
        pass



# Check for llama-server binary - prefer native binary for the platform
LLAMA_SERVER = None
if WINDOWS:
    candidate = BASE_DIR / "llama-server.exe"
    if candidate.exists():
        LLAMA_SERVER = candidate
    else:
        candidate = BASE_DIR / "llama-server"
        if candidate.exists():
            LLAMA_SERVER = candidate
elif WSL:
    # On WSL, prefer Windows binary for better compatibility
    candidate = BASE_DIR / "llama-server.exe"
    if candidate.exists():
        LLAMA_SERVER = candidate
    else:
        candidate = BASE_DIR / "llama-server"
        if candidate.exists():
            LLAMA_SERVER = candidate
else:
    # On Linux, check for native binary first, then Windows binary
    candidate = BASE_DIR / "llama-server"
    if candidate.exists():
        LLAMA_SERVER = candidate
    else:
        candidate = BASE_DIR / "llama-server.exe"
        if candidate.exists():
            LLAMA_SERVER = candidate

PYTHON = sys.executable

def convert_path_for_binary(path: Path) -> str:
    r"""Convert path for the binary being used.
    On Linux with Windows binary (WSL), convert /mnt/c/... to C:/...
    Windows APIs accept forward slashes, so we use C:/... format.
    """
    path_str = str(path)
    if LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe') and path_str.startswith('/mnt/'):
        # Convert /mnt/X/... to X:/...
        # Windows APIs accept forward slashes, avoiding backslash escaping issues
        parts = path_str.split('/')
        if len(parts) >= 3 and parts[1] == 'mnt':
            drive = parts[2].upper()
            # Use forward slashes - Windows APIs accept this format
            windows_path = drive + ':/' + '/'.join(parts[3:])
            return windows_path
    return path_str

# Download status tracking
download_status: Dict[str, Dict[str, Any]] = {}
download_lock = threading.Lock()

# Models cache
_models: dict[str, dict[str, Any]] = {}
_models_lock = threading.Lock()

# Track recent stop operations to avoid race conditions
_recent_stops: dict[str, float] = {}  # model_name -> timestamp
_recent_stops_lock = threading.Lock()

# Track PIDs for WSL models (since PowerShell can't see Windows process info from Linux)
_wsl_pids: dict[str, int] = {}  # model_name -> pid
_wsl_pids_lock = threading.Lock()

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(BASE_DIR / "webapp.log"), logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("llamaweb")
log.info(f"Platform: WINDOWS={WINDOWS}, WSL={WSL}")
log.info(f"LLAMA_SERVER: {LLAMA_SERVER}")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def load_env() -> dict[str, str]:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_models() -> dict[str, dict[str, Any]]:
    """Get cached models (thread-safe)."""
    with _models_lock:
        return _models.copy()

def load_models_cache() -> None:
    """Load models from disk into cache (called at startup and after modifications)."""
    global _models
    models = {}
    
    # Load from models.json
    if MODELS_JSON.exists():
        try:
            with open(MODELS_JSON, "r", encoding="utf-8") as fh:
                models = json.load(fh)
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"Failed to load models.json: {e}")
    
    # Merge with filesystem scan
    filesystem_models = scan_models_directory()
    for model_name, fs_meta in filesystem_models.items():
        # Case-insensitive lookup for existing model
        existing_key = None
        for key in models:
            if key.lower() == model_name.lower():
                existing_key = key
                break
        
        if existing_key is None:
            # New model from filesystem
            models[model_name] = fs_meta
        else:
            # Update existing model with filesystem info
            existing = models[existing_key]
            existing["file_exists"] = True
            existing["file_size_gb"] = fs_meta.get("file_size_gb")
            # Always use filesystem expected_size if available
            if fs_meta.get("expected_size"):
                existing["expected_size"] = fs_meta["expected_size"]
            # Ensure filename is set
            if fs_meta.get("filename"):
                existing["filename"] = fs_meta["filename"]
            # Ensure model_path is set
            if fs_meta.get("model_path"):
                existing["model_path"] = fs_meta["model_path"]
            # Ensure repo is set
            if existing.get("repo") is None:
                existing["repo"] = ""
    
    assign_ports(models)
    with _models_lock:
        _models = models

def refresh_models_cache() -> None:
    """Force refresh of the models cache."""
    load_models_cache()


def scan_models_directory() -> dict[str, dict[str, Any]]:
    """Scan the models directory and return a dictionary of models with their metadata."""
    models = {}
    if not MODELS_DIR.exists():
        return models
    
    for model_dir in sorted(MODELS_DIR.iterdir()):
        if not model_dir.is_dir():
            continue
        
        model_name = model_dir.name
        
        for model_file in model_dir.glob("*.gguf"):
            try:
                file_size = model_file.stat().st_size
                file_size_gb = round(file_size / (1024 ** 3), 2)
                
                models[model_name] = {
                    "model_path": str(model_file.relative_to(BASE_DIR)),
                    "repo": "",
                    "filename": model_file.name,
                    "expected_size": file_size,
                    "port": 0,
                    "cache_size": DEFAULTS["cache_size"],
                    "threads": DEFAULTS["threads"],
                    "threads_batch": DEFAULTS["threads_batch"],
                    "timeout": DEFAULTS["timeout"],
                    "mlock": DEFAULTS["mlock"],
                    "flash_attn": DEFAULTS["flash_attn"],
                    "no_mmap": DEFAULTS["no_mmap"],
                    "parallel": DEFAULTS["parallel"],
                    "ubatch_size": DEFAULTS["ubatch_size"],
                    "file_size_gb": file_size_gb,
                    "file_exists": True
                }
                break
            except OSError:
                continue
    
    return models


def assign_ports(models: dict[str, dict[str, Any]]) -> None:
    """Assign sequential ports to models starting from 11000, ensuring uniqueness."""
    sorted_names = sorted(models.keys())
    used_ports = set()
    # Collect already-assigned ports
    for model_name in sorted_names:
        port = models[model_name].get("port", 0)
        if port > 0:
            used_ports.add(port)
    # Assign unique ports only if needed
    for i, model_name in enumerate(sorted_names):
        port = models[model_name].get("port", 0)
        # Only reassign if port is invalid (<=0) or is a duplicate
        if port <= 0 or (port in used_ports and models[model_name].get("port") != port):
            # Find next available port
            new_port = 11000 + i * 10
            while new_port in used_ports:
                new_port += 10
            models[model_name]["port"] = new_port
            used_ports.add(new_port)


def save_models(models: dict[str, dict[str, Any]]) -> None:
    with open(MODELS_JSON, "w", encoding="utf-8") as fh:
        json.dump(models, fh, indent=2)
    # Also update the in-memory cache
    with _models_lock:
        global _models
        _models = models.copy()


def log_event(action: str, model: str, port: int | str, pid: int | str | None, status: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = json.dumps({"time": ts, "action": action, "model": model, "port": port, "pid": pid, "status": status})
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(entry + "\n")
    log.info(entry)


def find_pid_by_port(port: int) -> int | None:
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


def pids_of_llama() -> list[int]:
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


def _kill_tree(pid: int, model: str = "") -> bool:
    """Kill a process and all its children. Returns True if successful."""
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


def check_port_active(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError as e:
        log.debug(f"Port check for {port} failed: {e}")
        return False


def build_cmd(model: str, port: int | str, meta: dict[str, Any]) -> list[str]:
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


# ──────────────────────────────────────────────────────────────────────────────
# Download logic
# ──────────────────────────────────────────────────────────────────────────────
def _download_worker(model_name: str, url: str, expected_size: int, token: str | None, force: bool = False):
    """Background worker to download a model file."""
    try:
        # Determine output path from models.json
        models = get_models()
        if model_name not in models:
            with download_lock:
                download_status[model_name] = {"status": "error", "error": "Model not found in config"}
            return
        meta = models[model_name]
        out_path = BASE_DIR / meta["model_path"]
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Check for existing file to resume (skip if force redownload)
        resume_pos = 0
        if out_path.exists() and not force:
            existing_size = out_path.stat().st_size
            if expected_size and existing_size >= expected_size * 0.9:  # 90% tolerance
                with download_lock:
                    download_status[model_name] = {"status": "complete", "progress": 100, "size": existing_size}
                return
            resume_pos = existing_size
            log.info(f"Resuming download for {model_name} from {resume_pos} bytes")
        elif out_path.exists() and force:
            # Force redownload: delete existing file
            log.info(f"Force redownload for {model_name}: deleting existing file")
            out_path.unlink()

        # Update status
        with download_lock:
            download_status[model_name] = {
                "status": "downloading", 
                "progress": expected_size > 0 and int((resume_pos / expected_size) * 100) or 0,
                "size": expected_size if expected_size > 0 else 0,
                "downloaded": resume_pos
            }

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        headers["User-Agent"] = "Mozilla/5.0"
        if resume_pos > 0:
            headers["Range"] = f"bytes={resume_pos}-"
        headers["Accept"] = "*/*"
        
        start_time = time.time()
        last_notify = 0
        
        log.info(f"Starting download for {model_name} from {url}")
        
        # Stream download
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            log.error(f"Failed to connect to download URL {url}: {e}")
            raise
        with response as r:
            if r.status_code == 416:  # Range not satisfiable
                with download_lock:
                    download_status[model_name] = {"status": "complete", "progress": 100, "size": out_path.stat().st_size}
                return
            
            r.raise_for_status()
            downloaded = resume_pos
            chunk_size = 1024 * 1024  # 1MB
            file_mode = "ab" if resume_pos > 0 else "wb"
            with open(out_path, file_mode) as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        elapsed = time.time() - start_time
                        # Update progress every 100ms or every 10MB
                        if time.time() - last_notify > 0.1 or downloaded - last_notify > 10 * 1024 * 1024:
                            if expected_size and expected_size > 0:
                                progress = int((downloaded / expected_size) * 100)
                            else:
                                # Fallback: show progress based on bytes downloaded
                                # Just show that we're making progress
                                progress = min(99, max(1, int(downloaded / (1024 * 1024))))  # 1% per MB
                            speed_mbps = (downloaded - resume_pos) / (1024 * 1024) / max(elapsed, 0.1)
                            with download_lock:
                                download_status[model_name].update({
                                    "progress": progress, 
                                    "size": expected_size if expected_size > 0 else downloaded,
                                    "downloaded": downloaded,
                                    "speed_mbps": round(speed_mbps, 2)
                                })
                            last_notify = downloaded

        # Verify size
        actual_size = out_path.stat().st_size
        if expected_size and actual_size < expected_size * 0.9:  # allow 10% tolerance
            raise ValueError(f"Downloaded file too small: {actual_size} < {expected_size * 0.9}")

        # Success
        with download_lock:
            download_status[model_name] = {"status": "complete", "progress": 100, "size": actual_size}
        log_event("download", model_name, 0, None, f"completed ({actual_size} bytes)")
    except Exception as e:
        log.exception("Download failed for %s", model_name)
        with download_lock:
            download_status[model_name] = {"status": "error", "error": str(e), "retryable": True}


def start_download(model_name: str, url: str, expected_size: int, force: bool = False):
    """Start download in a background thread."""
    env = load_env()
    token = env.get("TOKEN")
    thread = threading.Thread(target=_download_worker, args=(model_name, url, expected_size, token, force), daemon=True)
    thread.start()


# ──────────────────────────────────────────────────────────────────────────────
# Model management (add/delete)
# ──────────────────────────────────────────────────────────────────────────────
def add_model_entry(
    name: str,
    repo: str,
    filename: str,
    expected_size: int,
    port: int,
    cache_size: int = 32768,
    threads: int = 16,
    threads_batch: int = 22,
    timeout: int = 3600,
    mlock: bool = True,
    flash_attn: str = "off",
    no_mmap: bool = True,
    parallel: int = 4,
    ubatch_size: int = 512,
) -> bool:
    """Add a new model entry to models.json and reserve directory."""
    try:
        models = get_models()
        if name in models:
            return False  # already exists
        # Ensure model directory exists
        model_dir = BASE_DIR / "models" / name.lower().replace(" ", "-")
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / filename
        # Relative path from BASE_DIR
        rel_path = model_path.relative_to(BASE_DIR)
        models[name] = {
            "model_path": str(rel_path),
            "repo": repo,
            "filename": filename,
            "expected_size": expected_size,
            "port": port,
            "cache_size": cache_size,
            "threads": threads,
            "threads_batch": threads_batch,
            "timeout": timeout,
            "mlock": mlock,
            "flash_attn": flash_attn,
            "no_mmap": no_mmap,
            "parallel": parallel,
            "ubatch_size": ubatch_size,
        }
        save_models(models)
        return True
    except Exception as e:
        log.exception("Failed to add model %s", name)
        return False


def delete_model_entry(name: str, delete_files: bool = False) -> bool:
    """Remove model from models.json and optionally delete its files."""
    try:
        models = get_models()
        if name not in models:
            return False
        meta = models[name]
        if delete_files:
            model_path_str = meta.get("model_path", "")
            if model_path_str:
                model_path = BASE_DIR / model_path_str
                if model_path.exists():
                    model_path.unlink()
                # Optionally remove directory if empty
                model_dir = model_path.parent
                try:
                    if model_dir.exists() and not any(model_dir.iterdir()):
                        model_dir.rmdir()
                except Exception:
                    pass
        del models[name]
        save_models(models)
        # Refresh the in-memory cache
        load_models_cache()
        return True
    except Exception as e:
        log.exception("Failed to delete model %s", name)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Status and control endpoints
# ──────────────────────────────────────────────────────────────────────────────
def _fetch_metrics_from_model(port: int) -> dict[str, float | None]:
    """Fetch performance metrics from a running llama-server's /metrics endpoint.

    Returns a dict with keys 'pp', 'tg', 'pg' (or None if not available).
    """
    host = DEFAULTS["host"]
    url = f"http://{host}:{port}/metrics"
    result: dict[str, float | None] = {"pp": None, "tg": None, "pg": None}
    try:
        resp = requests.get(url, timeout=0.5)
        if resp.status_code != 200:
            return result
        text = resp.text
        # Parse Prometheus text format
        # We'll collect raw values by metric name (strip labels)
        values: dict[str, float] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Format: metric_name{labels...} value timestamp?
            # We only want the first two tokens
            parts = line.split()
            if len(parts) < 2:
                continue
            name_full = parts[0]
            # Strip label suffix if present (everything from first '{' to matching '}')
            name = name_full.split("{", 1)[0]
            try:
                val = float(parts[1])
                values[name] = val
            except ValueError:
                continue
        # Map to expected fields
        # llama-server uses colon-separated names; support both separators
        # pp: prompt processing time (ms). llama-server gives: llamacpp:prompt_seconds_total
        prompt_ms = None
        if "llamacpp_prompt_processing_time_ms" in values:
            prompt_ms = values["llamacpp_prompt_processing_time_ms"]
        elif "llamacpp:prompt_seconds_total" in values:
            prompt_ms = values["llamacpp:prompt_seconds_total"] * 1000.0
        if prompt_ms is not None:
            result["pp"] = round(prompt_ms, 2)

        # tg: tokens per second (generation throughput)
        # llama-server gives total time and total tokens; we compute the rate
        gen_tokens = None
        gen_seconds = None
        # Accept both underscore and colon metric names for tokens
        if "llamacpp_generated_tokens_total" in values:
            gen_tokens = values["llamacpp_generated_tokens_total"]
        elif "llamacpp:predicted_tokens_total" in values:
            gen_tokens = values["llamacpp:predicted_tokens_total"]
        # Accept both possible names for generation time
        if "llamacpp_tokens_per_second" in values:
            # Some builds may still expose a direct tokens_per_second metric
            result["tg"] = round(values["llamacpp_tokens_per_second"], 2)
        elif "llamacpp:tokens_predicted_seconds_total" in values:
            gen_seconds = values["llamacpp:tokens_predicted_seconds_total"]
        # Compute tg if we have tokens and seconds
        if "tg" not in result and gen_tokens is not None and gen_seconds and gen_seconds > 0:
            result["tg"] = round(gen_tokens / gen_seconds, 2)

        # pg: combined total tokens (prompt + generated)
        prompt_tokens = values.get("llamacpp_prompt_tokens_total")
        if prompt_tokens is None and "llamacpp:prompt_tokens_total" in values:
            prompt_tokens = values["llamacpp:prompt_tokens_total"]
        if prompt_tokens is not None and gen_tokens is not None:
            result["pg"] = int(prompt_tokens + gen_tokens)
        elif prompt_tokens is not None:
            result["pg"] = int(prompt_tokens)
        elif gen_tokens is not None:
            result["pg"] = int(gen_tokens)
    except Exception as e:
        log.debug(f"Failed to fetch metrics for port {port}: {e}")
    return result


def _status_for_model(model: str, meta: dict[str, Any]) -> dict[str, Any]:
    port = int(meta.get("port", 11000))
    model_path_str = meta.get("model_path", "")
    model_path = BASE_DIR / model_path_str if model_path_str else None
    exists = model_path.is_file() if model_path else False
    # Quick check if port is active
    active = check_port_active(port)
    pid = None
    status = "stopped"

    if WSL:
        with _wsl_pids_lock:
            stored_pid_debug = _wsl_pids.get(model)
        log.info(f"Status check for {model}: WSL=True, port={port}, active={active}, stored_pid={stored_pid_debug}")
    
    # Check if model was recently stopped (within last 10 seconds)
    with _recent_stops_lock:
        recent_stop_time = _recent_stops.get(model, 0)
        if recent_stop_time and time.time() - recent_stop_time < 10:
            # If recently stopped, still report as stopped even if port shows active
            # This handles race conditions with port release and WSL PID lookup
            status = "stopped"
        else:
            # First check stored PID for WSL (most reliable)
            if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
                with _wsl_pids_lock:
                    stored_pid = _wsl_pids.get(model)
                if stored_pid:
                    pid = stored_pid
                    status = "running"
                    log.info(f"Status for {model}: using stored PID {pid}")
                elif active:
                    # Port active but no stored PID - this is expected on WSL
                    # The stored PID might be lost if server restarted
                    # Use None for pid since find_pid_by_port won't work for Windows processes
                    status = "running"
                    log.info(f"Status for {model}: port active but no stored PID (WSL)")
            elif active:
                # Non-WSL: use port check
                pid = find_pid_by_port(port)
                if pid:
                    status = "running"
                else:
                    status = "running (unknown pid)"
    # File size if it exists
    size_gb: float | None = meta.get("file_size_gb")
    if exists and size_gb is None and model_path:
        try:
            size_bytes = model_path.stat().st_size
            size_gb = round(size_bytes / (1024 ** 3), 2)
        except OSError:
            pass
    # Check for partial download
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
    # Fetch performance metrics if model is running
    metrics: dict[str, float | None] = {}
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


@app.route("/")
def index():
    models = get_models()
    statuses = [_status_for_model(name, meta) for name, meta in models.items()]
    html = render_template_string(TEMPLATE, statuses=statuses, host=DEFAULTS["host"], flask_port=FLASK_PORT, cache_version="v2")
    return html


@app.route("/api/status")
def api_status():
    models = get_models()
    results = []
    log.info(f"Status API called, models: {list(models.keys())}")
    for name, meta in models.items():
        result = _status_for_model(name, meta)
        results.append(result)
        log.info(f"Status API returning: model={result['model']}, status={result['status']}, pid={result['pid']}")
    response = jsonify(results)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route("/api/debug/<model_name>")
def api_debug(model_name):
    """Debug endpoint to check model status."""
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


@app.route("/api/start", methods=["POST"])
def api_start():
    try:
        data = request.get_json(force=True) or {}
        model = data.get("model", "").strip()
        if not model:
            return jsonify({"error": "Missing 'model'"}), 400
        models = get_models()
        if model not in models:
            return jsonify({"error": f"Unknown model '{model}'"}), 404
        meta = models[model]
        port = int(meta["port"])
        if find_pid_by_port(port):
            return jsonify({"error": f"Already running on port {port}", "status": "running"}), 409

        model_file = BASE_DIR / meta["model_path"]
        if not model_file.is_file():
            return jsonify({"error": f"Model file not found: {meta['model_path']}"}), 404

        cmd = build_cmd(model, port, meta)
        proc = None
        pid = None
        
        # On WSL with Windows binary, use bash to start the process in background
        # This is more reliable than PowerShell's Start-Process on Linux
        if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
            log.info(f"WSL mode: starting Windows binary in background via bash")
            log.info(f"LLAMA_SERVER: {LLAMA_SERVER}")
            log.info(f"cmd: {cmd}")
            # Build arguments directly from meta dictionary for reliability
            args = [
                convert_path_for_binary(BASE_DIR / meta["model_path"]),
                model,
                DEFAULTS["host"],
                str(port),
                str(meta.get("threads", DEFAULTS["threads"])),
                str(meta.get("threads_batch", DEFAULTS["threads_batch"])),
                str(meta.get("cache_size", DEFAULTS["cache_size"])),
                str(meta.get("timeout", DEFAULTS["timeout"])),
                str(meta.get("parallel", DEFAULTS["parallel"])),
                str(meta.get("ubatch_size", DEFAULTS["ubatch_size"])),
            ]
            log.info(f"args: {args}")
            # Build the command with Windows path for bash
            bash_cmd = (
                f'nohup "{cmd[0]}" -m "{args[0]}" --alias "{args[1]}" '
                f'--host "{args[2]}" --port {args[3]} --threads {args[4]} '
                f'--threads-batch {args[5]} -c {args[6]} --timeout {args[7]} '
                f'--mlock --no-mmap --parallel {args[8]} --ubatch-size {args[9]} '
                '> /dev/null 2>&1 & echo $!'
            )
            log.info(f"Starting Windows process via bash: nohup")
            log.info(f"bash_cmd: {bash_cmd}")
            # Run bash in background and immediately return
            proc = subprocess.Popen(
                ["bash", "-c", bash_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Give the process a moment to start and print PID
            time.sleep(0.2)
            # On WSL, we can't reliably get the PID from Windows process
            # Instead, we'll track it by port and return a placeholder
            pid = None  # Will be set to None since we can't get Windows PID
            log.info(f"Started bash process to launch Windows binary, pid={proc.pid}")
        elif WINDOWS:
            kwargs = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
            proc = subprocess.Popen(cmd, **kwargs)
            pid = proc.pid
            log.info(f"Started Windows process with PID {pid}")
        else:
            # On Linux, use start_new_session for detached process
            kwargs = {"start_new_session": True}
            proc = subprocess.Popen(cmd, **kwargs)
            pid = proc.pid
            log.info(f"Started Linux process with PID {pid}")
        
        # On WSL with Windows binary, we can't get the PID from bash
        # The PID will be tracked by port instead
        if pid is None and not (WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe')):
            log_event("start", model, port, None, "failed: could not get PID")
            return jsonify({"error": "Failed to start server: could not get PID"}), 500

        # Wait briefly for the process to start
        time.sleep(2.5)
        
        # Check if process is still running
        if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
            # On WSL, check if port is active (more reliable than PID check for background processes)
            # Try multiple times as the port may take a moment to become available
            is_running = False
            for _ in range(5):
                if check_port_active(port):
                    is_running = True
                    break
                time.sleep(0.5)
        elif proc:
            is_running = proc.poll() is None
        else:
            is_running = False
        
        if is_running:
            # Clear any recent stop entry so status reflects running state
            with _recent_stops_lock:
                _recent_stops.pop(model, None)
            # For WSL, we can't store the PID since it's a Windows process
            # The status will be determined by port check
            log.info(f"Start successful: model={model}, pid={pid}, port={port}, is_running={is_running}")
            log_event("start", model, port, pid, "running")
            return jsonify({"status": "running", "pid": pid, "port": port})
        else:
            log_event("start", model, port, pid, "exited_early")
            return jsonify({"error": "Server exited immediately", "status": "exited"}), 500
    except Exception as e:
        log.exception("Failed to start server for %s", model)
        return jsonify({"error": f"Failed to start server: {str(e)}"}), 500


@app.route("/api/stop", methods=["POST"])
def api_stop():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing 'model'"}), 400
    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    meta = models[model]
    port = int(meta["port"])
    
    # On WSL with Windows binary, we can't reliably get the PID
    # Use pids_of_llama() to find the process
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        # Find llama-server processes
        pids = pids_of_llama()
        if pids:
            pid = pids[0]
            log.info(f"Stop request for {model}: using llama-server PID {pid} (WSL mode)")
        else:
            log.info(f"No llama-server process found for {model}")
            # Record stop time even when no PID found, to prevent status race condition
            with _recent_stops_lock:
                _recent_stops[model] = time.time()
            return jsonify({"status": "stopped"})
    else:
        pid = find_pid_by_port(port)
        log.info(f"Stop request for {model}: port={port}, pid={pid}")
        
        # If no PID found but port is active, try to find llama-server process anyway
        if not pid and check_port_active(port):
            pids = pids_of_llama()
            log.info(f"No PID from port, found llama-server PIDs: {pids}")
            if pids:
                pid = pids[0]  # Use first found
    
    if not pid:
        log.info(f"No PID found for {model}, returning stopped")
        # Record stop time even when no PID found, to prevent status race condition
        with _recent_stops_lock:
            _recent_stops[model] = time.time()
        return jsonify({"status": "stopped"})

    success = _kill_tree(pid, model)
    log.info(f"Kill result for {model} (pid={pid}): success={success}")
    
    # Clear the stored PID for WSL models
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        with _wsl_pids_lock:
            old_pid = _wsl_pids.pop(model, None)
        log.info(f"Cleared stored PID for WSL model {model}: old_pid={old_pid}")
    
    # Record the stop time to avoid race conditions
    with _recent_stops_lock:
        _recent_stops[model] = time.time()
    
    # Wait for port to be released (up to 3 seconds)
    for _ in range(30):
        time.sleep(0.1)
        if not check_port_active(port):
            break
    
    if success:
        log_event("stop", model, port, pid, "stopped")
    else:
        log_event("stop", model, port, pid, "kill_failed")
    # Clear the WSL PID tracking for this model
    if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
        with _wsl_pids_lock:
            _wsl_pids.pop(model, None)
    return jsonify({"status": "stopped", "pid": pid, "success": success})


@app.route("/api/download", methods=["POST"])
def api_download():
    """Start downloading a model.
    Expected JSON: { "model": "...", "url": "...", "expected_size": 123456789, "resume": true, "force": true }
    
    When force=true and the URL is from a different file, creates a new model entry (Add Variant).
    """
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    url = data.get("url", "").strip()
    try:
        expected_size = int(data.get("expected_size", 0))
    except ValueError:
        expected_size = 0
    resume = data.get("resume", False)
    force = data.get("force", False)
    if not model or not url:
        return jsonify({"error": "Missing model or url"}), 400
    
    models = get_models()
    
    # Handle "Add Variant" case: force=true with a different file
    # BUT: if we're re-downloading (same model, same filename), just overwrite
    if force and model in models:
        meta = models[model]
        existing_filename = meta.get("filename", "")
        # Extract filename from URL to compare
        url_filename = url.split("/")[-1] if "/" in url else ""
        
        # Normalize filenames for comparison (case-insensitive, ignore .gguf extension)
        # Also normalize separators: replace . with - to handle v1.5.Q4_K_M vs v1-5-q4_k_m
        existing_normalized = existing_filename.lower()
        if existing_normalized.endswith(".gguf"):
            existing_normalized = existing_normalized[:-5]
        url_normalized = url_filename.lower()
        if url_normalized.endswith(".gguf"):
            url_normalized = url_normalized[:-5]
        
        # Normalize separators: replace dots with hyphens for consistent comparison
        existing_normalized = existing_normalized.replace('.', '-')
        url_normalized = url_normalized.replace('.', '-')
        
        # Only create a new variant if the filename is truly different
        if url_filename and url_normalized != existing_normalized:
            # Create a new model entry for the variant
            # Derive new model name from filename
            base_filename = url_filename[:-5] if url_filename.lower().endswith(".gguf") else url_filename
            # Create a display name by replacing separators with spaces
            new_model_name = base_filename.replace("_", " ").replace("-", " ").replace(".", " ")
            # Ensure uniqueness
            counter = 1
            original_name = new_model_name
            while new_model_name in models:
                new_model_name = f"{original_name} {counter}"
                counter += 1
            
            # Create the new model entry
            model_dir = BASE_DIR / "models" / new_model_name.lower().replace(" ", "-")
            model_dir.mkdir(parents=True, exist_ok=True)
            model_path = model_dir / url_filename
            rel_path = model_path.relative_to(BASE_DIR)
            
            # Extract repo from URL
            repo = ""
            if "huggingface.co/" in url:
                parts = url.split("huggingface.co/")[1].split("/")
                repo = "/".join(parts[:2]) if len(parts) >= 2 else ""
            
            # Get default port for new model
            port = 11000 + len(models) * 10
            
            new_meta = {
                "model_path": str(rel_path),
                "repo": repo,
                "filename": url_filename,
                "expected_size": expected_size,
                "port": port,
                "cache_size": DEFAULTS["cache_size"],
                "threads": DEFAULTS["threads"],
                "threads_batch": DEFAULTS["threads_batch"],
                "timeout": DEFAULTS["timeout"],
                "mlock": DEFAULTS["mlock"],
                "flash_attn": DEFAULTS["flash_attn"],
                "no_mmap": DEFAULTS["no_mmap"],
                "parallel": DEFAULTS["parallel"],
                "ubatch_size": DEFAULTS["ubatch_size"],
            }
            
            models[new_model_name] = new_meta
            save_models(models)
            log.info(f"Created new model entry for variant: {new_model_name}")
            
            # Start download for the new model
            start_download(new_model_name, url, expected_size, force=True)
            return jsonify({"status": "download started", "model": new_model_name, "is_variant": True})
    
    # Original logic for existing model
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    # Ensure target directory exists
    meta = models[model]
    target = BASE_DIR / meta["model_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    
    # If force redownload, delete existing file and start fresh
    if force and target.exists():
        log.info(f"Force redownload requested for {model}: deleting existing file")
        target.unlink()
        start_download(model, url, expected_size, force=True)
        return jsonify({"status": "download started", "model": model, "force": True})
    
    # Check if we should resume
    actual_size = 0
    if resume and target.exists():
        stat = target.stat()
        actual_size = stat.st_size
        if expected_size and actual_size < expected_size * 0.9:
            # Resume is possible
            pass
        else:
            # File is complete or resume doesn't make sense
            return jsonify({"status": "complete", "model": model, "size": actual_size})
    
    start_download(model, url, expected_size)
    return jsonify({"status": "download started", "model": model})


@app.route("/api/download-search", methods=["POST"])
def api_download_search():
    """Search HF for a model by name and return all GGUF files found.
    Expected JSON: { "model": "...", "force": true }
    Returns list of GGUF files for user selection, or single result to auto-download.
    """
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    force = data.get("force", False)
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    meta = models[model]
    
    # Check if file already exists and is complete (skip if force)
    out_path = BASE_DIR / meta.get("model_path", "")
    if out_path.exists() and not force:
        existing_size = out_path.stat().st_size
        expected_size = meta.get("expected_size", 0)
        if expected_size and existing_size >= expected_size * 0.9:
            return jsonify({"status": "complete", "model": model, "size": existing_size, "results": []})
    
    # Search HF for the model
    try:
        resp = requests.get(
            "https://huggingface.co/api/models",
            params={"search": model, "limit": 10, "full": "true"},
            timeout=10
        )
        if resp.status_code != 200:
            return jsonify({"error": f"HF search failed: HTTP {resp.status_code}"}), 500
        
        hf_models = resp.json()
        if not hf_models:
            return jsonify({"error": f"No model found on Hugging Face for '{model}'"}), 404
        
        # Collect all GGUF files from all matching models
        results = []
        for hf_model in hf_models:
            repo = hf_model["id"]
            for file in hf_model.get("siblings", []):
                if file.get("rfilename", "").lower().endswith(".gguf"):
                    filename = file["rfilename"]
                    size = file.get("size", 0)
                    if not size:
                        size = estimate_gguf_size(repo, filename)
                    url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
                    results.append({
                        "repo": repo,
                        "filename": filename,
                        "size": size,
                        "url": url,
                        "downloads": hf_model.get("downloads", 0),
                        "likes": hf_model.get("likes", 0)
                    })
        
        if not results:
            return jsonify({"error": f"No GGUF file found for '{model}' on Hugging Face"}), 404
        
        # If only one result, start download automatically
        if len(results) == 1:
            url = results[0]["url"]
            size = results[0]["size"]
            target = BASE_DIR / meta["model_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            start_download(model, url, size, force=force)
            return jsonify({"status": "download started", "model": model, "results": results})
        
        # Return multiple results for user selection
        return jsonify({"status": "multiple_results", "model": model, "results": results})
    except Exception as e:
        log.error(f"HF search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/download-search-filename", methods=["POST"])
def api_download_search_filename():
    """Search HF for a specific GGUF filename and return matching files.
    Expected JSON: { "filename": "model.Q4_K_M.gguf", "force": true }
    Strategy: Extract model name from filename, search for model, match exact filename.
    """
    data = request.get_json(force=True) or {}
    filename = data.get("filename", "").strip()
    force = data.get("force", False)
    if not filename:
        return jsonify({"error": "Missing filename"}), 400
    
    # Normalize the target filename for comparison (lowercase, no .gguf)
    target_filename = filename.lower()
    if target_filename.endswith(".gguf"):
        target_filename = target_filename[:-5]
    
    try:
        # Extract model name from filename
        # Common patterns:
        # - model.Q4_K_M.gguf -> model
        # - model-q4_0.gguf -> model
        # - Model-Name-v1.5-Q4_K_M.gguf -> Model-Name-v1.5
        base_name = filename[:-5] if filename.lower().endswith(".gguf") else filename
        
        # Remove common quantization suffixes
        quant_patterns = ['Q2_K', 'Q2_K_S', 'Q2_K_M', 'Q3_K_S', 'Q3_K_M', 'Q3_K_L',
                         'Q4_0', 'Q4_K_S', 'Q4_K_M', 'Q4_K_L',
                         'Q5_0', 'Q5_K_S', 'Q5_K_M', 'Q5_K_L',
                         'Q6_K', 'Q8_0', 'Q8_K', 'F16', 'BF16', 'F32']
        
        search_term = base_name  # Keep original format for HF search (hyphens work better)
        # Try to strip quantization suffix for comparison
        # because GGUF repos on HF include the quantization suffix in their names
        # e.g., "deepseek-coder-7b-instruct-v1.5-Q4_K_M-GGUF"
        stripped = False
        for sep in ['_', '-', '.']:
            for quant in quant_patterns:
                if base_name.upper().endswith(sep + quant):
                    # Strip the quantization suffix from search term
                    search_term = base_name[:-len(sep + quant)]
                    stripped = True
                    break
            if stripped:
                break
        if not stripped:
            for quant in quant_patterns:
                if base_name.upper().endswith(quant):
                    # Strip the quantization suffix from search term
                    search_term = base_name[:-len(quant)]
                    stripped = True
                    break
        
        if not search_term:
            # Fallback: use first segment before any separator
            fallback = base_name.split('_')[0].split('-')[0].split('.')[0]
            search_term = fallback if fallback else base_name
        
        log.info(f"Filename search: original='{filename}', extracted='{search_term}'")
        
        # Search Hugging Face for the model name
        resp = requests.get(
            "https://huggingface.co/api/models",
            params={"search": search_term, "limit": 20, "full": "true"},
            timeout=10
        )
        if resp.status_code != 200:
            return jsonify({"error": f"HF search failed: HTTP {resp.status_code}"}), 500
        
        hf_models = resp.json()
        if not hf_models:
            return jsonify({"error": f"No model found on Hugging Face for '{search_term}'"}), 404
        
        # Collect all GGUF files that match the filename exactly (normalized)
        results = []
        debug_candidates = []  # For logging
        
        # Normalize target_filename for comparison: replace dots with hyphens before quant suffixes
        # This handles cases like: deepseek-coder-7b-instruct-v1.5.Q4_K_M -> deepseek-coder-7b-instruct-v1-5-q4_k_m
        target_normalized = target_filename.replace('.', '-')
        # Also normalize to handle any remaining dot-separator before quantization
        for quant in ['q4_k_m', 'q4_k_s', 'q5_k_m', 'q8_0', 'f16', 'bf16', 'f32']:
            # Replace patterns like v1.5.q4_k_m with v1-5-q4_k_m
            target_normalized = target_normalized.replace('.' + quant, '-' + quant)
        
        for hf_model in hf_models:
            repo = hf_model["id"]
            for file in hf_model.get("siblings", []):
                file_rfilename = file.get("rfilename", "")
                if file_rfilename.lower().endswith(".gguf"):
                    # Normalize candidate filename
                    candidate = file_rfilename[:-5].lower() if file_rfilename.lower().endswith(".gguf") else file_rfilename.lower()
                    # Normalize candidate similarly
                    candidate_normalized = candidate.replace('.', '-')
                    for quant in ['q4_k_m', 'q4_k_s', 'q5_k_m', 'q8_0', 'f16', 'bf16', 'f32']:
                        candidate_normalized = candidate_normalized.replace('.' + quant, '-' + quant)
                    
                    debug_candidates.append({
                        "file_rfilename": file_rfilename,
                        "candidate_normalized": candidate_normalized,
                        "target_normalized": target_normalized,
                        "match": candidate_normalized == target_normalized
                    })
                    # Check for exact match (normalized)
                    if candidate_normalized == target_normalized:
                        size = file.get("size", 0)
                        if not size:
                            size = estimate_gguf_size(repo, file_rfilename)
                        url = f"https://huggingface.co/{repo}/resolve/main/{file_rfilename}"
                        results.append({
                            "repo": repo,
                            "filename": file_rfilename,
                            "size": size,
                            "url": url,
                            "downloads": hf_model.get("downloads", 0),
                            "likes": hf_model.get("likes", 0)
                        })
        
        # Detailed logging for debugging
        log.info(f"Filename search debug: target='{filename}', target_normalized='{target_filename}', search_term='{search_term}'")
        log.info(f"HF returned {len(hf_models)} models, {len(debug_candidates)} GGUF files total")
        for dbg in debug_candidates[:10]:  # Log up to 10 candidates
            log.info(f"  Candidate: {dbg['file_rfilename']} -> normalized='{dbg['candidate_normalized']}' match={dbg['match']}")
        
        if not results:
            return jsonify({"error": f"No GGUF file matching '{filename}' found on Hugging Face"}), 404
        
        # If only one exact match, start download automatically
        if len(results) == 1:
            url = results[0]["url"]
            size = results[0]["size"]
            # We don't have a model name to check, so just return success and let frontend handle it
            return jsonify({"status": "single_result", "filename": filename, "results": results})
        
        # Multiple exact matches? Return all for user selection.
        return jsonify({"status": "multiple_results", "filename": filename, "results": results})
    except Exception as e:
        log.error(f"HF filename search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/download-status")
def api_download_status():
    with download_lock:
        response = jsonify(download_status)
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response


@app.route("/api/download-stream/<model_name>")
def api_download_stream(model_name: str):
    """SSE endpoint for download progress streaming."""
    from urllib.parse import unquote
    model_name = unquote(model_name)
    
    def generate() -> Generator[str, None, None]:
        try:
            # Send initial status
            with download_lock:
                status = download_status.get(model_name, {"status": "waiting", "progress": 0})
            yield f"data: {json.dumps(status)}\n\n"
            
            # Poll for updates
            last_status = status.get("status", "")
            while last_status not in ("complete", "error"):
                time.sleep(0.5)
                with download_lock:
                    status = download_status.get(model_name, {"status": "waiting", "progress": 0})
                yield f"data: {json.dumps(status)}\n\n"
                last_status = status.get("status", "")
            
            # Final update
            yield f"data: {json.dumps(status)}\n\n"
        except GeneratorExit:
            pass
    
    return Response(generate(), mimetype="text/event-stream", headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
    })


@app.route("/api/add-model", methods=["POST"])
def api_add_model():
    """Add a new model definition.
    Expected JSON: {
        "name": "MyModel",
        "repo": "username/model-repo",
        "filename": "model.Q4_K_M.gguf",
        "expected_size": 1234567890,
        "port": 11010,
        ... optional other params ...
    }
    """
    data = request.get_json(force=True) or {}
    required = ["name", "repo", "filename", "expected_size", "port"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400
    try:
        port = int(data["port"])
        expected_size = int(data["expected_size"])
    except ValueError:
        return jsonify({"error": "port and expected_size must be integers"}), 400
    success = add_model_entry(
        name=data["name"].strip(),
        repo=data["repo"].strip(),
        filename=data["filename"].strip(),
        expected_size=expected_size,
        port=port,
        cache_size=int(data.get("cache_size", DEFAULTS["cache_size"])),
        threads=int(data.get("threads", DEFAULTS["threads"])),
        threads_batch=int(data.get("threads_batch", DEFAULTS["threads_batch"])),
        timeout=int(data.get("timeout", DEFAULTS["timeout"])),
        mlock=bool(data.get("mlock", DEFAULTS["mlock"])),
        flash_attn=str(data.get("flash_attn", DEFAULTS["flash_attn"])),
        no_mmap=bool(data.get("no_mmap", DEFAULTS["no_mmap"])),
        parallel=int(data.get("parallel", DEFAULTS["parallel"])),
        ubatch_size=int(data.get("ubatch_size", DEFAULTS["ubatch_size"])),
    )
    if not success:
        return jsonify({"error": f"Model '{data['name']}' already exists or failed to add"}), 409
    return jsonify({"status": "model added", "name": data["name"]})


@app.route("/api/delete-model", methods=["POST"])
def api_delete_model():
    """Delete a model.
    Expected JSON: { "model": "...", "delete_files": false }
    """
    data = request.get_json(force=True) or {}
    name = data.get("model", "").strip()
    if not name:
        return jsonify({"error": "Missing model name"}), 400
    delete_files = bool(data.get("delete_files", False))
    success = delete_model_entry(name, delete_files=delete_files)
    if not success:
        return jsonify({"error": f"Model '{name}' not found"}), 404
    return jsonify({"status": "model deleted", "name": name, "delete_files": delete_files})


@app.route("/api/update-model-repo", methods=["POST"])
def api_update_model_repo():
    """Update the Hugging Face repo field for a model."""
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    repo = data.get("repo", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    if not repo:
        return jsonify({"error": "Missing repo"}), 400

    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    # Update repo field
    models[model]["repo"] = repo
    save_models(models)

    return jsonify({"status": "repo updated", "model": model, "repo": repo})


@app.route("/api/logs")
def api_logs():
     if not LOG_FILE.exists():
         return jsonify([])
     lines = LOG_FILE.read_text("utf-8", errors="replace").strip().splitlines()
     return jsonify(lines[-100:])


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
     """Gracefully shutdown the Flask server."""
     log.info("Shutdown requested via API")
     # Use a thread to allow response to be sent before shutdown
     def shutdown():
         time.sleep(0.1)
         import os
         os._exit(0)
     threading.Thread(target=shutdown, daemon=True).start()
     return jsonify({"status": "shutdown"})

@app.route("/api/next-port")
def api_next_port():
    start = 11000
    port = start
    while check_port_active(port):
        port += 1
    return jsonify({"port": port})


@app.route("/api/model-file", methods=["POST"])
def api_model_file():
    """Get the download URL and size for a model file."""
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    
    meta = models[model]
    repo = meta.get("repo", "")
    filename = meta.get("filename", "")
    size = meta.get("expected_size", 0)
    
    if repo and filename:
        # Search HF for the actual file info
        try:
            resp = requests.get(f"https://huggingface.co/api/models/{repo}", timeout=10)
            if resp.status_code == 200:
                hf_model = resp.json()
                for file in hf_model.get("siblings", []):
                    if file.get("rfilename") == filename:
                        size = file.get("size", size)
                        break
            url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
            return jsonify({"url": url, "size": size, "filename": filename})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    return jsonify({"error": "Model has no repo/filename configured"}), 400


@app.route("/api/hf-repo-search", methods=["POST"])
def api_hf_repo_search():
    """Search Hugging Face for model GGUF files and return repo candidates (no download)."""
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    try:
        resp = requests.get(
            "https://huggingface.co/api/models",
            params={"search": model, "limit": 10, "full": "true"},
            timeout=10
        )
        if resp.status_code != 200:
            return jsonify({"error": f"HF search failed: HTTP {resp.status_code}"}), 500
        hf_models = resp.json()
        if not hf_models:
            return jsonify({"error": f"No model found on Hugging Face for '{model}'"}), 404

        results = []
        for hf_model in hf_models:
            repo = hf_model["id"]
            for file in hf_model.get("siblings", []):
                if file.get("rfilename", "").lower().endswith(".gguf"):
                    filename = file["rfilename"]
                    size = file.get("size", 0)
                    if not size:
                        size = estimate_gguf_size(repo, filename)
                    results.append({
                        "repo": repo,
                        "filename": filename,
                        "size": size,
                        "downloads": hf_model.get("downloads", 0),
                        "likes": hf_model.get("likes", 0)
                    })
        if not results:
            return jsonify({"error": f"No GGUF file found for '{model}' on Hugging Face"}), 404
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        log.error(f"HF search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/hf-metadata", methods=["GET"])
def api_hf_metadata():
    """Get Hugging Face metadata for a model."""
    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400

    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    meta = models[model]
    repo = meta.get("repo", "")
    if not repo:
        return jsonify({"error": "Model has no Hugging Face repo configured"}), 400

    try:
        resp = requests.get(f"https://huggingface.co/api/models/{repo}", timeout=10)
        if resp.status_code != 200:
            log.warning(f"HF metadata fetch failed for {repo}: HTTP {resp.status_code}")
            return jsonify({"error": f"Failed to fetch metadata: HTTP {resp.status_code}"}), 502

        data = resp.json()
        # Extract useful fields
        result = {
            "repo": repo,
            "downloads": data.get("downloads", 0),
            "likes": data.get("likes", 0),
            "tags": data.get("tags", []),
            "author": repo.split("/")[0] if "/" in repo else repo,
            "lastModified": data.get("lastModified", ""),
            "description": data.get("description", "")[:500] if data.get("description") else "",
        }
        return jsonify(result)
    except requests.exceptions.RequestException as e:
        log.error(f"HF metadata request error for {repo}: {e}")
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        log.error(f"HF metadata unexpected error for {repo}: {e}")
        return jsonify({"error": "Unexpected error"}), 500


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Proxy chat requests to a running model."""
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400

    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

     meta = models[model]
     port = meta.get("port")
     if not port:
         return jsonify({"error": "Model has no assigned port"}), 400
     try:
         port_int = int(port)
     except (TypeError, ValueError):
         log.error(f"Invalid port type for model {model}: {port!r}")
         return jsonify({"error": "Model has invalid port configuration"}), 500

     if not check_port_active(port_int):
         return jsonify({"error": f"Model '{model}' is not running (port {port} inactive)"}), 400

     payload = dict(data)
     payload["stream"] = False
     if "messages" not in payload:
         return jsonify({"error": "Missing 'messages' field"}), 400

     target_url = f"http://127.0.0.1:{port_int}/v1/chat/completions"
     try:
         # Use client-provided timeout if available, else default to 300 seconds
         client_timeout = data.get("timeout")
         if client_timeout is not None:
             try:
                 timeout = max(1, min(int(client_timeout), 600))  # clamp 1-600
             except (ValueError, TypeError):
                 timeout = 300
         else:
             timeout = 300
         resp = requests.post(target_url, json=payload, timeout=timeout)
         if resp.status_code != 200:
             return jsonify({"error": f"Model API returned {resp.status_code}", "details": resp.text}), resp.status_code
         try:
             json_data = resp.json()
         except json.JSONDecodeError as e:
             log.error(f"Invalid JSON from model {model} on port {port_int}: {resp.text[:500]}")
             return jsonify({"error": "Model returned invalid JSON response", "details": resp.text[:500]}), 500
         return jsonify(json_data)
     except requests.exceptions.RequestException as e:
         log.error(f"Chat proxy error for {model} on port {port_int}: {e}")
         return jsonify({"error": str(e)}), 500


@app.route("/api/search-models", methods=["POST"])
def api_search_models():
    """Search Hugging Face Hub for GGUF models."""
    try:
        data = request.get_json()
        query = data.get("query", "").strip()
        if not query:
            return jsonify([])

        # Search Hugging Face Hub
        resp = requests.get(
            "https://huggingface.co/api/models",
            params={
                "search": query,
                "limit": 20,
                "full": "true"
            },
            timeout=10
        )
        if resp.status_code != 200:
            log.error(f"HF search error: HTTP {resp.status_code}")
            return jsonify([])

        models = resp.json()
        results = []
        for model in models:
            model_id = model.get("id", "")
            # Find first GGUF file in siblings
            gguf_file = None
            for file in model.get("siblings", []):
                if file.get("rfilename", "").lower().endswith(".gguf"):
                    gguf_file = file
                    break
            if not gguf_file:
                continue  # Skip models without GGUF files

            filename = gguf_file.get("rfilename", "")
            size = gguf_file.get("size", 0)
            
            # If size is missing or 0, estimate it based on quantization and model size
            if not size:
                size = estimate_gguf_size(model_id, filename)

            results.append({
                "modelId": model_id,
                "filename": filename,
                "size": size,
                "downloads": model.get("downloads", 0),
                "likes": model.get("likes", 0)
            })
        return jsonify(results)
    except Exception as e:
        log.error(f"Search error: {e}")
        return jsonify([])


def estimate_gguf_size(model_id: str, filename: str) -> int:
    """
    Estimate GGUF file size based on model parameters and quantization.
    Returns size in bytes, or 0 if estimation fails.
    """
    # Quantization bytes per parameter (approximate)
    quant_patterns = {
        'Q2_K': 0.35, 'Q2_K_S': 0.30, 'Q2_K_M': 0.33,
        'Q3_K_S': 0.42, 'Q3_K_M': 0.47, 'Q3_K_L': 0.50,
        'Q4_0': 0.50, 'Q4_K_S': 0.50, 'Q4_K_M': 0.55, 'Q4_K_L': 0.60,
        'Q5_0': 0.65, 'Q5_K_S': 0.65, 'Q5_K_M': 0.70, 'Q5_K_L': 0.75,
        'Q6_K': 0.80, 'Q8_0': 1.0, 'Q8_K': 1.0,
        'F16': 2.0, 'BF16': 2.0, 'F32': 4.0,
    }
    
    # Known model family sizes (in billions of parameters)
    known_models = {
        'phi-3-mini': 3.8, 'phi-3.5-mini': 3.8, 'phi-4': 14,  # phi-4 ~14B
        'llama-2-7b': 7, 'llama-2-13b': 13, 'llama-2-70b': 70,
        'llama-3-8b': 8, 'llama-3-70b': 70,
        'llama-3.1-8b': 8, 'llama-3.1-70b': 70, 'llama-3.1-405b': 405,
        'mistral-7b': 7, 'mixtral-8x7b': 47,  # Mixtral effective ~47B
        'gemma-2b': 2, 'gemma-7b': 7,
    }
    
    # Determine bytes per parameter from quantization
    bpc = 2.0  # Default F16
    filename_lower = filename.lower()
    for quant, bytes_per_param in quant_patterns.items():
        if quant.lower() in filename_lower:
            bpc = bytes_per_param
            break
    
    # Try to extract parameter count from model_id or filename
    import re
    param_count = None
    
    # Look for explicit number followed by B (7B, 13B, 70B, etc.)
    match = re.search(r'(\d+(?:\.\d+)?)\s*[bB]', model_id + ' ' + filename)
    if match:
        try:
            param_count = float(match.group(1))
        except:
            pass
    
    # If not found, check known model families
    if param_count is None:
        model_id_lower = model_id.lower()
        for family, params in known_models.items():
            if family in model_id_lower:
                param_count = params
                break
    
    if param_count is None:
        return 0
    
    total_params = param_count * 1_000_000_000
    estimated_size = int(total_params * bpc)
    return estimated_size


# ──────────────────────────────────────────────────────────────────────────────
# HTML template (embedded) – corrected
# ──────────────────────────────────────────────────────────────────────────────
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>llama.cpp – Model Manager</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta name="cache-version" content="{{ cache_version }}">
<style>
   :root {
     --bg: #1a1a2e; --surface: #16213e; --surface2: #0f3460;
     --text: #e0e0e0; --text2: #a0a0b8; --green: #2ecc71;
     --red: #e74c3c; --yellow: #f1c40f; --blue: #3498db;
     --btn-run: #27ae60; --btn-stop: #e74c3c; --btn-download: #ff9800;
     --btn-add: #9b59b6; --btn-del: #e67e22; --btn-search: #3498db;
     --btn-goto: #00bcd4;
   }
  /* Theme: Dark (default) */
  [data-theme="dark"] {
    --bg: #1a1a2e; --surface: #16213e; --surface2: #0f3460;
    --text: #e0e0e0; --text2: #a0a0b8;
  }
  /* Theme: Light */
  [data-theme="light"] {
    --bg: #f5f5f5; --surface: #ffffff; --surface2: #e0e0e0;
    --text: #333333; --text2: #666666;
  }
  /* Theme: Solarized Dark */
  [data-theme="solarized-dark"] {
    --bg: #002b36; --surface: #073642; --surface2: #586e75;
    --text: #839496; --text2: #93a1a1;
  }
  /* Theme: Nord */
  [data-theme="nord"] {
    --bg: #2e3440; --surface: #3b4252; --surface2: #4c566a;
    --text: #d8dee9; --text2: #a5abb6;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
  }
  header {
    background: var(--surface); padding: 1.2rem 2rem;
    display: flex; align-items: center; gap: 1rem;
    border-bottom: 2px solid var(--surface2);
  }
  header h1 { margin: 0; font-size: 1.4rem; white-space: nowrap; }
  header .badge {
    background: var(--blue); color: #fff; padding: .2rem .6rem;
    border-radius: 12px; font-size: .75rem; text-transform: uppercase;
  }
  main { padding: 1.5rem 2rem; max-width: 1200px; margin: 0 auto; }
  .toolbar {
    display: flex; gap: .75rem; flex-wrap: wrap; margin-bottom: 1.2rem;
    align-items: center;
  }
  .toolbar input[type=search] {
    flex: 1; min-width: 220px; padding: .5rem .8rem;
    border-radius: 6px; border: 1px solid var(--surface2);
    background: var(--surface); color: var(--text); font-size: .95rem;
  }
  .toolbar button, .toolbar a {
    padding: .5rem 1rem; border: none; border-radius: 6px;
    cursor: pointer; font-size: .9rem; text-decoration: none; color: #fff;
  }
   .btn-run   { background: var(--btn-run); }
   .btn-stop  { background: var(--btn-stop); }
   .btn-download { background: var(--btn-download); }
   .btn-add   { background: var(--btn-add); }
   .btn-del   { background: var(--btn-del); }
   .btn-search { background: var(--btn-search); }
   .btn-goto { background: var(--btn-goto); }
.tile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}
.tile {
  background: var(--surface);
  border: 1px solid var(--surface2);
  border-radius: 8px;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: .6rem;
}
.tile.running {
  border-left: 4px solid var(--green);
  opacity: .98;
}
.tile-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: .4rem;
}
.tile-header b {
  font-size: 1.05rem;
  word-break: break-word;
  flex: 1;
}
.tile-body {
  font-size: .87rem;
  color: var(--text2);
  display: flex;
  flex-direction: column;
  gap: .25rem;
}
.tile-body code {
  color: var(--text);
}
.tile-actions {
  display: flex;
  gap: .4rem;
  flex-wrap: wrap;
  margin-top: .1rem;
}
  .chip {
    display: inline-block; padding: .15rem .55rem; border-radius: 10px;
    font-size: .78rem; font-weight: 600;
  }
  .chip.ok   { background: rgba(46,204,113,.15); color: var(--green); }
  .chip.bad  { background: rgba(231,76,60,.15);  color: var(--red); }
  .chip.warn { background: rgba(241,196,15,.15); color: var(--yellow); }
  .chip.dl   { background: rgba(255,152,0,.15); color: var(--btn-download); }
  .actions { display: flex; gap: .4rem; flex-wrap: wrap; }
  .actions button {
    padding: .3rem .7rem; border: none; border-radius: 4px;
    font-size: .8rem; cursor: pointer; color: #fff;
  }
  .actions button:disabled { opacity: .4; cursor: not-allowed; }
  .actions .run  { background: var(--btn-run); }
  .actions .stop  { background: var(--btn-stop); }
  .actions .download { background: var(--btn-download); }
  .actions .add    { background: var(--btn-add); }
  .actions .del    { background: var(--btn-del); }
   .actions .goto { background: var(--blue); }
   .actions .chat { background: var(--blue); }
   #log {
    background: var(--surface); padding: .8rem 1rem;
    border-radius: 6px; font-family: monospace; font-size: .82rem;
    max-height: 260px; overflow-y: auto; margin-top: 1.4rem;
    white-space: pre-wrap; color: var(--text2);
  }
  #log b { color: var(--text); }
  .banner {
    background: var(--surface2); color: var(--text); padding: .5rem 1rem;
    border-radius: 6px; margin-bottom: 1rem; font-size: .92rem;
  }
  .banner a { color: var(--yellow); }
  #add-model-form {
    background: var(--surface); padding: 1rem; border-radius: 6px;
    margin-top: 1rem; display: none;
  }
  #add-model-form h3 { margin-top: 0; color: var(--text); }
  #add-model-form label { display: block; margin-top: .5rem; font-weight: bold; }
  #add-model-form input, #add-model-form select {
    width: 100%; padding: .5rem; border: 1px solid var(--surface2);
    border-radius: 4px; background: var(--surface); color: var(--text);
    margin-top: .25rem;
  }
  #add-model-form button {
    margin-top: 1rem; padding: .5rem 1rem; border: none;
    border-radius: 4px; cursor: pointer;
  }
  #add-model-form .btn-add { background: var(--btn-add); }
  #add-model-form .btn-cancel { background: var(--red); }
  #search-results {
    margin-top: 0.5rem;
    max-height: 200px;
    overflow-y: auto;
    border: 1px solid var(--surface2);
    border-radius: 4px;
    background: var(--surface);
    display: none;
  }
  #search-results div {
    padding: 0.5rem;
    border-bottom: 1px solid var(--surface2);
    cursor: pointer;
  }
  #search-results div:hover {
    background: rgba(52,152,219,0.1);
  }
   #search-results div:last-child {
     border-bottom: none;
   }
   .btn-goto { background: var(--blue); }
   #loading {
     display: none;
     text-align: center;
     padding: 2rem;
     color: var(--text2);
     font-size: 1.1rem;
   }
    .spinner {
      width: 40px;
      height: 40px;
      margin: 0 auto 1rem;
      border: 4px solid var(--surface2);
      border-top: 4px solid var(--btn-goto);
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }
   @keyframes spin {
     0% { transform: rotate(0deg); }
     100% { transform: rotate(360deg); }
   }
    @media (max-width: 640px) {
   main { padding: 1rem; }
   header { flex-direction: column; align-items: flex-start; gap: .4rem; }
   .tile-header, .tile-actions { font-size: .82rem; }
 }
   #content-area {
     display: grid;
     grid-template-columns: 1fr;
     gap: 1.5rem;
   }
   #content-area.has-panel {
     grid-template-columns: 1fr 340px;
   }
   @media (max-width: 1024px) {
     #content-area.has-panel {
       grid-template-columns: 1fr;
     }
   }
   .properties-panel {
     background: var(--surface);
     border: 1px solid var(--surface2);
     border-radius: 8px;
     padding: 1rem;
     position: relative;
     max-height: calc(100vh - 220px);
     overflow-y: auto;
   }
   .panel-header {
     display: flex;
     justify-content: space-between;
     align-items: flex-start;
     position: relative;
     padding-right: 1.5rem;
   }
   .panel-close-btn:hover {
     color: var(--text);
   }
   .panel-section {
     margin-bottom: 1rem;
   }
   .panel-section:last-child {
     margin-bottom: 0;
   }
   .panel-section h3 {
     margin: 0 0 .5rem;
     font-size: .9rem;
     color: var(--text2);
     text-transform: uppercase;
     letter-spacing: .5px;
   }
   .panel-grid {
     display: grid;
     grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
     gap: .5rem;
   }
   .panel-item {
     display: flex;
     flex-direction: column;
     gap: .2rem;
   }
   .panel-item .label {
     font-size: .75rem;
     color: var(--text2);
   }
   .panel-item .value {
     font-size: .9rem;
     color: var(--text);
     word-break: break-word;
   }
   .panel-item .value code {
     background: var(--surface2);
     padding: .1rem .3rem;
     border-radius: 3px;
     font-family: monospace;
     font-size: .85rem;
   }
    .chip.running {
      background: rgba(46,204,113,.2);
      color: var(--green);
      font-weight: 600;
      padding: .2rem .6rem;
      border-radius: 10px;
      display: inline-block;
      font-size: .8rem;
    }
    .loading {
      color: var(--text2);
      font-style: italic;
      padding: .5rem 0;
    }
    .error {
      color: #e74c3c;
      padding: .5rem 0;
      font-size: .9rem;
    }
    .tag {
      display: inline-block;
      background: var(--surface2);
      color: var(--text);
      padding: .15rem .5rem;
      border-radius: 4px;
      font-size: .75rem;
      margin: 0 .2rem .2rem 0;
    }
     .panel-item.full-width {
       grid-column: 1 / -1;
     }
     /* Chat Interface Styles */
     .chat-section {
       border: 1px solid var(--surface2);
       border-radius: 8px;
       padding: 0.75rem;
       background: rgba(0,0,0,0.1);
     }
     .chat-params {
       display: grid;
       grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
       gap: 0.5rem;
       margin-bottom: 0.75rem;
     }
     .chat-params .panel-item {
       display: flex;
       flex-direction: column;
       gap: 0.3rem;
     }
     .chat-params input[type="range"] {
       width: 100%;
       height: 4px;
       background: var(--surface2);
       border-radius: 2px;
       outline: none;
       -webkit-appearance: none;
     }
     .chat-params input[type="range"]::-webkit-slider-thumb {
       -webkit-appearance: none;
       width: 14px;
       height: 14px;
       background: var(--blue);
       border-radius: 50%;
       cursor: pointer;
     }
     .chat-params input[type="number"] {
       background: var(--surface);
       border: 1px solid var(--surface2);
       color: var(--text);
       padding: 0.2rem 0.4rem;
       border-radius: 4px;
       font-size: 0.85rem;
       width: 70px;
     }
     .chat-messages {
       max-height: 200px;
       overflow-y: auto;
       background: var(--surface);
       border: 1px solid var(--surface2);
       border-radius: 6px;
       padding: 0.5rem;
       margin-bottom: 0.5rem;
       display: flex;
       flex-direction: column;
       gap: 0.5rem;
       font-size: 0.85rem;
     }
     .chat-message {
       padding: 0.4rem 0.6rem;
       border-radius: 8px;
       max-width: 90%;
       word-wrap: break-word;
       line-height: 1.4;
     }
     .chat-message.user {
       align-self: flex-end;
       background: var(--btn-add);
       color: #fff;
     }
     .chat-message.assistant {
       align-self: flex-start;
       background: var(--surface2);
       color: var(--text);
     }
     .chat-message.error {
       align-self: center;
       background: var(--btn-del);
       color: #fff;
       font-size: 0.8rem;
     }
     .chat-input-area {
       display: flex;
       gap: 0.5rem;
       margin-bottom: 0.5rem;
     }
     .chat-input-area textarea {
       flex: 1;
       background: var(--surface);
       border: 1px solid var(--surface2);
       color: var(--text);
       padding: 0.5rem;
       border-radius: 6px;
       resize: vertical;
       min-height: 60px;
       font-family: inherit;
       font-size: 0.9rem;
     }
     .chat-input-area textarea:focus {
       outline: none;
       border-color: var(--blue);
     }
     .btn-chat-send {
       background: var(--btn-add);
       color: #fff;
       border: none;
       border-radius: 6px;
       padding: 0.5rem 1rem;
       cursor: pointer;
       font-size: 0.85rem;
       align-self: flex-end;
     }
     .btn-chat-send:hover {
       opacity: 0.9;
     }
      .btn-chat-send:disabled {
        background: var(--text2);
        cursor: not-allowed;
      }
      .chat-stats {
        font-size: 0.75rem;
        color: var(--text2);
        text-align: right;
      }
      .btn-primary {
        background: var(--blue);
        color: #fff;
        border: none;
        border-radius: 6px;
        padding: 0.5rem 1rem;
        cursor: pointer;
        font-size: 0.9rem;
        transition: opacity 0.2s;
      }
      .btn-primary:hover {
        opacity: 0.9;
      }
      .hf-repo-card:hover {
        border-color: var(--blue) !important;
      }

      /* Tile chat styles */
      .tile .tile-chat {
        border-top: 1px solid var(--surface2);
        padding-top: 0.5rem;
        margin-top: 0.5rem;
      }
      .tile .chat-params {
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem;
        margin-bottom: 0.5rem;
      }
      .tile .chat-params .panel-item {
        min-width: 100px;
        font-size: 0.8rem;
      }
      .tile .chat-messages {
        max-height: 120px;
        font-size: 0.85rem;
      }
      .tile .chat-input-area {
        display: flex;
        gap: 0.4rem;
        margin-top: 0.4rem;
      }
      .tile .chat-input-area textarea {
        min-height: 40px;
        font-size: 0.85rem;
        resize: vertical;
      }
      .tile .btn-chat-send {
        padding: 0.3rem 0.6rem;
        font-size: 0.8rem;
      }
      .tile .chat-stats {
        font-size: 0.75rem;
        text-align: right;
        margin-top: 0.25rem;
      }

      /* ──────────────────────────────────────────────────────────────
         Chat dialog responsive styling (bead web-28d)
         ────────────────────────────────────────────────────────────── */

      /* Smooth transitions for interactive elements */
      .chat-section,
      .tile .tile-chat,
      .chat-params input[type="range"],
      .chat-params input[type="number"],
      .chat-input-area textarea,
      .btn-chat-send {
        transition: border-color 0.2s ease, background-color 0.2s ease, box-shadow 0.2s ease;
      }

      /* Focus-visible for accessibility */
      .chat-section :focus-visible,
      .tile .tile-chat :focus-visible {
        outline: 2px solid var(--blue);
        outline-offset: 2px;
       }

       /* Chat Modal styling (bead web-e7r.7) */
       #chat-modal {
         display: flex !important;
         position: fixed;
         inset: 0;
         background: rgba(0, 0, 0, 0.6);
         align-items: center;
         justify-content: center;
         backdrop-filter: blur(2px);
         opacity: 0;
         pointer-events: none;
         transition: opacity 0.25s ease-out;
       }
       #chat-modal[aria-hidden="false"] {
         opacity: 1;
         pointer-events: auto;
       }
       #chat-modal .chat-section {
         background: var(--surface);
         border: 1px solid var(--surface2);
         border-radius: 12px;
         box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
         display: flex;
         flex-direction: column;
         overflow: hidden;
         width: min(800px, 90vw);
         max-height: 90vh;
         position: relative;
         margin: 1rem;
       }
       #chat-modal-close {
         position: absolute;
         top: 0.75rem;
         right: 0.75rem;
         width: 2rem;
         height: 2rem;
         border-radius: 50%;
         background: transparent;
         border: none;
         color: var(--text2);
         font-size: 1.75rem;
         line-height: 1;
         cursor: pointer;
         z-index: 10;
         display: flex;
         align-items: center;
         justify-content: center;
         padding: 0;
         transition: color 0.2s ease, background-color 0.2s ease;
       }
       #chat-modal-close:hover,
       #chat-modal-close:focus-visible {
         color: var(--text);
         background: var(--surface2);
         outline: none;
       }
        #chat-modal-content {
          display: flex;
          flex-direction: column;
          flex: 1;
          padding: 1rem;
          padding-top: 2.5rem;
          overflow: hidden;
        }
        #chat-modal .modal-chat {
          display: flex;
          flex-direction: column;
          flex: 1;
          min-height: 0;
        }
        .chat-params {
         display: grid;
         grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
         gap: 0.5rem;
         margin-bottom: 0.75rem;
         padding-bottom: 0.75rem;
         border-bottom: 1px solid var(--surface2);
         flex-shrink: 0;
       }
       .chat-params .panel-item {
         min-width: 0;
         font-size: 0.8rem;
       }
       .chat-params .panel-item .label {
         font-size: 0.7rem;
         text-transform: uppercase;
         letter-spacing: 0.5px;
       }
        .chat-messages {
          flex: 1;
          overflow-y: auto;
          display: flex;
          flex-direction: column;
          gap: 0.75rem;
          padding-right: 0.5rem;
          margin-bottom: 0.75rem;
          min-height: 0;
          max-height: none;
        }
       .chat-input-area {
         display: flex;
         gap: 0.5rem;
         align-items: flex-end;
         flex-shrink: 0;
         padding-top: 0.5rem;
         border-top: 1px solid var(--surface2);
       }
       .chat-input-area textarea {
         flex: 1;
         min-height: 60px;
         max-height: 150px;
         resize: vertical;
         padding: 0.6rem;
         border: 1px solid var(--surface2);
         border-radius: 6px;
         background: var(--bg);
         color: var(--text);
         font-family: inherit;
         font-size: 0.95rem;
         line-height: 1.4;
         transition: border-color 0.2s ease;
       }
       .chat-input-area textarea:focus {
         outline: none;
         border-color: var(--blue);
       }
       .chat-input-area textarea::placeholder {
         color: var(--text2);
         opacity: 0.7;
       }
       .btn-chat-send {
         flex-shrink: 0;
         padding: 0.5rem 1rem;
         border: none;
         border-radius: 6px;
         background: var(--btn-run);
         color: #fff;
         font-weight: 600;
         cursor: pointer;
         transition: background-color 0.2s ease, transform 0.1s ease;
       }
       .btn-chat-send:hover:not(:disabled) {
         background: #219a52;
       }
       .btn-chat-send:active:not(:disabled) {
         transform: translateY(1px);
       }
       .btn-chat-send:disabled {
         opacity: 0.5;
         cursor: not-allowed;
       }
       .chat-stats {
         font-variant-numeric: tabular-nums;
         letter-spacing: 0.02em;
         font-size: 0.75rem;
         color: var(--text2);
         text-align: right;
         margin-top: 0.25rem;
       }
       .chat-messages {
         scroll-behavior: smooth;
       }
       .chat-messages::-webkit-scrollbar {
         width: 8px;
       }
       .chat-messages::-webkit-scrollbar-track {
         background: transparent;
       }
       .chat-messages::-webkit-scrollbar-thumb {
         background: var(--surface2);
         border-radius: 4px;
       }
       .chat-messages::-webkit-scrollbar-thumb:hover {
         background: var(--text2);
         opacity: 0.5;
       }
       @media (prefers-reduced-motion: reduce) {
         #chat-modal {
           transition: none;
         }
       }

       /* Ensure chat modal overlay appears above other content (if used) */
       .chat-section,
       .modal-overlay.chat-modal {
         z-index: 1100; /* above hf-repo-modal (1000) */
       }

       
       /* Responsive adjustments for small screens */
      @media (max-width: 600px) {
        /* Modal-like chat container (if used as modal) */
        .chat-section {
          width: 95% !important;
          max-width: none !important;
          max-height: 90vh !important;
          overflow-y: auto;
          padding: 0.5rem;
        }

        /* Tile-embedded chat */
        .tile .tile-chat {
          padding: 0.4rem;
        }
        .tile .chat-params {
          grid-template-columns: 1fr; /* Stack parameters vertically */
          gap: 0.4rem;
        }
        .tile .chat-params .panel-item {
          min-width: auto;
          font-size: 0.75rem;
        }
        .tile .chat-messages {
          max-height: 150px;
          font-size: 0.8rem;
        }
        .tile .chat-input-area {
          flex-direction: column;
          gap: 0.4rem;
        }
        .tile .chat-input-area textarea {
          min-height: 70px;
          font-size: 0.85rem;
        }
        .tile .btn-chat-send {
          width: 100%;
          padding: 0.5rem;
        }
      }

      /* Medium screens */
      @media (max-width: 768px) and (min-width: 601px) {
        .tile .tile-chat {
          padding: 0.6rem;
        }
      }

      /* Visual hierarchy: improve message bubbles */
      .chat-message {
        padding: 0.5rem 0.75rem;
        border-radius: 12px;
        max-width: 85%;
        line-height: 1.5;
        word-break: break-word;
        overflow-wrap: anywhere;
      }

      /* Stats with monospaced numbers for alignment */
      .chat-stats {
        font-variant-numeric: tabular-nums;
        letter-spacing: 0.02em;
      }


     </style>
</head>
<body>
<header>
  <h1>🐘 llama.cpp</h1>
  <span class="badge">{{ host }}:{{ flask_port }}</span>
  <select id="themeSelector" style="padding: .3rem .6rem; border-radius: 4px; border: 1px solid var(--surface2); background: var(--surface); color: var(--text); font-size: .85rem; margin-left: auto;">
    <option value="dark">Dark</option>
    <option value="light">Light</option>
    <option value="solarized-dark">Solarized Dark</option>
    <option value="nord">Nord</option>
  </select>
</header>
<main>

<div class="toolbar">
  <input type="search" id="search" placeholder="Filter models…">
  <button class="btn-goto" id="refreshBtn" style="background: var(--btn-goto); color: var(--bg);">↻ Refresh</button>
  <button class="btn-del" id="shutdownBtn" style="background: var(--btn-stop);">⏻ Shutdown</button>
  <button class="btn-add" id="addModelBtn">➕ Add Model</button>
  <a class="btn-goto" href="/api/logs" target="_blank">📜 Logs</a>
</div>

<!-- Download Selection Modal -->
<div id="download-modal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,.55); z-index:999; align-items:center; justify-content:center;">
  <div style="background:var(--surface); border-radius:8px; padding:1.2rem; width:min(500px,90vw); max-height:80vh; display:flex; flex-direction:column; border:1px solid var(--surface2);">
    <h3 style="margin:0 0 .8rem; color:var(--text);">Download from Hugging Face</h3>
    <p style="margin:0 0 .6rem; color:var(--text2); font-size:.85rem;" id="dl-modal-model-name"></p>
    <div id="dl-search-results" style="max-height:300px; overflow-y:auto; border:1px solid var(--surface2); border-radius:4px; background:var(--surface);"></div>
    <div style="display:flex; gap:.5rem; justify-content:flex-end; margin-top:1rem;">
      <button type="button" class="btn-cancel" id="dl-modal-cancel" style="padding:.4rem .9rem; border:none; border-radius:4px; background:var(--btn-del); color:#fff; cursor:pointer;">Cancel</button>
    </div>
  </div>
</div>

<div id="add-model-form">
    <h3>Add New Model</h3>
    <form id="addForm">
      <div style="display: flex; gap: 0.5rem; align-items: end;">
        <label>Model Name:
          <input type="text" id="modelName" name="name" required style="flex: 1;">
          <button type="button" id="searchBtn" class="btn-search">🔍 Search</button>
        </label>
      </div>
      <div id="search-results"></div>
      <label>Hugging Face Repo (e.g., bartowski/phi-4-GGUF): <input type="text" name="repo" required></label>
      <label>Filename (e.g., phi-4-Q4_K_M.gguf): <input type="text" name="filename" required></label>
      <label>Expected Size (bytes): <input type="number" name="expected_size" min="1" required></label>
      <label>Port (e.g., 11010): <input type="number" name="port" min="1" required></label>
      <label>Cache Size: <input type="number" name="cache_size" value="32768" min="1"></label>
      <label>Threads: <input type="number" name="threads" value="16" min="1"></label>
      <label>Threads Batch: <input type="number" name="threads_batch" value="22" min="1"></label>
      <label>Timeout: <input type="number" name="timeout" value="3600" min="1"></label>
      <label>Use MLock: <input type="checkbox" name="mlock" checked></label>
      <label>Flash Attn:
        <select name="flash_attn">
          <option value="off">off</option>
          <option value="on">on</option>
        </select>
      </label>
      <label>No MMap: <input type="checkbox" name="no_mmap" checked></label>
      <label>Parallel: <input type="number" name="parallel" value="4" min="1"></label>
      <label>UBatch Size: <input type="number" name="ubatch_size" value="512" min="1"></label>
      <div>
        <button type="submit" class="btn-add">Add Model</button>
        <button type="button" class="btn-cancel" onclick="toggleAddForm()">Cancel</button>
      </div>
    </form>
     </div>
     <div id="loading">
       <div class="spinner"></div>
       <div>Loading models…</div>
     </div>
     <div id="content-area" style="display: grid; gap: 1.5rem;">
       <div id="tile-grid" class="tile-grid"></div>
       <div id="properties-panel" class="properties-panel" style="display: none;">
         <div class="panel-header">
           <h2 id="panel-model-name" style="margin:0;font-size:1.1rem;"></h2>
           <button id="panel-close" class="panel-close-btn" style="position:absolute;top:.5rem;right:.5rem;background:none;border:none;color:var(--text2);font-size:1.5rem;cursor:pointer;line-height:1;">&times;</button>
         </div>
         <div id="panel-content" style="padding-top:1rem;"></div>
       </div>
     </div>
    <div id="log"></div>
  </main>

  <!-- Modal for Hugging Face repo selection -->
  <div id="hf-repo-modal" class="modal-overlay" style="display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 1000; align-items: center; justify-content: center;">
    <div class="modal-content" style="background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 8px; max-width: 600px; width: 90%; max-height: 80vh; overflow-y: auto; padding: 1.5rem; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">
      <h3 style="margin-top: 0; margin-bottom: 1rem;">Select Hugging Face Repository</h3>
      <p id="hf-modal-description" style="color: var(--muted); margin-bottom: 1rem;"></p>
      <div id="hf-repo-list" style="display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 1rem;"></div>
      <div style="display: flex; justify-content: flex-end; gap: 0.5rem;">
        <button id="hf-modal-cancel" class="btn-secondary" style="padding: 0.5rem 1rem; border: 1px solid var(--border); background: var(--bg-primary); color: var(--fg-primary); border-radius: 4px; cursor: pointer;">Cancel</button>
      </div>
   </div>
 </div>

  <!-- Chat Modal (bead web-e7r.1: modal container) -->
   <div id="chat-modal" class="modal-overlay chat-modal" role="dialog" aria-modal="true" aria-label="Chat dialog" style="display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 1100; align-items: center; justify-content: center;">
    <div class="chat-section" style="width: min(800px, 90vw); max-height: 90vh; overflow: hidden; position: relative;">
      <button id="chat-modal-close" style="position: absolute; top: 0.5rem; right: 0.5rem; background: none; border: none; color: var(--text2); font-size: 1.5rem; cursor: pointer; line-height: 1; z-index: 10;">&times;</button>
      <button id="chat-modal-clear" style="position: absolute; top: 0.5rem; left: 0.5rem; background: none; border: 1px solid var(--border); color: var(--text2); border-radius: 4px; padding: 0.25rem 0.5rem; font-size: 0.8rem; cursor: pointer; z-index: 10;">Clear</button>
      <div id="chat-modal-content" style="padding-top: 0.5rem;"></div>
    </div>
  </div>

 <script>
const HOST = "{{ host }}";
const FLAVOR = "{{ flask_port }}";
const CACHE_VERSION = "{{ cache_version }}";

// Panel state
let selectedModel = null;
let currentModelRows = []; // Cache of model data from latest refresh

function selectModel(modelName) {
  selectedModel = modelName;
  const panel = document.getElementById('properties-panel');
  const contentArea = document.getElementById('content-area');
  panel.style.display = 'block';
  contentArea.classList.add('has-panel');
  
  // Find model data
  const modelData = currentModelRows.find(r => r.model === modelName);
  if (!modelData) {
    console.error('Model data not found for', modelName);
    return;
  }
  
  // Set heading
  document.getElementById('panel-model-name').textContent = modelName;
  
  // Render properties
  renderPanelProperties(modelData, modelName);
}

function closePanel() {
  selectedModel = null;
  const panel = document.getElementById('properties-panel');
  const contentArea = document.getElementById('content-area');
  panel.style.display = 'none';
  contentArea.classList.remove('has-panel');
}

 async function renderPanelProperties(data, modelName) {
   const content = document.getElementById('panel-content');
   
   // Determine if running
   const running = data.status && data.status.startsWith('running');
   // Generate a safe ID for element IDs (no special characters)
   const safeId = modelName.replace(/[^a-zA-Z0-9]/g, '_');
   
   // Build HTML
   let html = '';
  
  // Static Properties section
  html += `<div class="panel-section">
    <h3>Properties</h3>
    <div class="panel-grid">`;
  
  // Helper to add item
  const addItem = (label, value, isCode = false) => {
    const valHtml = isCode ? `<code>${value}</code>` : value;
    html += `<div class="panel-item">
      <span class="label">${label}</span>
      <span class="value">${valHtml}</span>
    </div>`;
  };
  
  addItem('File', data.model_path ? data.model_path.split('/').pop() : (data.filename || '-'));
  addItem('Size', fmtGb(data.file_size_gb));
  addItem('Port', data.port ? `:${data.port}` : '-');
  addItem('Cache Size', data.cache_size + ' tokens');
  addItem('Threads', data.threads);
  addItem('Threads Batch', data.threads_batch);
  addItem('Timeout', data.timeout + 's');
  addItem('MLock', data.mlock ? 'Yes' : 'No');
  addItem('Flash Attn', data.flash_attn || 'off');
  addItem('No MMAP', data.no_mmap ? 'Yes' : 'No');
  addItem('Parallel', data.parallel);
  addItem('UBatch Size', data.ubatch_size);
  
  html += `</div></div>`;
  
   // Running Statistics section (only if running)
   if (running) {
     html += `<div class="panel-section">
       <h3>Running Statistics</h3>
       <div class="panel-grid">`;
     addItem('Status', '<span class="chip running">● running</span>');
     if (data.pid) addItem('PID', `<code>${data.pid}</code>`);
     html += `</div></div>`;
     
     }



     // Hugging Face Metadata section
   html += `<div class="panel-section">
     <h3>Model Information</h3>`;

   // If repo is not configured, show button to discover
   if (!data.repo) {
     html += `<div id="hf-metadata-missing" style="text-align: center; padding: 1rem 0;">
       <p style="margin-bottom: 0.75rem; color: var(--muted);">Hugging Face repository not configured.</p>
       <button id="hf-find-btn-${safeId}" class="btn-primary" data-model="${modelName}">Find on Hugging Face</button>
       <div id="hf-search-status-${safeId}" style="margin-top: 0.5rem; font-size: 0.875rem; color: var(--muted);"></div>
     </div>`;
   }

   html += `
     <div id="hf-metadata-loading" class="loading" style="display: none;">Loading Hugging Face data...</div>
     <div id="hf-metadata-content" style="display: none;"></div>
     <div id="hf-metadata-error" class="error" style="display: none;"></div>
   </div>`;
  
  content.innerHTML = html;
  
  // Fetch HF metadata asynchronously
  try {
    const resp = await fetch(`/api/hf-metadata?model=${encodeURIComponent(modelName)}`);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const msg = err.error || 'Failed to load Hugging Face metadata';
      document.getElementById('hf-metadata-error').textContent = msg;
      document.getElementById('hf-metadata-error').style.display = 'block';
      document.getElementById('hf-metadata-loading').style.display = 'none';
      return;
    }
    const meta = await resp.json();
    document.getElementById('hf-metadata-loading').style.display = 'none';
    
    let metaHtml = '<div class="panel-grid">';
    
    // Downloads
    if (meta.downloads !== undefined) {
      const dl = meta.downloads.toLocaleString();
      metaHtml += `<div class="panel-item"><span class="label">Downloads</span><span class="value">${dl}</span></div>`;
    }
    
    // Likes
    if (meta.likes !== undefined) {
      const likes = meta.likes.toLocaleString();
      metaHtml += `<div class="panel-item"><span class="label">Likes</span><span class="value">${likes}</span></div>`;
    }
    
    // Author
    if (meta.author) {
      metaHtml += `<div class="panel-item"><span class="label">Author</span><span class="value">${meta.author}</span></div>`;
    }
    
    // Last modified
    if (meta.lastModified) {
      const date = new Date(meta.lastModified).toLocaleDateString();
      metaHtml += `<div class="panel-item"><span class="label">Updated</span><span class="value">${date}</span></div>`;
    }
    
    // Tags
    if (Array.isArray(meta.tags) && meta.tags.length > 0) {
      metaHtml += `<div class="panel-item full-width"><span class="label">Tags</span><span class="value">`;
      meta.tags.slice(0, 8).forEach(tag => {
        metaHtml += `<span class="tag">${tag}</span>`;
      });
      if (meta.tags.length > 8) {
        metaHtml += `<span class="tag">+${meta.tags.length - 8} more</span>`;
      }
      metaHtml += `</span></div>`;
    }
    
    // Description (truncated)
    if (meta.description) {
      const maxLen = 200;
      const desc = meta.description.length > maxLen 
        ? meta.description.substring(0, maxLen) + '...' 
        : meta.description;
      metaHtml += `<div class="panel-item full-width"><span class="label">Description</span><span class="value" style="white-space: pre-wrap;">${desc}</span></div>`;
    }
    
    metaHtml += `</div>`;
    document.getElementById('hf-metadata-content').innerHTML = metaHtml;
    document.getElementById('hf-metadata-content').style.display = 'block';
   } catch (e) {
     document.getElementById('hf-metadata-loading').style.display = 'none';
     document.getElementById('hf-metadata-error').textContent = 'Network error while fetching metadata';
     document.getElementById('hf-metadata-error').style.display = 'block';
    }
    
   }
  
  // Render chat messages for a specific model
  function renderChatMessages(modelName) {
    const safeId = modelName.replace(/[^a-zA-Z0-9]/g, '_');
    const messagesDiv = document.getElementById(`chat-messages-${safeId}`);
    if (!messagesDiv) return;
    
    messagesDiv.innerHTML = '';
    const history = chatHistory[modelName] || [];
    
    history.forEach(msg => {
      const msgEl = document.createElement('div');
      msgEl.className = `chat-message ${msg.role}`;
      msgEl.textContent = msg.content;
      messagesDiv.appendChild(msgEl);
    });
    
    // Scroll to bottom
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  }
  
  // Send a chat message to the model
  async function sendChatMessage(modelName) {
    const safeId = modelName.replace(/[^a-zA-Z0-9]/g, '_');
    const input = document.getElementById(`chat-input-${safeId}`);
    const sendBtn = document.getElementById(`chat-send-${safeId}`);
    const messagesDiv = document.getElementById(`chat-messages-${safeId}`);
    const statsDiv = document.getElementById(`chat-stats-${safeId}`);
    const content = input.value.trim();
    if (!content) return;
    
     // Get parameters
     const temperature = parseFloat(document.getElementById(`chat-temp-${safeId}`).value);
     const top_p = parseFloat(document.getElementById(`chat-top-p-${safeId}`).value);
     const max_tokens = parseInt(document.getElementById(`chat-max-tokens-${safeId}`).value, 10);
     const timeout = parseInt(document.getElementById(`chat-timeout-${safeId}`).value, 10) || 300;
     
     // Add user message to history and UI
     chatHistory[modelName].push({ role: 'user', content });
    localStorage.setItem('chat_history_' + modelName, JSON.stringify(chatHistory[modelName]));
    input.value = '';
    localStorage.removeItem('chat_input_' + modelName);
    renderChatMessages(modelName);
    
    // Disable input and show loading
    sendBtn.disabled = true;
    input.disabled = true;
    const loadingMsg = document.createElement('div');
    loadingMsg.className = 'chat-message assistant';
    loadingMsg.textContent = 'Thinking...';
    loadingMsg.id = 'chat-loading';
    messagesDiv.appendChild(loadingMsg);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    
    const startTime = Date.now();
    
    try {
       const resp = await fetch('/api/chat', {
         method: 'POST',
         headers: { 'Content-Type': 'application/json' },
         body: JSON.stringify({
           model: modelName,
           messages: chatHistory[modelName].map(m => ({ role: m.role, content: m.content })),
           temperature,
           top_p,
           max_tokens,
           timeout
         })
       });
      
      // Remove loading indicator
      const loading = document.getElementById('chat-loading');
      if (loading) loading.remove();
      
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      
      const data = await resp.json();
      const responseTime = Date.now() - startTime;
      
      // Extract assistant message from OpenAI response format
      const assistantMessage = data.choices?.[0]?.message?.content || '';
      if (!assistantMessage) {
        throw new Error('Empty response from model');
      }
      
      // Add assistant response to history
      chatHistory[modelName].push({ role: 'assistant', content: assistantMessage });
      localStorage.setItem('chat_history_' + modelName, JSON.stringify(chatHistory[modelName]));
      renderChatMessages(modelName);
      
      // Update stats - use completion_tokens or total_tokens
      const usage = data.usage || {};
      const tokens = usage.completion_tokens || usage.total_tokens || 0;
      const tps = (tokens / (responseTime / 1000)).toFixed(1);
      statsDiv.innerHTML = `<span>✓ ${tokens} tokens</span> <span>⏱ ${responseTime}ms</span> <span>🚀 ${tps} t/s</span>`;
      
    } catch (err) {
      const loading = document.getElementById('chat-loading');
      if (loading) loading.remove();
      
      const errorMsg = document.createElement('div');
      errorMsg.className = 'chat-message error';
      errorMsg.textContent = `Error: ${err.message}`;
      messagesDiv.appendChild(errorMsg);
      statsDiv.innerHTML = `<span style="color: var(--red);">❌ ${err.message}</span>`;
      
    } finally {
      sendBtn.disabled = false;
      input.disabled = false;
      input.focus();
    }
  }
  
  function fmtGb(v) {
  if (v == null) return '<span class="chip warn">missing</span>';
  return v.toFixed(2) + ' GB';
}
function jsEscape(str) {
  return String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}
function portUrl(p) { return 'http://' + HOST + ':' + p; }

let isRefreshing = false;
// Store download progress state to preserve across refreshes
const downloadProgressState = {};
// Store chat message history per model
const chatHistory = {};
// Load saved chat histories from localStorage
for (let i = 0; i < localStorage.length; i++) {
  const key = localStorage.key(i);
  if (key && key.startsWith('chat_history_')) {
    const modelName = key.slice('chat_history_'.length);
    try {
      const saved = JSON.parse(localStorage.getItem(key));
      if (Array.isArray(saved)) {
        chatHistory[modelName] = saved;
      }
    } catch (e) {
      console.error('Failed to parse saved chat history for model', modelName, e);
    }
  }
}
async function refresh(showLoading=true) {
  if (isRefreshing) {
    console.log('Refresh already in progress, skipping');
    return;
  }
  isRefreshing = true;
  console.log('refresh() called, showLoading:', showLoading);
  if (showLoading) {
    const loadingEl = document.getElementById('loading');
    if (loadingEl) loadingEl.style.display = 'block';
  }
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000);
    const res = await fetch('/api/status?' + Date.now(), { 
      cache: 'no-store',
      signal: controller.signal
    });
    clearTimeout(timeoutId);
    console.log('Fetched /api/status, status:', res.status);
    if (!res.ok) {
      throw new Error('HTTP ' + res.status + ': ' + res.statusText);
    }
    const text = await res.text();
    console.log('Response text length:', text.length);
    let rows;
    try {
      rows = JSON.parse(text);
    } catch (e) {
      console.error('Failed to parse /api/status response:', text);
      throw e;
    }
     console.log('Received', rows.length, 'models');
     // Cache model data for panel population
     currentModelRows = rows;
    const dlRes = await fetch('/api/download-status?' + Date.now(), { 
      cache: 'no-store',
      signal: controller.signal 
    });
    const dlText = await dlRes.text();
    const dlMap = {};
    let dlJson;
    try {
      dlJson = JSON.parse(dlText);
    } catch (e) {
      console.error('Failed to parse /api/download-status response:', dlText);
      dlJson = {};
    }
    for (const [k, v] of Object.entries(dlJson)) {
      dlMap[k] = v;
    }
const q = document.getElementById('search').value.toLowerCase();
const grid = document.getElementById('tile-grid');
console.log('tile-grid element:', grid);
console.log('rows to render:', rows.length);
const filtered = rows.filter(r => r.model.toLowerCase().includes(q) || String(r.port).includes(q));
// Sort by model name, then by filename
filtered.sort((a, b) => {
  const modelCompare = a.model.localeCompare(b.model);
  if (modelCompare !== 0) return modelCompare;
  const aFilename = a.filename || (a.model_path ? a.model_path.split('/').pop() : a.model);
  const bFilename = b.filename || (b.model_path ? b.model_path.split('/').pop() : b.model);
  return aFilename.localeCompare(bFilename);
});
console.log('filtered rows:', filtered.length);
const html = filtered.map((r) => {
  const running = r.status.startsWith('running');
  console.log('Model:', r.model, 'status:', r.status, 'running:', running);
  const statusChip = running
    ? '<span class="chip ok">● running</span>'
    : r.file_exists
    ? '<span class="chip ok">● stopped</span>'
    : '<span class="chip bad">✕ missing</span>';
  const dl = dlMap[r.model] || {};
  const partial = r.partial_download || 0;
  let dlHtml = '';
  if (dl.status === 'downloading') {
    dlHtml = '<button class="download" disabled>⬇ ' + dl.progress + '%</button>';
  } else if (dl.status === 'complete') {
    dlHtml = '<button class="download" disabled>✓ Done</button>';
  } else if (dl.status === 'error') {
    dlHtml = '<button class="download" onclick="retryDownload(jsEscape(r.model), true)">🔄 Retry</button>';
  } else if (partial > 0) {
    dlHtml = '<button class="download" onclick="resumeDownload(jsEscape(r.model), ' + partial + ')">▶ Resume</button>';
  } else {
    const filename = r.filename || (r.model_path ? r.model_path.split('/').pop() : r.model);
    dlHtml = '<button class="download" onclick="downloadByFilename(\'' + jsEscape(r.model) + '\', \'' + jsEscape(filename) + '\', true)">⬇ ' + (r.file_exists ? 'Re-download' : 'Download') + '</button>';
  }
  const portCell = r.file_exists
    ? '<a class="actions btn-goto" style="padding:.3rem .7rem;border-radius:4px;font-size:.8rem;color:#fff;background:var(--blue);text-decoration:none" href="' + portUrl(r.port) + '" target="_blank">:' + r.port + '</a>'
    : '<span class="chip bad">-</span>';
  // Check if there's an active download - use dl from API or stored progress state
  const apiProgress = dlMap[r.model];
  const dlProgress = (apiProgress && apiProgress.status) 
    ? apiProgress 
    : (downloadProgressState[r.model] || {});
  // Show progress bar if status is downloading OR if there's active progress (progress > 0)
  const showProgress = dlProgress.status === 'downloading' || (dlProgress.progress && dlProgress.progress > 0);
  const progressHtml = showProgress
    ? '<div id="progress-' + r.model.replace(/[^a-zA-Z0-9]/g, '_') + '" class="download-progress" style="margin-top: .5rem; padding: .5rem; background: var(--surface2); border-radius: 4px;">'
      + '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: .25rem;">'
      + '<strong style="font-size: .85rem;">Download Progress</strong>'
      + '<span id="progress-text-' + r.model.replace(/[^a-zA-Z0-9]/g, '_') + '" style="font-size: .75rem;">' + (dlProgress.progress || 0) + '%</span>'
      + '</div>'
      + '<div style="background: var(--surface); border-radius: 4px; height: 6px; overflow: hidden;">'
      + '<div id="progress-bar-' + r.model.replace(/[^a-zA-Z0-9]/g, '_') + '" style="background: var(--btn-download); height: 100%; width: ' + (dlProgress.progress || 0) + '%; transition: width 0.2s;"></div>'
      + '</div>'
      + '<div id="progress-details-' + r.model.replace(/[^a-zA-Z0-9]/g, '_') + '" style="font-size: .7rem; color: var(--text2); margin-top: .25rem;">' + (dlProgress.speed_mbps ? dlProgress.speed_mbps + ' MB/s' : '') + '</div>'
      + '</div>'
     : '';
    const safeId = r.model.replace(/[^a-zA-Z0-9]/g, '_');

    return '<div class="tile ' + (running ? 'running' : '') + '" data-model="' + jsEscape(r.model) + '" onclick="selectModel(\'' + jsEscape(r.model) + '\')">'
    + '<div class="tile-header"><b>' + jsEscape(r.model) + '</b>' + statusChip + '</div>'
    + '<div class="tile-body"><code title="' + jsEscape(r.model_path) + '">' + jsEscape(r.model_path ? r.model_path.split('/').pop() : r.filename) + '</code>'
    + '<div>' + fmtGb(r.file_size_gb) + ' · ' + r.status + (r.pid ? ' · pid ' + r.pid : '') + '</div>'
    + '<div>' + portCell + '</div>'
    + '</div>'
    + '<div class="tile-actions actions">'
    + dlHtml
    + ' <button class="run" onclick="act(\'' + jsEscape(r.model) + '\', \'start\')" ' + (running || !r.file_exists ? 'disabled' : '') + '>▶ Start</button>'
    + ' <button class="stop" onclick="act(\'' + jsEscape(r.model) + '\', \'stop\')" ' + (!running ? 'disabled' : '') + '>■ Stop</button>'
    + ' <button class="add" onclick="downloadModel(\'' + jsEscape(r.model) + '\', true)">＋ Add Variant</button>'
    + ' <button class="del" onclick="promptDeleteModel(\'' + jsEscape(r.model) + '\')">✕ Delete</button>'
    + (running ? ' <button class="chat" onclick="event.stopPropagation(); openChatModal(\'' + jsEscape(r.model) + '\')">💬 Chat</button>' : '')
    + '</div>'
    + progressHtml
    + '</div>';
  }).join('');
    console.log('Setting innerHTML, length:', html.length);


     grid.innerHTML = html;



    // If a model is selected, refresh the panel to reflect any state changes
    if (selectedModel) {
      const modelData = currentModelRows.find(r => r.model === selectedModel);
      if (modelData) {
        renderPanelProperties(modelData, selectedModel);
      } else {
        // Selected model no longer exists in the list
        closePanel();
      }
    }
  } catch (e) {
    console.error('Refresh failed:', e);
  } finally {
    const loadingEl = document.getElementById('loading');
    if (loadingEl) loadingEl.style.display = 'none';
    isRefreshing = false;
  }
}

async function act(model, verb) {
  const btn = event.target;
  console.log('act() called with model:', model, 'verb:', verb, 'btn:', btn);
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = verb === 'start' ? 'Starting…' : 'Stopping…';
  try {
    const res = await fetch(`/api/${verb}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ model }),
    });
    console.log('Fetch response:', res.status, res.statusText);
    const text = await res.text();
    console.log('Response text:', text);
    let data;
    try {
      data = JSON.parse(text);
    } catch (parseError) {
      log(`<b>${prev} error:</b> Server returned non-JSON response`, 'red');
      console.error('Failed to parse response:', text);
      return;
    }
    console.log('Parsed data:', data);
    if (data.error) { log('<b>' + prev + ' error:</b> ' + data.error, 'red'); }
    else { log(`<b>${prev}:</b> ${model} → ${data.status}${data.pid ? ' (pid ' + data.pid + ')' : ''}`, 'green'); }
    // For stop, wait a bit longer and force refresh to ensure status is updated
    const delay = verb === 'stop' ? 1200 : 800;
    console.log('Calling refresh after', delay, 'ms');
    // Capture btn reference for setTimeout
    const capturedBtn = btn;
    const capturedPrev = prev;
    setTimeout(() => { 
      capturedBtn.disabled = false; 
      capturedBtn.textContent = capturedPrev; 
      console.log('Calling refresh()');
      refresh(true); 
    }, delay);
  } catch (e) {
    console.error('act() error:', e);
    log('<b>Error:</b> ' + e, 'red');
    setTimeout(() => { btn.disabled = false; btn.textContent = prev; }, 800);
  }
}

async function testStatus() {
  console.log('testStatus() called');
  try {
    const res = await fetch('/api/debug/deepseek-coder-7b');
    console.log('Response:', res.status, res.statusText);
    const data = await res.json();
    console.log('Debug status:', data);
    alert('Debug: ' + JSON.stringify(data, null, 2));
  } catch (e) {
    console.error('testStatus error:', e);
    alert('Error: ' + e);
  }
}

async function downloadModel(model, force = false) {
  console.log('downloadModel called for:', model, 'force:', force);
  // First, search HF for the model using the known name
  try {
    const res = await fetch('/api/download-search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ model, force })
    });
    const data = await res.json();
    console.log('download-search response:', data);
    
    if (data.error) {
      log('<b>Download error:</b> ' + data.error, 'red');
      return;
    }
    
    // If already complete, show message
    if (data.status === 'complete') {
      log(`<b>${model}:</b> already downloaded (${fmtSize(data.size)})`, 'green');
      return;
    }
    
    // If download started automatically (single result), show progress
    if (data.status === 'download started') {
      log(`<b>Download:</b> ${model} started`, 'green');
      // Small delay to let the download worker set initial status
      setTimeout(() => {
        showProgressBar(model, data.results?.[0]?.size || 0);
        startProgressListener(model);
        refresh();
      }, 100);
      return;
    }
    
    // If multiple results, show modal for selection
    if (data.status === 'multiple_results') {
      console.log('Showing modal with', data.results.length, 'results');
      showDownloadModal(model, data.results, force);
    }
  } catch (err) {
    log('<b>Error:</b> ' + err, 'red');
  }
}

async function downloadByFilename(model, filename, force = false) {
  console.log('downloadByFilename called for:', model, 'filename:', filename, 'force:', force);
  // Search HF for the specific filename
  try {
    const res = await fetch('/api/download-search-filename', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ filename, force })
    });
    const data = await res.json();
    console.log('download-search-filename response:', data);
    
    if (data.error) {
      log('<b>Download error:</b> ' + data.error, 'red');
      return;
    }
    
     // If single exact match, trigger download automatically
     if (data.status === 'single_result') {
       console.log('Single result found, starting download automatically');
       const result = data.results[0];
       _triggerDownload(model, result.url, result.size, force);
       return;
     }
    
    // If multiple results, show modal for selection
    if (data.status === 'multiple_results') {
      console.log('Showing modal with', data.results.length, 'results for filename:', filename);
      // Pass the model name (not filename) to the modal so the download uses the correct entry
      showDownloadModal(model, data.results, force);
    }
  } catch (err) {
    log('<b>Error:</b> ' + err, 'red');
  }
}

async function retryDownload(model, resume = false) {
  // Check if there's an existing partial download
  const statusRes = await fetch('/api/download-status');
  const status = await statusRes.json();
  const dlStatus = status[model] || {};
  
  if (dlStatus.status === 'error' && resume && confirm(`Resume interrupted download for ${model}?`)) {
    downloadWithResume(model, true);
  } else if (dlStatus.status === 'error' && confirm(`Retry failed download for ${model}?`)) {
    downloadModel(model, true);  // Use force for retry
  } else {
    downloadModel(model, true);  // Use force for retry
  }
}

async function resumeDownload(model, partialBytes) {
  if (!confirm(`Resume download for ${model}? (${fmtSize(partialBytes)} already downloaded)`)) {
    return;
  }
  downloadWithResume(model, true, partialBytes);
}

async function downloadWithResume(model, resume, partialBytes = 0, force = false) {
  // Get the download URL
  const fileRes = await fetch('/api/model-file', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ model })
  });
  const fileInfo = await fileRes.json();
  
  if (fileInfo.error) {
    log('<b>Error:</b> ' + fileInfo.error, 'red');
    return;
  }
  
  const response = await fetch('/api/download', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ 
      model, 
      url: fileInfo.url, 
      expected_size: fileInfo.size || 0,
      resume: resume,
      force: force
    })
  });
  const data = await response.json();
  
  if (data.error) {
    log('<b>Resume error:</b> ' + data.error, 'red');
  } else {
    log(`<b>Download:</b> ${model} resumed`, 'green');
    showProgressBar(model, fileInfo.size || 0);
    startProgressListener(model);
  }
}

function showDownloadModal(model, results, force = false) {
  const modal = document.getElementById('download-modal');
  const modelNameText = document.getElementById('dl-modal-model-name');
  const resultsDiv = document.getElementById('dl-search-results');
  
  modelNameText.textContent = 'Select a file to download:';
  resultsDiv.innerHTML = results.map(r => `
    <div data-url="${r.url}" data-size="${r.size}" style="padding:.5rem; cursor:pointer; border-bottom:1px solid var(--surface2);">
      <strong>${r.filename}</strong><br>
      <small>Size: ${fmtSize(r.size)} • Downloads: ${r.downloads.toLocaleString()} • Likes: ${r.likes}</small>
    </div>
  `).join('');
  resultsDiv.style.display = 'block';
  modal.style.display = 'flex';
  
  // Click handler on results
  resultsDiv.querySelectorAll('div').forEach(div => {
    div.addEventListener('click', function() {
      const url = this.getAttribute('data-url');
      const size = parseInt(this.getAttribute('data-size')) || 0;
      modal.style.display = 'none';
      _triggerDownload(model, url, size, force);
    });
  });
  
  // Cancel handler
  document.getElementById('dl-modal-cancel').onclick = () => { 
    modal.style.display = 'none'; 
  };
}

function fmtSize(bytes) {
  if (!bytes || bytes === 0) return 'Unknown size';
  const gb = bytes / (1024 ** 3);
  return gb.toFixed(2) + ' GB';
}

function fmtSpeed(bytesPerSecond) {
  if (!bytesPerSecond) return '';
  const mbps = bytesPerSecond / (1024 * 1024);
  return mbps.toFixed(1) + ' MB/s';
}

// Progress bar UI - updates state, progress bar is rendered by refresh()
function showProgressBar(model, expectedSize) {
  console.log('showProgressBar called for:', model, 'size:', expectedSize);
  
  // Initialize progress state for this model
  downloadProgressState[model] = {
    status: 'downloading',
    progress: 0,
    downloaded: 0,
    size: expectedSize || 0,
    speed_mbps: 0
  };
  console.log('Progress state initialized for', model);
}

// SSE listener for download progress
function startProgressListener(model) {
  console.log('startProgressListener called for:', model);
  const eventSource = new EventSource(`/api/download-stream/${encodeURIComponent(model)}`);
  
  eventSource.onopen = function(e) {
    console.log('SSE connection opened for', model);
  };
  
  eventSource.onmessage = function(e) {
    console.log('SSE message received:', e.data);
    try {
      const data = JSON.parse(e.data);
      const progress = data.progress || 0;
      const downloaded = data.downloaded || 0;
      const size = data.size || 0;
      const speed = data.speed_mbps || 0;
      
      // Initialize or update the stored state for persistence across refreshes
      if (!downloadProgressState[model]) {
        downloadProgressState[model] = { status: 'downloading', progress: 0, downloaded: 0, size: 0, speed_mbps: 0 };
      }
      downloadProgressState[model].status = data.status || 'downloading';
      downloadProgressState[model].progress = progress;
      downloadProgressState[model].downloaded = downloaded;
      downloadProgressState[model].size = size;
      downloadProgressState[model].speed_mbps = speed;
      // Sanitize details to prevent XSS
      const detailsText = data.status === 'error' 
        ? 'Error: ' + (data.error || 'Unknown error')
        : data.status === 'complete' 
          ? 'Complete!' 
          : (speed ? speed.toFixed(1) + ' MB/s' + ' · ' : '');
      downloadProgressState[model].details = detailsText.replace(/</g, '&lt;').replace(/>/g, '&gt;');
      
      if (data.status === 'complete' || data.status === 'error') {
        setTimeout(() => {
          eventSource.close();
          // Clean up progress state after completion
          setTimeout(() => {
            delete downloadProgressState[model];
          }, 5000);
        }, 500);
      }
    } catch (err) {
      console.error('Progress parse error:', err);
    }
  };
  
  eventSource.onerror = function(e) {
    console.log('SSE closed for', model, e);
    eventSource.close();
  };
}

async function _triggerDownload(model, url, size, force = false) {
  console.log('_triggerDownload called:', { model, url, size, force });
  
  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ model, url, expected_size: size || 0, force })
    });
    const data = await res.json();
    console.log('download response:', data);
    
    if (data.error) {
      log('<b>Download error:</b> ' + data.error, 'red');
      return;
    }
    
    // Show appropriate message for variant vs regular download
    if (data.is_variant) {
      log(`<b>New variant created:</b> ${data.model}`, 'green');
    } else {
      log(`<b>Download:</b> ${model} starting...`, 'green');
    }
    
    // Show progress bar for active downloads
    showProgressBar(data.model, size || 0);
    
    if (data.status === 'complete') {
      log(`<b>${model}:</b> already downloaded (${fmtSize(data.size)})`, 'green');
      // Update progress bar to show complete
      setTimeout(() => {
        const safeModelId = data.model.replace(/[^a-zA-Z0-9]/g, '_');
        const bar = document.getElementById('progress-bar-' + safeModelId);
        const text = document.getElementById('progress-text-' + safeModelId);
        const details = document.getElementById('progress-details-' + safeModelId);
        if (bar) bar.style.width = '100%';
        if (text) text.textContent = '100% (already downloaded)';
        if (details) details.textContent = 'Complete!';
      }, 100);
      return;
    }
    
    // Small delay to ensure progress bar is rendered before SSE starts
    await new Promise(resolve => setTimeout(resolve, 50));
    
    // Use data.model for progress listener (handles variant case)
    startProgressListener(data.model);
    refresh();
  } catch (e) {
    log('<b>Error:</b> ' + e, 'red');
  }
}

function toggleAddForm() {
   const form = document.getElementById('add-model-form');
   form.style.display = form.style.display === 'none' ? 'block' : 'none';
   if (form.style.display === 'block') {
     document.getElementById('modelName').focus();
   }
 }

 function showAddForm() {
   const form = document.getElementById('add-model-form');
   form.style.display = 'block';
   document.getElementById('modelName').focus();
 }

 async function promptAddModel(existingModel) {
   showAddForm();
 }

async function promptDeleteModel(model) {
  if (!confirm(`Delete model "${model}"? This will remove it from the list.\\nCheck "Delete files" to also remove the GGUF file.`)) {
    return;
  }
  const deleteFiles = confirm(`Also delete the GGUF file on disk?`);
  try {
    const res = await fetch('/api/delete-model', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ model, delete_files: deleteFiles })
    });
    const data = await res.json();
    if (data.error) {
      alert('Error: ' + data.error);
    } else {
      log(`<b>Delete:</b> ${model} → ${data.status}`, 'green');
      refresh();
    }
  } catch (e) {
    log('<b>Error:</b> ' + e, 'red');
  }
}

function log(msg, color) {
  const el = document.getElementById('log');
  const ts = new Date().toISOString().slice(11, 19);
  el.innerHTML = `<span style="color:${color || 'inherit'}">[${ts}] ${msg}</span>\n` + el.innerHTML;
  if (el.children.length > 200) el.innerHTML = el.innerHTML.slice(0, 8000);
}

// Handle add form submission
document.getElementById('addForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  const formData = new FormData(e.target);
  const data = {};
  formData.forEach((value, key) => {
    if (key === 'mlock' || key === 'no_mmap') {
      data[key] = formData.get(key) === 'on';
    } else if (['expected_size', 'port', 'cache_size', 'threads', 'threads_batch', 'timeout', 'parallel', 'ubatch_size'].includes(key)) {
      data[key] = parseInt(value, 10);
    } else {
      data[key] = value;
    }
  });
  const submitBtn = e.target.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  submitBtn.textContent = 'Adding…';
  try {
    const res = await fetch('/api/add-model', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    });
    const result = await res.json();
    if (result.error) {
      log('<b>Add error:</b> ' + result.error, 'red');
      alert('Error: ' + result.error);
    } else {
      log(`<b>Add:</b> ${data.name} → ${result.status}`, 'green');
      toggleAddForm();
       e.target.reset();
       refresh();
     }
   } catch (err) {
     log('<b>Error:</b> ' + err, 'red');
     alert('Error: ' + err);
   } finally {
     submitBtn.disabled = false;
     submitBtn.textContent = 'Add Model';
   }
});

// Hugging Face search functionality
document.getElementById('searchBtn').addEventListener('click', async function() {
  const query = document.getElementById('modelName').value.trim();
  if (!query) {
    alert('Please enter a model name to search');
    return;
  }
  const btn = this;
  btn.disabled = true;
  btn.textContent = 'Searching…';
  try {
    const res = await fetch('/api/search-models', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ query })
    });
    const results = await res.json();
    const resultsDiv = document.getElementById('search-results');
    if (results.length === 0) {
      resultsDiv.innerHTML = '<div style="padding: 0.5rem; color: var(--text2);">No models found</div>';
      resultsDiv.style.display = 'block';
      return;
    }
    resultsDiv.innerHTML = results.map(r => `
      <div data-model="${r.modelId}" data-filename="${r.filename}" data-size="${r.size}">
        <strong>${r.modelId}</strong><br>
        <small>Downloads: ${r.downloads.toLocaleString()} • Likes: ${r.likes}</small>
      </div>
    `).join('');
    resultsDiv.style.display = 'block';
    
     // Add click handlers to results
     resultsDiv.querySelectorAll('div').forEach(div => {
       div.addEventListener('click', async function() {
         const modelId = this.getAttribute('data-model');
         const filename = this.getAttribute('data-filename');
         const size = parseInt(this.getAttribute('data-size')) || 0;
         
         // Fill the form
         document.getElementById('modelName').value = modelId.split('/').pop() || modelId;
         document.querySelector('input[name="repo"]').value = modelId;
         document.querySelector('input[name="filename"]').value = filename;
         document.querySelector('input[name="expected_size"]').value = size;
         
         // Suggest a port (find next available port starting from 11000)
         const portResp = await fetch('/api/next-port');
         const portData = await portResp.json();
         document.querySelector('input[name="port"]').value = portData.port;
         
         // Hide results
         document.getElementById('search-results').style.display = 'none';
         btn.disabled = false;
         btn.textContent = '🔍 Search';
       });
     });
  } catch (err) {
    log('<b>Search error:</b> ' + err, 'red');
    alert('Search failed: ' + err);
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 Search';
  }
 });

// Debounce search input to avoid excessive requests
let searchTimeout;
document.getElementById('search').addEventListener('input', () => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(refresh, 300);
});
// Theme switching
  const themeSelector = document.getElementById('themeSelector');
  const savedTheme = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', savedTheme);
  themeSelector.value = savedTheme;
  themeSelector.addEventListener('change', function() {
    const theme = this.value;
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  });

document.getElementById('refreshBtn').addEventListener('click', refresh);
document.getElementById('shutdownBtn').addEventListener('click', async function() {
  if (!confirm('Shutdown the web application? This will stop the Flask server.')) {
    return;
  }
  try {
    const res = await fetch('/api/shutdown', { method: 'POST' });
    const data = await res.json();
    console.log('Shutdown response:', data);
    alert('Server shutting down...');
  } catch (e) {
    console.error('Shutdown error:', e);
  }
});
document.getElementById('addModelBtn').addEventListener('click', showAddForm);
document.getElementById('panel-close').addEventListener('click', closePanel);
   refresh();
   setInterval(() => refresh(false), 5000);

 // Hugging Face repo discovery
 let currentRepoSearchModel = null;
 const hfModal = document.getElementById('hf-repo-modal');
 const hfRepoList = document.getElementById('hf-repo-list');
 const hfModalCancel = document.getElementById('hf-modal-cancel');

 function updateModelRepo(modelName, repo) {
   fetch('/api/update-model-repo', {
     method: 'POST',
     headers: { 'Content-Type': 'application/json' },
     body: JSON.stringify({ model: modelName, repo: repo })
   })
   .then(resp => resp.json())
   .then(data => {
     if (data.error) throw new Error(data.error);
     // Refresh panel to show metadata
     selectModel(modelName);
   })
   .catch(err => {
     const statusEl = document.getElementById(`hf-search-status-${modelName.replace(/[^a-zA-Z0-9]/g, '_')}`);
     if (statusEl) statusEl.textContent = 'Error: ' + err.message;
   });
 }

 function showRepoSelectionModal(modelName, results) {
   currentRepoSearchModel = modelName;
   hfRepoList.innerHTML = '';
   const desc = document.getElementById('hf-modal-description');
   desc.textContent = `Found ${results.length} result(s). Select the correct Hugging Face repository.`;

   // Aggregate results by repo
   const reposMap = new Map();
   results.forEach(r => {
     if (!reposMap.has(r.repo)) {
       reposMap.set(r.repo, { repo: r.repo, files: [], downloads: r.downloads, likes: r.likes });
     }
     reposMap.get(r.repo).files.push(r);
   });

   reposMap.forEach(item => {
     const card = document.createElement('div');
     card.className = 'hf-repo-card';
     card.style.cssText = 'padding: 0.75rem; border: 1px solid var(--border); border-radius: 6px; background: var(--surface1); cursor: pointer; transition: background 0.2s;';
     card.innerHTML = `
       <div style="display: flex; justify-content: space-between; align-items: start;">
         <div style="flex: 1;">
           <div style="font-weight: 600; color: var(--text); margin-bottom: 0.25rem;">${item.repo}</div>
           <div style="font-size: 0.8rem; color: var(--muted);">${item.files.length} GGUF file(s)</div>
         </div>
         <div style="text-align: right; font-size: 0.85rem; color: var(--text2);">
           <div>⬇ ${item.downloads.toLocaleString()}</div>
           <div>♥ ${item.likes.toLocaleString()}</div>
         </div>
       </div>
     `;
     card.addEventListener('click', () => {
       hfModal.style.display = 'none';
       updateModelRepo(modelName, item.repo);
     });
     card.addEventListener('mouseenter', () => card.style.background = 'var(--surface2)');
     card.addEventListener('mouseleave', () => card.style.background = 'var(--surface1)');
     hfRepoList.appendChild(card);
   });

   hfModal.style.display = 'flex';
  }

  // Select the best Hugging Face repo based on downloads and likes
  function selectBestRepo(results) {
    const reposMap = new Map();
    results.forEach(r => {
      if (!reposMap.has(r.repo)) {
        reposMap.set(r.repo, { repo: r.repo, downloads: r.downloads, likes: r.likes, fileCount: 0 });
      }
      reposMap.get(r.repo).fileCount++;
    });
    const repos = Array.from(reposMap.values());
    repos.sort((a, b) => {
      const scoreA = a.downloads + a.likes;
      const scoreB = b.downloads + b.likes;
      if (scoreB !== scoreA) return scoreB - scoreA;
      return b.downloads - a.downloads;
    });
    return repos[0];
  }

  // Attach click handlers to "Find on Hugging Face" buttons
  document.addEventListener('click', function(e) {
   if (e.target && e.target.id && e.target.id.startsWith('hf-find-btn-')) {
     const modelName = e.target.getAttribute('data-model');
     if (!modelName) return;
     const safeId = modelName.replace(/[^a-zA-Z0-9]/g, '_');
     const statusEl = document.getElementById(`hf-search-status-${safeId}`);
     if (statusEl) statusEl.textContent = 'Searching Hugging Face...';

     fetch('/api/hf-repo-search', {
       method: 'POST',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify({ model: modelName })
     })
     .then(resp => resp.json())
     .then(data => {
       if (data.error) {
         throw new Error(data.error);
       }
       if (statusEl) statusEl.textContent = '';
        // Auto-select best repo based on downloads and likes
        const best = selectBestRepo(data.results);
        if (statusEl) {
          statusEl.textContent = `Selected: ${best.repo} (↓${best.downloads.toLocaleString()} ♥${best.likes.toLocaleString()})`;
        }
        updateModelRepo(modelName, best.repo);
     })
     .catch(err => {
       if (statusEl) statusEl.textContent = 'Error: ' + err.message;
     });
   }
 });

 if (hfModalCancel) {
   hfModalCancel.addEventListener('click', () => {
     hfModal.style.display = 'none';
     currentRepoSearchModel = null;
   });
 }
 // Close modal on outside click
  hfModal.addEventListener('click', (e) => {
    if (e.target === hfModal) {
      hfModal.style.display = 'none';
      currentRepoSearchModel = null;
    }
  });

  // Modal Manager (web-e7r.2)
  const ModalManager = {
    activeModal: null,
    lastFocusedElement: null,
    focusableSelector: 'a, button, textarea, input, select, [tabindex]:not([tabindex="-1"])',
    
    open(modalId) {
      const modal = document.getElementById(modalId);
      if (!modal) return;
      
      // Save current focus
      this.lastFocusedElement = document.activeElement;
      
      // Show modal
      modal.style.display = 'flex';
      modal.setAttribute('aria-hidden', 'false');
      
      // Trap focus - find all focusable elements
      const focusable = Array.from(modal.querySelectorAll(this.focusableSelector));
      if (focusable.length > 0) {
        // Use setTimeout to ensure modal is visible before focusing
        setTimeout(() => focusable[0].focus(), 0);
      }
      
      // Attach event listeners
      this._attachHandlers(modal);
      
      this.activeModal = modal;
    },
    
    close(modalId) {
      const modal = document.getElementById(modalId);
      if (!modal) return;
      
      // Hide modal
      modal.style.display = 'none';
      modal.setAttribute('aria-hidden', 'true');
      
      // Remove event listeners
      this._detachHandlers(modal);
      
      // Restore focus to the element that opened the modal
      if (this.lastFocusedElement) {
        this.lastFocusedElement.focus();
      }
      
      this.activeModal = null;
    },
    
    _attachHandlers(modal) {
      const handleKeydown = (e) => this._onKeyDown(e, modal);
      const handleClick = (e) => this._onClick(e, modal);
      
      // Store references for removal
      modal._modalKeydownHandler = handleKeydown;
      modal._modalClickHandler = handleClick;
      
      modal.addEventListener('keydown', handleKeydown);
      modal.addEventListener('click', handleClick);
    },
    
    _detachHandlers(modal) {
      if (modal._modalKeydownHandler) {
        modal.removeEventListener('keydown', modal._modalKeydownHandler);
        delete modal._modalKeydownHandler;
      }
      if (modal._modalClickHandler) {
        modal.removeEventListener('click', modal._modalClickHandler);
        delete modal._modalClickHandler;
      }
    },
    
    _onKeyDown(e, modal) {
      // Escape key closes modal
      if (e.key === 'Escape') {
        this.close(modal.id);
        return;
      }
      
      // Tab key: trap focus within modal
      if (e.key === 'Tab') {
        const focusable = Array.from(modal.querySelectorAll(this.focusableSelector));
        if (focusable.length === 0) return;
        
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    },
    
    _onClick(e, modal) {
      // Close when clicking on the backdrop (overlay)
      if (e.target === modal) {
        this.close(modal.id);
      }
    }
  };

  // Chat Modal Specific Setup
  document.addEventListener('DOMContentLoaded', function() {
    const chatModal = document.getElementById('chat-modal');
    const chatCloseBtn = document.getElementById('chat-modal-close');
    
    if (chatCloseBtn) {
      chatCloseBtn.addEventListener('click', () => ModalManager.close('chat-modal'));
     }

     // Clear chat button handler
     const chatClearBtn = document.getElementById('chat-modal-clear');
     if (chatClearBtn) {
       chatClearBtn.addEventListener('click', () => {
         const modelName = chatModal.getAttribute('data-model-name');
         if (modelName) {
           if (chatHistory[modelName]) {
             chatHistory[modelName] = [];
           }
           const safeId = modelName.replace(/[^a-zA-Z0-9]/g, '_');
           const messagesDiv = document.getElementById(`chat-messages-${safeId}`);
           if (messagesDiv) {
             messagesDiv.innerHTML = '';
           }
           const input = document.getElementById(`chat-input-${safeId}`);
           if (input) {
             input.value = '';
           }
           localStorage.removeItem('chat_input_' + modelName);
           localStorage.removeItem('chat_history_' + modelName);
         }
       });
     }

     // Render chat interface inside modal (bead web-e7r.3)
    function renderChatInModal(modelName) {
      const safeId = modelName.replace(/[^a-zA-Z0-9]/g, '_');
      const contentDiv = document.getElementById('chat-modal-content');
      if (!contentDiv) return;

      // Ensure chat history exists
      if (!chatHistory[modelName]) {
        chatHistory[modelName] = [];
      }

       // Get model data for performance metrics
       const modelData = currentModelRows.find(r => r.model === modelName);
       const isRunning = modelData && modelData.status && modelData.status.startsWith('running');
       const ppVal = isRunning && modelData.pp ? modelData.pp + ' ms' : '-';
       const tgVal = isRunning && modelData.tg ? modelData.tg + ' t/s' : '-';
       const pgVal = isRunning && modelData.pg ? modelData.pg : '-';

       // Build chat UI
       contentDiv.innerHTML = `
        <div class="tile-chat modal-chat">
          <div class="chat-params">
            <div class="panel-item"><span class="label">Temperature</span>
              <input type="range" id="chat-temp-${safeId}" min="0" max="2" step="0.1" value="0.7"
                     oninput="document.getElementById('chat-temp-val-${safeId}').textContent = this.value">
              <span class="value" id="chat-temp-val-${safeId}">0.7</span>
            </div>
            <div class="panel-item"><span class="label">Top P</span>
              <input type="range" id="chat-top-p-${safeId}" min="0" max="1" step="0.05" value="0.9"
                     oninput="document.getElementById('chat-top-p-val-${safeId}').textContent = this.value">
              <span class="value" id="chat-top-p-val-${safeId}">0.9</span>
            </div>
             <div class="panel-item"><span class="label">Max Tokens</span>
               <input type="number" id="chat-max-tokens-${safeId}" min="1" max="4096" value="512">
             </div>
             <div class="panel-item"><span class="label">Timeout (s)</span>
               <input type="number" id="chat-timeout-${safeId}" min="1" max="600" value="300">
             </div>
            </div>
           <div class="panel-section">
             <h3>Performance Metrics</h3>
             <div class="panel-grid">
               <div class="panel-item"><span class="label">Prompt Processing</span><span class="value">${ppVal}</span></div>
               <div class="panel-item"><span class="label">Text Generation</span><span class="value">${tgVal}</span></div>
               <div class="panel-item"><span class="label">Combined</span><span class="value">${pgVal}</span></div>
             </div>
           </div>
           <div class="chat-messages" id="chat-messages-${safeId}"></div>
          <div class="chat-input-area">
            <textarea id="chat-input-${safeId}" placeholder="Type your message..." rows="3"></textarea>
            <button id="chat-send-${safeId}" class="btn-chat-send">Send</button>
          </div>
          <div class="chat-stats" id="chat-stats-${safeId}"></div>
        </div>
      `;

      // Render existing messages
      renderChatMessages(modelName);

      // Restore input from localStorage
      const savedInput = localStorage.getItem('chat_input_' + modelName);
      const input = document.getElementById(`chat-input-${safeId}`);
      if (input && savedInput !== null) {
        input.value = savedInput;
      }

      // Attach send button handler
      const sendBtn = document.getElementById(`chat-send-${safeId}`);
      if (sendBtn) {
        sendBtn.onclick = () => sendChatMessage(modelName);
      }

      // Attach textarea Enter-to-send (Shift+Enter for newline)
      if (input) {
        input.addEventListener('keydown', function(e) {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage(modelName);
          }
        });
        // Save draft on input changes
        input.addEventListener('input', () => {
          localStorage.setItem('chat_input_' + modelName, input.value);
        });
        // Focus input
        input.focus();
      }
    }

    // Global function to open chat modal (called by tile button)
    window.openChatModal = function(modelName) {
      // Store model name on modal for later use
      chatModal.setAttribute('data-model-name', modelName);

      // Render chat UI in modal
      renderChatInModal(modelName);

      // Open modal using manager
      ModalManager.open('chat-modal');
    };
  });

   </script>
  </body>
  </html>"""


# ──────────────────────────────────────────────────────────────────────────────
# Entry-point
# ──────────────────────────────────────────────────────────────────────────────
FLASK_PORT: int = 5000


def main() -> None:
    global FLASK_PORT
    parser = argparse.ArgumentParser(description="llama.cpp web frontend")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Flask listen port (default 5000)")
    args = parser.parse_args()
    FLASK_PORT = args.port
    log.info("Starting web UI on http://%s:%d from %s", args.host, args.port, BASE_DIR)
    log.info("Loading models cache...")
    load_models_cache()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
