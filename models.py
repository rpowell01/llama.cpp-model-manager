#!/usr/bin/env python3
"""
Model management: caching, filesystem scanning, port assignment, and persistence.

This module maintains the in-memory model registry with thread-safe access.

REMOTE_MODE: In remote mode, this module delegates to RemoteModelRegistry for
API-based model management. In local mode, it provides filesystem-based model
management with port assignment and cache persistence.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from config import BASE_DIR, DEFAULTS, MODELS_DIR, MODELS_JSON

# Thread-safe in-memory model cache
_models: dict[str, dict[str, Any]] = {}
_models_lock = threading.Lock()

log = logging.getLogger("llamaweb")


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────

class InvalidModeError(Exception):
    """Raised when attempting to use local-only functions in remote mode."""
    
    def __init__(self, operation: str):
        super().__init__(
            f"Cannot perform '{operation}' in remote mode. "
            "This operation requires local process management."
        )
        self.operation = operation


# ──────────────────────────────────────────────────────────────────────────────
# Model Data Class - imported from remote_client for consistency
# ──────────────────────────────────────────────────────────────────────────────

# Import Model from remote_client to avoid duplication
from remote_client import Model


# ──────────────────────────────────────────────────────────────────────────────
# Model Registry
# ──────────────────────────────────────────────────────────────────────────────

class ModelRegistry:
    """
    Unified model registry that works in both local and remote modes.
    
    In local mode, it provides filesystem-based model management.
    In remote mode, it delegates to RemoteModelRegistry for API-based management.
    """
    
    def __init__(self, config):
        """
        Initialize the ModelRegistry.
        
        Args:
            config: Config object with is_remote_mode property and remote_url property
        """
        self._config = config
        self._remote_client = None
        
        if self._config.is_remote_mode:
            from remote_client import RemoteModelRegistry
            self._remote_client = RemoteModelRegistry(self._config.remote_url)
    
    @property
    def is_remote_mode(self) -> bool:
        """Return True if running in remote mode."""
        return self._config.is_remote_mode
    
    def list_models(self) -> list[Model]:
        """
        List all models.
        
        Returns:
            List of Model objects. In remote mode, returns models from the API.
            In local mode, returns models from the local cache.
        """
        if self._config.is_remote_mode:
            return self._remote_client.list_models()
        
        # Local mode: return from local cache
        with _models_lock:
            models = []
            for name, meta in _models.items():
                model = Model.from_dict({**meta, "name": name})
                models.append(model)
            return models
    
    def load_model(self, name: str) -> bool:
        """
        Load a model. In remote mode, delegates to the remote API.
        
        Args:
            name: Model name to load
            
        Returns:
            True if successful
            
        Raises:
            InvalidModeError: In local mode (use processes module)
        """
        if self._config.is_remote_mode:
            return self._remote_client.load_model(name)
        raise InvalidModeError("load_model via registry - use processes module for local mode")
    
    def unload_model(self, name: str) -> bool:
        """
        Unload a model. In remote mode, delegates to the remote API.
        
        Args:
            name: Model name to unload
            
        Returns:
            True if successful
            
        Raises:
            InvalidModeError: In local mode (use processes module)
        """
        if self._config.is_remote_mode:
            return self._remote_client.unload_model(name)
        raise InvalidModeError("unload_model via registry - use processes module for local mode")
    
    def get_model(self, name: str) -> Optional[Model]:
        """
        Get a specific model by name.
        
        Args:
            name: Model name to retrieve
            
        Returns:
            Model object if found, None otherwise
        """
        if self._config.is_remote_mode:
            remote_model = self._remote_client.get_model(name)
            return remote_model
        
        # Local mode
        with _models_lock:
            if name in _models:
                return Model.from_dict({**_models[name], "name": name})
            return None
    
    def close(self) -> None:
        """Close the registry and release resources."""
        if self._remote_client:
            self._remote_client.close()


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
    except Exception:
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
    except Exception:
        log.exception("Failed to delete model %s", name)
        return False


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
                file_size_gb = round(file_size / (1024**3), 2)

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
                    "file_exists": True,
                }
                break
            except OSError:
                continue

    return models


def _get_seen_ports() -> set[int]:
    """Get ports already in use by external processes (llama-server or other).
    
    Uses ss/lsof for efficient port scanning instead of iterating through
    all possible ports with individual process lookups.
    """
    import shutil
    
    seen = set()
    
    # Use ss to get all listening ports at once (much faster than iterating)
    try:
        ss_path = shutil.which("ss")
        if ss_path:
            result = subprocess.run(
                [ss_path, "-tlnp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                # Parse lines like: LISTEN 0 511 127.0.0.1:11001 0.0.0.0:*
                parts = line.split()
                if len(parts) >= 4 and parts[0] == "LISTEN":
                    # Extract port from local address (format: IP:PORT)
                    local_addr = parts[3]
                    if ":" in local_addr:
                        port_str = local_addr.rsplit(":", 1)[1]
                        if port_str.isdigit():
                            seen.add(int(port_str))
            return seen
    except Exception:
        pass
    
    # Fallback: check for llama-server processes and skip port lookup
    # (port assignment will handle conflicts if they occur)
    return seen


def assign_ports(models: dict[str, dict[str, Any]]) -> None:
    """Assign sequential ports to models starting from 11000, ensuring uniqueness."""
    sorted_names = sorted(models.keys())
    used_ports = set()
    seen_ports = _get_seen_ports()  # Ports in use by external processes
    # Assign unique ports only if needed
    for i, model_name in enumerate(sorted_names):
        port = models[model_name].get("port", 0)
        # Reassign if: port is invalid (<=0), or is a duplicate within batch,
        # or is already in use by an external process
        if port <= 0 or port in used_ports or port in seen_ports:
            # Find next available port
            new_port = 11000 + i * 10
            while new_port in used_ports or new_port in seen_ports:
                new_port += 10
            models[model_name]["port"] = new_port
            used_ports.add(new_port)
        else:
            used_ports.add(port)


def save_models(models: dict[str, dict[str, Any]]) -> None:
    """Save models to disk and update in-memory cache."""
    with open(MODELS_JSON, "w", encoding="utf-8") as fh:
        json.dump(models, fh, indent=2)
    # Also update the in-memory cache
    with _models_lock:
        global _models
        _models = models.copy()
