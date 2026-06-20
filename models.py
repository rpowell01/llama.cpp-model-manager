#!/usr/bin/env python3
"""
Model management: caching, filesystem scanning, port assignment, and persistence.

This module maintains the in-memory model registry with thread-safe access.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from config import BASE_DIR, DEFAULTS, MODELS_DIR, MODELS_JSON

# Thread-safe in-memory model cache
_models: dict[str, dict[str, Any]] = {}
_models_lock = threading.Lock()

log = logging.getLogger("llamaweb")


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
    """Save models to disk and update in-memory cache."""
    with open(MODELS_JSON, "w", encoding="utf-8") as fh:
        json.dump(models, fh, indent=2)
    # Also update the in-memory cache
    with _models_lock:
        global _models
        _models = models.copy()
