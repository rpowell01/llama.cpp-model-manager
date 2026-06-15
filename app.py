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
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, render_template_string, request
import requests  # For Hugging Face API calls

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_JSON = BASE_DIR / "models.json"
LOG_FILE = BASE_DIR / "running_models.log"
ENV_FILE = BASE_DIR / ".env"

DEFAULTS: dict[str, Any] = {
    "host": "0.0.0.0",
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
LLAMA_SERVER = BASE_DIR / "llama-server.exe"
if WINDOWS and not LLAMA_SERVER.exists():
    LLAMA_SERVER = BASE_DIR / "llama-server"

PYTHON = sys.executable

# Download status tracking
download_status: Dict[str, Dict[str, Any]] = {}
download_lock = threading.Lock()

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(BASE_DIR / "webapp.log"), logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("llamaweb")

# ──────────────────────────────────────────────────────────────────────────────
# Flask app setup
# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)

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


def load_models() -> dict[str, dict[str, Any]]:
    if not MODELS_JSON.exists():
        return {}
    with open(MODELS_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_models(models: dict[str, dict[str, Any]]) -> None:
    with open(MODELS_JSON, "w", encoding="utf-8") as fh:
        json.dump(models, fh, indent=2)


def log_event(action: str, model: str, port: int | str, pid: int | str | None, status: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = json.dumps({"time": ts, "action": action, "model": model, "port": port, "pid": pid, "status": status})
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(entry + "\n")
    log.info(entry)


def find_pid_by_port(port: int) -> int | None:
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


def check_port_active(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def build_cmd(model: str, port: int | str, meta: dict[str, Any]) -> list[str]:
    cmd: list[str] = [
        str(LLAMA_SERVER),
        "-m",
        str(BASE_DIR / meta["model_path"]),
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
    return cmd


# ──────────────────────────────────────────────────────────────────────────────
# Download logic
# ──────────────────────────────────────────────────────────────────────────────
def _download_worker(model_name: str, url: str, expected_size: int, token: str | None):
    """Background worker to download a model file."""
    try:
        # Determine output path from models.json
        models = load_models()
        if model_name not in models:
            with download_lock:
                download_status[model_name] = {"status": "error", "error": "Model not found in config"}
            return
        meta = models[model_name]
        out_path = BASE_DIR / meta["model_path"]
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Update status
        with download_lock:
            download_status[model_name] = {"status": "downloading", "progress": 0, "size": expected_size}

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        headers["User-Agent"] = "Mozilla/5.0"

        # Stream download
        with requests.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if expected_size:
                            progress = int((downloaded / expected_size) * 100)
                            with download_lock:
                                download_status[model_name]["progress"] = progress
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
            download_status[model_name] = {"status": "error", "error": str(e)}


def start_download(model_name: str, url: str, expected_size: int):
    """Start download in a background thread."""
    env = load_env()
    token = env.get("TOKEN")
    thread = threading.Thread(target=_download_worker, args=(model_name, url, expected_size, token), daemon=True)
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
        models = load_models()
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
        models = load_models()
        if name not in models:
            return False
        meta = models[name]
        if delete_files:
            model_path = BASE_DIR / meta["model_path"]
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
        return True
    except Exception as e:
        log.exception("Failed to delete model %s", name)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Status and control endpoints
# ──────────────────────────────────────────────────────────────────────────────
def _status_for_model(model: str, meta: dict[str, Any]) -> dict[str, Any]:
    port = int(meta["port"])
    pid = find_pid_by_port(port)
    model_path = BASE_DIR / meta["model_path"]
    exists = model_path.is_file()
    active = check_port_active(port)
    if pid:
        status = "running"
    elif active:
        status = "running (unknown pid)"
    else:
        status = "stopped"
    # File size if it exists
    size_gb: float | None = None
    if exists:
        try:
            size_bytes = model_path.stat().st_size
            size_gb = round(size_bytes / (1024 ** 3), 2)
        except OSError:
            pass
    # Download status
    dl_status = None
    with download_lock:
        dl_status = download_status.get(model)
    return {
        "model": model,
        "port": port,
        "status": status,
        "pid": pid,
        "file_exists": exists,
        "file_size_gb": size_gb,
        "model_path": meta["model_path"],
        "download_status": dl_status,
        **meta,
    }


@app.route("/")
def index():
    models = load_models()
    statuses = [_status_for_model(name, meta) for name, meta in models.items()]
    html = render_template_string(TEMPLATE, statuses=statuses, host=DEFAULTS["host"], flask_port=FLASK_PORT)
    return html


@app.route("/api/status")
def api_status():
    models = load_models()
    return jsonify([_status_for_model(n, m) for n, m in models.items()])


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing 'model'"}), 400
    models = load_models()
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
    kwargs: dict[str, Any] = {}
    if WINDOWS:
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except Exception as exc:
        log_event("start", model, port, None, f"failed: {exc}")
        return jsonify({"error": str(exc)}), 500

    # Wait briefly to see if it crashes immediately
    time.sleep(2.5)
    if proc.poll() is None:
        log_event("start", model, port, proc.pid, "running")
        return jsonify({"status": "running", "pid": proc.pid, "port": port})
    else:
        log_event("start", model, port, proc.pid, "exited_early")
        return jsonify({"error": f"Server exited immediately with code {proc.poll()}", "status": "exited"}), 500


@app.route("/api/stop", methods=["POST"])
def api_stop():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing 'model'"}), 400
    models = load_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    port = int(models[model]["port"])
    pid = find_pid_by_port(port)
    if not pid:
        return jsonify({"status": "stopped"})

    success = _kill_tree(pid)
    if success:
        log_event("stop", model, port, pid, "stopped")
    else:
        log_event("stop", model, port, pid, "kill_failed")
    return jsonify({"status": "stopped", "pid": pid, "success": success})


@app.route("/api/download", methods=["POST"])
def api_download():
    """Start downloading a model.
    Expected JSON: { "model": "...", "url": "...", "expected_size": 123456789 }
    """
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    url = data.get("url", "").strip()
    try:
        expected_size = int(data.get("expected_size", 0))
    except ValueError:
        expected_size = 0
    if not model or not url:
        return jsonify({"error": "Missing model or url"}), 400
    # Basic validation: model should exist in models.json
    models = load_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    # Ensure target directory exists
    meta = models[model]
    target = BASE_DIR / meta["model_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    # If file already exists and looks complete, we could skip, but we'll just start download (will overwrite)
    start_download(model, url, expected_size)
    return jsonify({"status": "download started", "model": model})


@app.route("/api/download-status")
def api_download_status():
    with download_lock:
        return jsonify(download_status)


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
    Expected JSON: { "name": "...", "delete_files": false }
    """
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Missing model name"}), 400
    delete_files = bool(data.get("delete_files", False))
    success = delete_model_entry(name, delete_files=delete_files)
    if not success:
        return jsonify({"error": f"Model '{name}' not found"}), 404
    return jsonify({"status": "model deleted", "name": name, "delete_files": delete_files})


@app.route("/api/logs")
def api_logs():
     if not LOG_FILE.exists():
         return jsonify([])
     lines = LOG_FILE.read_text("utf-8", errors="replace").strip().splitlines()
     return jsonify(lines[-100:])

@app.route("/api/next-port")
def api_next_port():
    start = 11000
    port = start
    while check_port_active(port):
        port += 1
    return jsonify({"port": port})


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
<style>
  :root {
    --bg: #1a1a2e; --surface: #16213e; --surface2: #0f3460;
    --text: #e0e0e0; --text2: #a0a0b8; --green: #2ecc71;
    --red: #e74c3c; --yellow: #f1c40f; --blue: #3498db;
    --btn-run: #27ae60; --btn-stop: #e74c3c; --btn-download: #ff9800;
    --btn-add: #9b59b6; --btn-del: #e67e22; --btn-search: #3498db;
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
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: var(--text2); font-weight: 500;
       border-bottom: 1px solid var(--surface2); padding: .5rem .4rem; }
  td { padding: .55rem .4rem; border-bottom: 1px solid var(--surface2); vertical-align: middle; }
  tr.running { background: rgba(46,204,113,.06); }
  tr:hover { background: rgba(255,255,255,.03); }
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
     border-top: 4px solid var(--blue);
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
     th, td { font-size: .82rem; }
   }
</head>
<body>
<header>
  <h1>🐘 llama.cpp</h1>
  <span class="badge">{{ host }}:{{ flask_port }}</span>
</header>
<main>
  <p class="banner">
    Access from other devices on your LAN using <b>http://{{ host }}:{{ flask_port }}</b>
    ({{ host }} is bound to 0.0.0.0).
  </p>
  <div class="toolbar">
    <input type="search" id="search" placeholder="Filter models…">
    <button class="btn-goto" onclick="refresh()">↻ Refresh</button>
    <button class="btn-add" onclick="toggleAddForm()">➕ Add Model</button>
    <a class="btn-goto" href="/api/logs" target="_blank">📜 Logs</a>
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
   <table>
    <thead>
      <tr>
        <th>#</th><th>Status</th><th>Model</th><th>File</th><th>File size</th>
        <th>Port</th><th>Actions</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
  <div id="log"><b>Ready.</b></div>
</main>
<script>
const HOST = "{{ host }}";
const FLAVOR = "{{ flask_port }}";

function fmtGb(v) {
  if (v == null) return '<span class="chip warn">missing</span>';
  return v.toFixed(2) + ' GB';
}
function jsEscape(str) {
  return String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}
function portUrl(p) { return 'http://' + HOST + ':' + p; }

async function refresh() {
  const res = await fetch('/api/status');
  const rows = await res.json();
  const dlRes = await fetch('/api/download-status');
  const dlMap = {};
  const dlJson = await dlRes.json();
  for (const [k, v] of Object.entries(dlJson)) {
    dlMap[k] = v;
  }
  const q = document.getElementById('search').value.toLowerCase();
  const tb = document.getElementById('tbody');
  tb.innerHTML = rows
    .filter(r => r.model.toLowerCase().includes(q) || String(r.port).includes(q))
    .map((r, i) => {
      const running = r.status.startsWith('running');
      const statusChip = running
        ? '<span class="chip ok">● running</span>'
        : r.file_exists
          ? '<span class="chip ok">● stopped</span>'
          : '<span class="chip bad">✕ missing</span>';
      const dl = dlMap[r.model] || {};
      let dlHtml = '';
      if (dl.status === 'downloading') {
        dlHtml = `<div class="actions"><button class="download" disabled>⬇ ${dl.progress}%</button></div>`;
      } else if (dl.status === 'complete') {
        dlHtml = `<div class="actions"><button class="download" disabled>✓ Done</button></div>`;
      } else if (dl.status === 'error') {
        dlHtml = `<div class="actions"><button class="download" disabled style="background:var(--red)">✗ Error</button></div>`;
       } else if (!r.file_exists) {
         dlHtml = `<div class="actions"><button class="download" onclick="downloadModel('${jsEscape(r.model)}')">⬇ Download</button></div>`;
      } else {
        dlHtml = ''; // file exists, no download needed
      }
      return `<tr class="${running ? 'running' : ''}">
        <td>${i + 1}</td>
        <td>${statusChip}<br><small style="color:var(--text2)">${r.status}${r.pid ? ' · pid ' + r.pid : ''}</small></td>
        <td><b>${r.model}</b></td>
        <td title="${r.model_path}"><code>${r.model_path.split('/').pop()}</code></td>
 <td>${fmtGb(r.file_size_gb)}</td>
 <td>${r.file_exists ? `<a class="actions btn-goto" style="padding:.3rem .7rem;border-radius:4px;font-size:.8rem;color:#fff;background:var(--blue);text-decoration:none" href="${portUrl(r.port)}" target="_blank">:${r.port}</a>` : `<span class="chip bad">-</span>`}</td>
 <td><div class="actions">
           <button class="run"  onclick="act('${jsEscape(r.model)}','start')"  ${running ? 'disabled' : ''}>▶ Start</button>
           <button class="stop" onclick="act('${jsEscape(r.model)}','stop')"   ${!running ? 'disabled' : ''}>■ Stop</button>
         </div></td>
         <td><div class="actions">
           <button class="add" onclick="promptAddModel('${jsEscape(r.model)}')">➕ Add Variant</button>
           <button class="del" onclick="promptDeleteModel('${jsEscape(r.model)}')">🗑 Delete</button>
         </div></td>
      </tr>`;
    }).join('');
}

async function act(model, verb) {
  const btn = event.target;
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = verb === 'start' ? 'Starting…' : 'Stopping…';
  try {
    const res = await fetch(`/api/${verb}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ model }),
    });
    const data = await res.json();
    if (data.error) { log('<b>' + prev + ' error:</b> ' + data.error, 'red'); }
    else { log(`<b>${prev}:</b> ${model} → ${data.status}${data.pid ? ' (pid ' + data.pid + ')' : ''}`, 'green'); }
  } catch (e) {
    log('<b>Error:</b> ' + e, 'red');
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = prev; refresh(); }, 800);
  }
}

async function downloadModel(model) {
  const btn = event.target;
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = 'Downloading…';
  try {
    // We need to know the URL and expected size; for simplicity we'll fetch from a predefined map.
    // In a real app, you might have a catalog endpoint or store this in models.json.
    // Here we use a hardcoded map for demonstration.
    const modelInfo = {
      "Phi-4": {url: "https://huggingface.co/bartowski/phi-4-GGUF/resolve/main/phi-4-Q4_K_M.gguf", size: 8000000000},
      "DeepSeek-Coder-33b": {url: "https://huggingface.co/bartowski/DeepSeek-Coder-33B-GGUF/resolve/main/deepseek-coder-33b-instruct.Q4_K_M.gguf", size: 18000000000},
      "DeepSeek-Coder-7b": {url: "https://huggingface.co/bartowski/DeepSeek-Coder-7B-GGUF/resolve/main/deepseek-coder-7b-instruct-v1.5.Q4_K_M.gguf", size: 4000000000},
      "DeepSeek-R1-Distill-Llama-70b": {url: "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Llama-70B-GGUF/resolve/main/DeepSeek-R1-Distill-Llama-70B-Q4_K_M.gguf", size: 40000000000},
      "DeepSeek-R1-Distill-Qwen-32b": {url: "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf", size: 20000000000},
      "DeepSeek-R1-Distill-Qwen-14b": {url: "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf", size: 9000000000},
      "Qwen3-14b": {url: "https://huggingface.co/lmstudio-community/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q4_K_M.gguf", size: 8500000000}
    }[model];
    if (!modelInfo) { throw new Error('No download info for model'); }
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        model: model,
        url: modelInfo.url,
        expected_size: modelInfo.size
      })
    });
    const data = await res.json();
    if (data.error) { log('<b>' + prev + ' error:</b> ' + data.error, 'red'); }
    else { log(`<b>${prev}:</b> download started`, 'green'); }
  } catch (e) {
    log('<b>Error:</b> ' + e, 'red');
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = prev; refresh(); }, 1000);
  }
}

function toggleAddForm() {
  const form = document.getElementById('add-model-form');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
  if (form.style.display === 'block') {
    document.getElementById('modelName').focus();
  }
}

async function promptAddModel(existingModel) {
  // For simplicity, just open the form - user can search or enter manually
  toggleAddForm();
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

document.getElementById('search').addEventListener('input', refresh);
refresh();
setInterval(refresh, 5000);
// Also periodically update download status
setInterval(async () => {
  try { await refresh(); } catch (_) {}
}, 3000);
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
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Flask listen port (default 5000)")
    args = parser.parse_args()
    FLASK_PORT = args.port
    log.info("Starting web UI on http://%s:%d from %s", args.host, args.port, BASE_DIR)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
