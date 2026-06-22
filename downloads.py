#!/usr/bin/env python3
"""
Download management: background downloads with resumable functionality, progress tracking.

This module handles downloading GGUF model files from Hugging Face or other URLs.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict

import requests

from config import BASE_DIR
from models import get_models
from state import log_event

# Global download status tracking
download_status: Dict[str, Dict[str, Any]] = {}
download_lock = threading.Lock()

log = logging.getLogger("llamaweb")


def load_env() -> dict[str, str]:
    """Load environment variables from .env file."""
    env = {}
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env





def _download_worker(
    model_name: str,
    url: str,
    expected_size: int,
    token: str | None = None,
    force: bool = False,
) -> None:
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
                    download_status[model_name] = {
                        "status": "complete",
                        "progress": 100,
                        "size": existing_size,
                    }
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
                "downloaded": resume_pos,
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
        except requests.exceptions.RequestException as e:
            log.error(f"Failed to connect to download URL {url}: {e}")
            raise

        with response as r:
            if r.status_code == 416:  # Range not satisfiable - server doesn't support resume
                log.info(f"Server doesn't support range requests for {model_name}, starting fresh download")
                # Delete partial file and restart download without Range header
                out_path.unlink()
                resume_pos = 0
                headers.pop("Range", None)
                # Update status to show fresh start
                with download_lock:
                    download_status[model_name] = {
                        "status": "downloading",
                        "progress": 0,
                        "size": expected_size if expected_size > 0 else 0,
                        "downloaded": 0,
                    }
                # Restart the request without Range header
                r.close()
                response = requests.get(url, headers=headers, stream=True, timeout=30)
                response.raise_for_status()
                r = response

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
                                download_status[model_name].update(
                                    {
                                        "progress": progress,
                                        "size": expected_size if expected_size > 0 else downloaded,
                                        "downloaded": downloaded,
                                        "speed_mbps": round(speed_mbps, 2),
                                    }
                                )
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


def start_download(
    model_name: str,
    url: str,
    expected_size: int,
    force: bool = False,
) -> None:
    """Start download in a background thread."""
    env = load_env()
    token = env.get("TOKEN")
    thread = threading.Thread(
        target=_download_worker,
        args=(model_name, url, expected_size, token, force),
        daemon=True,
    )
    thread.start()
