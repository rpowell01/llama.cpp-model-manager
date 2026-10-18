"""Automatic metrics export for running llama.cpp models.

Exports metrics from running models to per-model CSV files in
``metrics_exports/``. Designed to run independently of the UI (no Flask
request/response dependency) and safe to call from background scheduler
threads.

The CSV format and derived metrics match ``api_export_metrics`` in
``routes/models.py`` so that manual and automatic exports produce identical
rows.
"""

from __future__ import annotations

import csv
import logging
import os
import threading

# fcntl is Unix-only; guard the import so the module still loads on Windows
# (the flock call sites degrade gracefully when fcntl is unavailable).
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows platform path
    fcntl = None
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from config import BASE_DIR
from models import get_models
from routes.models import check_port_active

log = logging.getLogger("llamaweb.exporter")

# Directory where per-model CSV files are written.
CSV_DIR = BASE_DIR / "metrics_exports"

# Serialises in-process CSV writes across scheduler/Flask threads.
_write_lock = threading.Lock()

# Column order matches the row dict built by api_export_metrics.
FIELDNAMES: list[str] = [
    "timestamp",
    "model",
    "port",
    # Prompt metrics
    "prompt_seconds_total",
    "prompt_tokens_total",
    "prompt_eval_ms_per_token",
    "prompt_eval_rate_tokens_per_sec",
    # Generation metrics
    "gen_seconds_total",
    "gen_tokens_total",
    "gen_eval_ms_per_token",
    "gen_rate_tokens_per_sec",
    # Speculative decoding
    "spec_draft_tokens_total",
    "spec_accepted_tokens_total",
    "spec_drafts_total",
    "spec_acceptance_rate_pct",
    "spec_avg_draft_len",
    "spec_est_speedup",
    # Configuration
    "threads",
    "threads_batch",
    "gpu_layers",
    "ctx_size",
    "batch_size",
    "ubatch_size",
    "flash_attn",
    "numa",
    "numa_value",
    "parallel",
    "rope_scaling_type",
    "cache_type_k",
    "cache_type_v",
    "spec_type",
    "spec_draft_n_max",
    "spec_draft_n_min",
]


def _fetch_metrics_text(port: int) -> str:
    """Fetch the Prometheus-text metrics payload from a running model.

    Args:
        port: The port the llama-server is listening on.

    Returns:
        The raw metrics text.

    Raises:
        RuntimeError: If the metrics endpoint cannot be reached or returns
            a non-OK status.
    """
    url = f"http://127.0.0.1:{port}/metrics"
    try:
        resp = requests.get(url, timeout=5)
    except requests.RequestException as exc:
        raise RuntimeError(f"Error fetching metrics from port {port}: {exc}") from exc
    if not resp.ok:
        raise RuntimeError(
            f"Failed to fetch metrics from port {port} (HTTP {resp.status_code})"
        )
    return resp.text


def _parse_metrics(metrics_text: str) -> dict[str, float]:
    """Parse Prometheus-text metrics into a ``{name: value}`` dict."""
    metrics: dict[str, float] = {}
    for line in metrics_text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            metrics[name] = float(parts[-1])
        except ValueError:
            continue
    return metrics


def build_metrics_row(model: str, meta: dict) -> dict:
    """Build the CSV row dict for a running model.

    Mirrors the metrics fetching and derived-metric calculation in
    ``api_export_metrics`` (routes/models.py).

    Args:
        model: Canonical model name.
        meta: Model configuration dictionary (from ``get_models()``).

    Returns:
        A dict keyed by :data:`FIELDNAMES`.

    Raises:
        ValueError: If the model has no port assigned.
        RuntimeError: If the model is not running or metrics cannot be read.
    """
    port = int(meta.get("port", 0))
    if not port:
        raise ValueError(f"Model '{model}' has no port assigned")

    if not check_port_active(port):
        raise RuntimeError(f"Model '{model}' is not running on port {port}")

    metrics_text = _fetch_metrics_text(port)
    metrics = _parse_metrics(metrics_text)

    # Prompt metrics
    prompt_sec = metrics.get("llamacpp:prompt_seconds_total", 0)
    prompt_tokens = metrics.get("llamacpp:prompt_tokens_total", 0)
    prompt_rate = (prompt_tokens / prompt_sec) if prompt_sec > 0 else 0
    prompt_eval_ms = (prompt_sec / prompt_tokens * 1000) if prompt_tokens > 0 else 0

    # Generation metrics
    gen_sec = metrics.get("llamacpp:tokens_predicted_seconds_total", 0)
    gen_tokens = metrics.get("llamacpp:tokens_predicted_total", 0)
    gen_rate = (gen_tokens / gen_sec) if gen_sec > 0 else 0
    eval_ms = (gen_sec / gen_tokens * 1000) if gen_tokens > 0 else 0

    # Speculative decoding
    draft_tokens = metrics.get("llamacpp:spec_decode_num_draft_tokens_total", 0)
    accepted_tokens = metrics.get("llamacpp:spec_decode_num_accepted_tokens_total", 0)
    drafts = metrics.get("llamacpp:spec_decode_num_drafts_total", 0)
    acceptance_rate = (accepted_tokens / draft_tokens * 100) if draft_tokens > 0 else 0
    avg_draft_len = (draft_tokens / drafts) if drafts > 0 else 0
    accepted_per_step = (accepted_tokens / drafts) if drafts > 0 else 0
    speedup = (1 + accepted_per_step) if drafts > 0 else 1

    now = datetime.now()
    return {
        "timestamp": now.isoformat(),
        "model": model,
        "port": port,
        "prompt_seconds_total": prompt_sec,
        "prompt_tokens_total": prompt_tokens,
        "prompt_eval_ms_per_token": round(prompt_eval_ms, 1),
        "prompt_eval_rate_tokens_per_sec": round(prompt_rate, 1),
        "gen_seconds_total": gen_sec,
        "gen_tokens_total": gen_tokens,
        "gen_eval_ms_per_token": round(eval_ms, 1),
        "gen_rate_tokens_per_sec": round(gen_rate, 1),
        "spec_draft_tokens_total": draft_tokens,
        "spec_accepted_tokens_total": accepted_tokens,
        "spec_drafts_total": drafts,
        "spec_acceptance_rate_pct": round(acceptance_rate, 1),
        "spec_avg_draft_len": round(avg_draft_len, 1),
        "spec_est_speedup": round(speedup, 2),
        "threads": meta.get("threads"),
        "threads_batch": meta.get("threads_batch"),
        "gpu_layers": meta.get("gpu_layers"),
        "ctx_size": meta.get("ctx_size"),
        "batch_size": meta.get("batch_size"),
        "ubatch_size": meta.get("ubatch_size"),
        "flash_attn": meta.get("flash_attn"),
        "numa": meta.get("numa"),
        "numa_value": meta.get("numa_value"),
        "parallel": meta.get("parallel"),
        "rope_scaling_type": meta.get("rope_scaling_type"),
        "cache_type_k": meta.get("cache_type_k"),
        "cache_type_v": meta.get("cache_type_v"),
        "spec_type": meta.get("spec_type"),
        "spec_draft_n_max": meta.get("spec_draft_n_max"),
        "spec_draft_n_min": meta.get("spec_draft_n_min"),
    }


def _append_row_to_csv(model: str, row: dict) -> Path:
    """Append a row to the model's CSV file (header only on first write).

    Thread-safe within a process via :data:`_write_lock` and safe across
    processes via ``fcntl`` file locking. The header is written only when
    the file is empty, which is checked while holding the file lock so the
    decision is consistent with the write.

    Args:
        model: Canonical model name (used for the file name).
        row: The row dict to append.

    Returns:
        The path to the CSV file.
    """
    CSV_DIR.mkdir(exist_ok=True)
    safe_name = model.replace("/", "_").replace(" ", "_")
    csv_path = CSV_DIR / f"{safe_name}_metrics.csv"

    fieldnames = [name for name in FIELDNAMES if name in row] or list(row.keys())

    with _write_lock:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            if fcntl is not None:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                except OSError:
                    # fcntl unavailable on this platform; threading lock still
                    # provides in-process safety.
                    pass
            try:
                # File size is checked while holding the lock so the
                # header decision races neither with other threads nor
                # other processes.
                is_empty = os.path.getsize(csv_path) == 0
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if is_empty:
                    writer.writeheader()
                writer.writerow(row)
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass

    return csv_path


def export_metrics_for_model(model_name: str) -> dict:
    """Export metrics for a single running model to its CSV file.

    Looks up the model configuration, fetches metrics from the running
    model, and appends a row to the per-model CSV file (creating it with a
    header on first write).

    Args:
        model_name: Name of the model to export metrics for (matched
            case-insensitively).

    Returns:
        A dict with ``status`` (``"exported"``), ``csv_path``, and ``row``.

    Raises:
        ValueError: If the model is unknown or has no port assigned.
        RuntimeError: If the model is not running or metrics cannot be read.
    """
    models = get_models()

    # Case-insensitive lookup for the canonical model key.
    model_key: Optional[str] = None
    for key in models:
        if key.lower() == model_name.lower():
            model_key = key
            break
    if model_key is None:
        raise ValueError(f"Unknown model '{model_name}'")

    meta = models[model_key]
    row = build_metrics_row(model_key, meta)
    csv_path = _append_row_to_csv(model_key, row)

    log.info(
        "Exported metrics for model '%s' to %s", model_key, csv_path
    )
    return {"status": "exported", "csv_path": str(csv_path), "row": row}
