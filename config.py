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


def _convert_windows_path_to_wsl(path: Path) -> Path:
    """
    Convert a Windows absolute path to a WSL mount path.
    
    Args:
        path: Windows Path (e.g., C:\\Users\\...)
        
    Returns:
        WSL Path (e.g., /mnt/c/Users/...) if conversion applies, otherwise original path
    """
    if not WSL:
        return path
    
    path_str = str(path)
    # Check if it's a Windows absolute path (e.g., C:\..., D:/...)
    # Windows paths can use backslash or forward slash
    if len(path_str) >= 3 and path_str[1] == ':' and path_str[2] in ('\\', '/'):
        drive = path_str[0].lower()
        # Convert to WSL mount path: /mnt/c/...
        # path_str[3:] gives us everything after C: (including the separator)
        # Normalize backslashes to forward slashes
        rest = path_str[3:].replace('\\', '/')
        # Ensure leading slash for the rest of the path
        if rest and not rest.startswith('/'):
            rest = '/' + rest
        wsl_path = f"/mnt/{drive}{rest}"
        return Path(wsl_path)
    
    return path


def _is_windows_absolute_path(path_str: str) -> bool:
    """Check if a string is a Windows absolute path (e.g., C:\\..., D:/...)."""
    if len(path_str) >= 3 and path_str[1] == ':' and path_str[2] in ('\\', '/'):
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE, override=False)
except ImportError:
    pass

# MODEL_DIR can be overridden via .env to point to an external models directory
MODEL_DIR_ENV = os.getenv("MODEL_DIR")
if MODEL_DIR_ENV:
    model_dir_path = Path(MODEL_DIR_ENV)
    # Convert Windows paths to WSL paths when running in WSL
    model_dir_path = _convert_windows_path_to_wsl(model_dir_path)
    MODELS_DIR = model_dir_path
else:
    MODELS_DIR = BASE_DIR / "models"

MODELS_JSON = BASE_DIR / "models.json"
LOG_FILE = BASE_DIR / "running_models.log"

# ──────────────────────────────────────────────────────────────────────────────
# Default server parameters
# CPU-optimized defaults for llama.cpp server

def _get_cpu_count() -> int:
    """Get logical CPU count for thread defaults."""
    try:
        return os.cpu_count() or 16
    except Exception:
        return 16

DEFAULTS: dict[str, Any] = {
    "host": "0.0.0.0",
    "port": 11000,
    "threads": _get_cpu_count(),  # Use all logical CPUs
    "threads_batch": _get_cpu_count(),  # Use all logical CPUs for batch
    "timeout": 3600,
    "mlock": True,  # Prevent swapping - critical for CPU performance
    "flash_attn": "auto",  # Disable flash attention on CPU
    "no_mmap": False,  # Disable mmap on CPU for better performance
    "parallel": "auto",  # Single parallel sequence for CPU
    "ubatch_size": 1024,
    # Agent/Tool parameters
    "jinja": True,  # Enable Jinja tool parsing for agent loops (Claude Code/OpenAI tool schemas)
    "reasoning_preserve": True,  # Preserve reasoning/thinking inner monologue across tool steps
    # CPU-only optimizations
    "gpu_layers": 99,  # 0 = CPU-only (no GPU offload)
    "cache_type_k": "q4_0",  # KV cache type: fp16, q4_0, q8_0 (q4_0 saves ~3.5x memory)
    "cache_type_v": "q4_0",  # KV cache type for V
    "numa": True,  # Enable NUMA awareness
    "numa_value": "distribute",  # NUMA mode: distribute, isolate, numactl
    "cpu_affinity": "auto",  # CPU core pinning: "auto", "none", or comma-separated core IDs
    "cpu_strict": False,  # Strict CPU mode (disable GPU ops)
    "batch_size": 4096,  # Batch size for prompt processing
    "ctx_size": 128000,  # Context window size
    "rope_freq_base": 10000.0,  # RoPE frequency base
    "rope_freq_scale": 1.0,  # RoPE frequency scaling
    # YaRN RoPE scaling
    "rope_scaling_type": "none",  # RoPE scaling method: none, linear, yarn
    "rope_scale": 1.0,  # RoPE context scaling factor (expands context by N)
    "yarn_orig_ctx": 0,  # YaRN: original context size of model (0 = auto from model)
    "yarn_ext_factor": -1.0,  # YaRN: extrapolation mix factor (-1 = default)
    "yarn_attn_factor": 1.0,  # YaRN: attention magnitude scaling factor
    "yarn_beta_slow": 1.0,  # YaRN: high correction dim (alpha)
    "yarn_beta_fast": 32.0,  # YaRN: low correction dim (beta)
    # Speculative decoding parameters
    "spec_type": "none",
    "spec_draft_model": "",
    "spec_draft_hf_repo": "",
    "spec_draft_n_max": 8,
    "spec_draft_n_min": 3,
    "spec_draft_p_split": 0.10,
    "spec_draft_p_min": 0.00,
    "spec_draft_ngl": "auto",
    "spec_draft_temp": 0.0,
    "spec_draft_top_p": 1.0,
    "spec_draft_min_p": 0.0,
    "spec_draft_typical": 1.0,
    "spec_ngram_size_n": 12,
    "spec_ngram_size_m": 48,
    "spec_ngram_min_hits": 1,
    # Ngram-mod specific parameters
    "spec_ngram_mod_n_match": 24,
    "spec_ngram_mod_n_min": 48,
    "spec_ngram_mod_n_max": 64,
    # Draft model KV cache type
    "spec_draft_type_k": "fp16",
    "spec_draft_type_v": "fp16",
    # Intel/AMD GPU Options
    "sycl": False,
    "grp_attn": False,
    "grp_attn_n": 8,
    # Feature flags
    # Model warmup
    "warmup_enabled": False,
    "warmup_message": "// TODO: add two numbers def add(a, b):",
    "warmup_max_tokens": 10,
    # Slot KV Cache Persistence
    "slot_save_path": "",
    "cache_reuse": 4096,
    "swa_full": False,
}

# Flask server port (set by main.py at runtime)
FLASK_PORT: int = 5000

# ──────────────────────────────────────────────────────────────────────────────
# llama-server binary location
# ──────────────────────────────────────────────────────────────────────────────
LLAMA_SERVER = None

# Check for LLAMA_SERVER_DIR environment variable first (highest priority)
llama_server_dir_env = os.getenv("LLAMA_SERVER_DIR")
if llama_server_dir_env:
    llama_server_dir = Path(llama_server_dir_env)
    # Convert Windows paths to WSL paths when running in WSL
    llama_server_dir = _convert_windows_path_to_wsl(llama_server_dir)
    if WINDOWS or WSL:
        candidate = llama_server_dir / "llama-server.exe"
        if candidate.exists():
            LLAMA_SERVER = candidate
    else:
        candidate = llama_server_dir / "llama-server"
        if candidate.exists():
            LLAMA_SERVER = candidate

# Fallback to searching in BASE_DIR
if LLAMA_SERVER is None:
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

def _convert_windows_path_to_wsl(path: Path) -> Path:
    """
    Convert a Windows absolute path to a WSL mount path.
    
    Args:
        path: Windows Path (e.g., C:\\Users\\...)
        
    Returns:
        WSL Path (e.g., /mnt/c/Users/...) if conversion applies, otherwise original path
    """
    if not WSL:
        return path
    
    path_str = str(path)
    # Check if it's a Windows absolute path (e.g., C:\..., D:/...)
    # Windows paths can use backslash or forward slash
    if len(path_str) >= 3 and path_str[1] == ':' and path_str[2] in ('\\', '/'):
        drive = path_str[0].lower()
        # Convert to WSL mount path: /mnt/c/...
        # path_str[3:] gives us everything after C: (including the separator)
        # Normalize backslashes to forward slashes
        rest = path_str[3:].replace('\\', '/')
        # Ensure leading slash for the rest of the path
        if rest and not rest.startswith('/'):
            rest = '/' + rest
        wsl_path = f"/mnt/{drive}{rest}"
        return Path(wsl_path)
    
    return path


def _is_windows_absolute_path(path_str: str) -> bool:
    """Check if a string is a Windows absolute path (e.g., C:\\..., D:/...)."""
    if len(path_str) >= 3 and path_str[1] == ':' and path_str[2] in ('\\', '/'):
        return True
    return False


def resolve_model_path(model_path_str: str) -> Path:
    """
    Resolve a model path string to an absolute Path.
    
    Handles three cases:
    1. Absolute path (Linux or Windows) - return as-is (with WSL conversion if needed)
    2. Relative path starting with "models/" - resolve relative to MODELS_DIR (respects MODEL_DIR env var)
    3. Other relative paths - resolve relative to BASE_DIR
    
    Args:
        model_path_str: Model path string from models.json
        
    Returns:
        Resolved absolute Path
    """
    if not model_path_str:
        return BASE_DIR
    
    # Check for Windows absolute path first (not detected by Path.is_absolute() on Linux)
    if _is_windows_absolute_path(model_path_str):
        model_path = Path(model_path_str)
        return _convert_windows_path_to_wsl(model_path)
    
    model_path = Path(model_path_str)
    if model_path.is_absolute():
        # Convert Windows paths to WSL paths when running in WSL
        return _convert_windows_path_to_wsl(model_path)
    
    # If path starts with "models/", resolve relative to MODELS_DIR
    # This respects the MODEL_DIR environment variable
    try:
        rel_to_models = model_path.relative_to("models")
        return MODELS_DIR / rel_to_models
    except ValueError:
        # Not under "models/", fall back to BASE_DIR
        return BASE_DIR / model_path


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
