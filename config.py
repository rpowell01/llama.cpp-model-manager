#!/usr/bin/env python3
"""
Configuration and platform detection for llama.cpp web frontend.

This module centralizes all constants, paths, and platform-specific logic.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
MODELS_JSON = BASE_DIR / "models.json"
LOG_FILE = BASE_DIR / "running_models.log"
ENV_FILE = BASE_DIR / ".env"

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE, override=False)
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────────────────────
# Default server parameters
DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 11000,
    "cache_size": 32768,
    "threads": 16,
    "threads_batch": 22,
    "timeout": 3600,
    "mlock": False,
    "flash_attn": "off",
    "no_mmap": True,
    "parallel": 2,
    "ubatch_size": 256,
}

# Flask server port (set by main.py at runtime)
FLASK_PORT: int = 5000

# ──────────────────────────────────────────────────────────────────────────────
# Platform detection
# ──────────────────────────────────────────────────────────────────────────────
WINDOWS = platform.system() == "Windows"

# Check for WSL (Windows Subsystem for Linux)
WSL = False
if not WINDOWS:
    try:
        proc_version = Path("/proc/version")
        if proc_version.exists() and "microsoft" in proc_version.read_text().lower():
            WSL = True
    except Exception:
        pass

# ──────────────────────────────────────────────────────────────────────────────
# llama-server binary location
# ──────────────────────────────────────────────────────────────────────────────
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


# ──────────────────────────────────────────────────────────────────────────────
# Utility functions
# ──────────────────────────────────────────────────────────────────────────────
def convert_path_for_binary(path: Path) -> str:
    r"""Convert path for the binary being used.

    On Linux with Windows binary (WSL), convert /mnt/c/... to C:/...
    Windows APIs accept forward slashes, so we use C:/... format.

    Args:
        path: The Path object to convert

    Returns:
        String representation of the path, converted if necessary
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


# ──────────────────────────────────────────────────────────────────────────────
# Remote configuration support
#
# REMOTE_MODE: When enabled (REMOTE_MODE=true), the application connects to an
# external llama.cpp server via REST API instead of managing local processes.
# All model operations are delegated to the remote server.
# ──────────────────────────────────────────────────────────────────────────────

class Config:
    """Configuration for remote vs local mode."""

    def __init__(self) -> None:
        self._remote_mode: bool = False
        self._remote_url: str = ""
        self._load()

    def _load(self) -> None:
        """Load configuration from environment variables."""
        # Read REMOTE_MODE: default to False if unset
        remote_mode_str = os.getenv("REMOTE_MODE", "").strip().lower()
        if remote_mode_str in ("true", "1", "yes", "on"):
            self._remote_mode = True
        else:
            self._remote_mode = False

        # Read LLAMA_CPP_SERVER_URL value (may be None if not set)
        url_env = os.getenv("LLAMA_CPP_SERVER_URL")

        if self._remote_mode:
            # In remote mode, URL must be explicitly set and non-empty
            if not url_env or not url_env.strip():
                raise ValueError(
                    "REMOTE_MODE is enabled but LLAMA_CPP_SERVER_URL is not set or empty. "
                    "Please set LLAMA_CPP_SERVER_URL to the remote server address."
                )
            self._remote_url = url_env.strip()
        else:
            # In local mode, use provided URL or default to localhost:8080
            self._remote_url = url_env.strip() if url_env and url_env.strip() else "http://localhost:8080"

    @property
    def is_remote_mode(self) -> bool:
        """Return True if remote mode is enabled."""
        return self._remote_mode

    @property
    def remote_url(self) -> str:
        """Return the remote server URL."""
        return self._remote_url
