"""Model CRUD operations."""
import requests
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, current_app
from logging import getLogger

from config import DEFAULTS, normalize_path_for_binary
from metrics import fetch_metrics_snapshot
from models import get_models, save_models, add_model_entry, delete_model_entry, refresh_models_cache

log = getLogger("llamaweb")

models_bp = Blueprint("models", __name__)

# Time budget for scraping a model's /metrics endpoint for the performance
# graph. A busy llama-server (long prompt processing) can take a while to
# answer, so the budget is generous; a failed scrape falls back to the last
# successful snapshot instead of failing the request.
GET_METRICS_TIMEOUT_SECONDS = 10.0

@models_bp.route("/api/add-model", methods=["POST"])
def api_add_model():
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
    # Normalize name: lowercase and replace spaces with hyphens for consistency with filesystem
    normalized_name = data["name"].strip().lower().replace(" ", "-")
    success = add_model_entry(
        name=normalized_name,
        repo=data["repo"].strip(),
        filename=data["filename"].strip(),
        expected_size=expected_size,
        port=port,
        threads=int(data.get("threads", DEFAULTS["threads"])),
        threads_batch=int(data.get("threads_batch", DEFAULTS["threads_batch"])),
        timeout=int(data.get("timeout", DEFAULTS["timeout"])),
        mlock=bool(data.get("mlock", DEFAULTS["mlock"])),
        flash_attn=str(data.get("flash_attn", DEFAULTS["flash_attn"])),
        no_mmap=bool(data.get("no_mmap", DEFAULTS["no_mmap"])),
        parallel=str(data.get("parallel", DEFAULTS["parallel"])),
        ubatch_size=int(data.get("ubatch_size", DEFAULTS["ubatch_size"])),
        # Agent/Tool parameters
        jinja=bool(data.get("jinja", DEFAULTS["jinja"])),
        reasoning_preserve=bool(data.get("reasoning_preserve", DEFAULTS["reasoning_preserve"])),
        # CPU-only optimizations
        gpu_layers=int(data.get("gpu_layers", DEFAULTS["gpu_layers"])),
        cache_type_k=str(data.get("cache_type_k", DEFAULTS["cache_type_k"])),
        cache_type_v=str(data.get("cache_type_v", DEFAULTS["cache_type_v"])),
        numa=bool(data.get("numa", DEFAULTS["numa"])),
        numa_value=str(data.get("numa_value", DEFAULTS["numa_value"])),
        cpu_affinity=str(data.get("cpu_affinity", DEFAULTS["cpu_affinity"])),
        cpu_strict=bool(data.get("cpu_strict", DEFAULTS["cpu_strict"])),
        batch_size=int(data.get("batch_size", DEFAULTS["batch_size"])),
        ctx_size=int(data.get("ctx_size", DEFAULTS["ctx_size"])),
        rope_freq_base=float(data.get("rope_freq_base", DEFAULTS["rope_freq_base"])),
        rope_freq_scale=float(data.get("rope_freq_scale", DEFAULTS["rope_freq_scale"])),
        rope_scaling_type=str(data.get("rope_scaling_type", DEFAULTS["rope_scaling_type"])),
        rope_scale=float(data.get("rope_scale", DEFAULTS["rope_scale"])),
        yarn_orig_ctx=int(data.get("yarn_orig_ctx", DEFAULTS["yarn_orig_ctx"])),
        yarn_ext_factor=float(data.get("yarn_ext_factor", DEFAULTS["yarn_ext_factor"])),
        yarn_attn_factor=float(data.get("yarn_attn_factor", DEFAULTS["yarn_attn_factor"])),
        yarn_beta_slow=float(data.get("yarn_beta_slow", DEFAULTS["yarn_beta_slow"])),
        yarn_beta_fast=float(data.get("yarn_beta_fast", DEFAULTS["yarn_beta_fast"])),
        # Speculative decoding parameters
        spec_type=str(data.get("spec_type", DEFAULTS["spec_type"])),
        spec_draft_model=str(data.get("spec_draft_model", DEFAULTS["spec_draft_model"])),
        spec_draft_hf_repo=str(data.get("spec_draft_hf_repo", DEFAULTS["spec_draft_hf_repo"])),
        spec_draft_n_max=int(data.get("spec_draft_n_max", DEFAULTS["spec_draft_n_max"])),
        spec_draft_n_min=int(data.get("spec_draft_n_min", DEFAULTS["spec_draft_n_min"])),
        spec_draft_p_split=float(data.get("spec_draft_p_split", DEFAULTS["spec_draft_p_split"])),
        spec_draft_p_min=float(data.get("spec_draft_p_min", DEFAULTS["spec_draft_p_min"])),
        spec_draft_ngl=str(data.get("spec_draft_ngl", DEFAULTS["spec_draft_ngl"])),
        spec_ngram_size_n=int(data.get("spec_ngram_size_n", DEFAULTS["spec_ngram_size_n"])),
        spec_ngram_size_m=int(data.get("spec_ngram_size_m", DEFAULTS["spec_ngram_size_m"])),
        spec_ngram_min_hits=int(data.get("spec_ngram_min_hits", DEFAULTS["spec_ngram_min_hits"])),
        # Ngram-mod parameters
        spec_ngram_mod_n_match=int(data.get("spec_ngram_mod_n_match", DEFAULTS["spec_ngram_mod_n_match"])),
        spec_ngram_mod_n_min=int(data.get("spec_ngram_mod_n_min", DEFAULTS["spec_ngram_mod_n_min"])),
        spec_ngram_mod_n_max=int(data.get("spec_ngram_mod_n_max", DEFAULTS["spec_ngram_mod_n_max"])),
        # Draft model KV cache type
        spec_draft_type_k=str(data.get("spec_draft_type_k", DEFAULTS["spec_draft_type_k"])),
        spec_draft_type_v=str(data.get("spec_draft_type_v", DEFAULTS["spec_draft_type_v"])),
        # Slot KV Cache Persistence
        slot_save_path=str(data.get("slot_save_path", DEFAULTS["slot_save_path"])),
        cache_reuse=int(data.get("cache_reuse", DEFAULTS["cache_reuse"])),
        swa_full=bool(data.get("swa_full", DEFAULTS["swa_full"])),
        # Generation / Sampling parameters
        temperature=float(data.get("temperature", DEFAULTS["temperature"])),
        top_k=int(data.get("top_k", DEFAULTS["top_k"])),
        top_p=float(data.get("top_p", DEFAULTS["top_p"])),
        min_p=float(data.get("min_p", DEFAULTS["min_p"])),
        repeat_penalty=float(data.get("repeat_penalty", DEFAULTS["repeat_penalty"])),
        presence_penalty=float(data.get("presence_penalty", DEFAULTS["presence_penalty"])),
        reasoning=str(data.get("reasoning", DEFAULTS["reasoning"])),
        reasoning_effort=str(data.get("reasoning_effort", DEFAULTS["reasoning_effort"])),
        reasoning_budget=int(data.get("reasoning_budget", DEFAULTS["reasoning_budget"])),
        reasoning_budget_message=str(data.get("reasoning_budget_message", DEFAULTS["reasoning_budget_message"])),
    # Chat template
    chat_template_file=str(data.get("chat_template_file", DEFAULTS["chat_template_file"])),
    # Custom HTTP Header for Kilo Code Config
    custom_header_key=str(data.get("custom_header_key", DEFAULTS.get("custom_header_key", ""))),
    custom_header_value=str(data.get("custom_header_value", DEFAULTS.get("custom_header_value", ""))),
    )
    if not success:
        return jsonify({"error": f"Model '{normalized_name}' already exists or failed to add"}), 409
    return jsonify({"status": "model added", "name": normalized_name})


@models_bp.route("/api/delete-model", methods=["POST"])
def api_delete_model():
    data = request.get_json(force=True) or {}
    name = data.get("model", "").strip()
    if not name:
        return jsonify({"error": "Missing model name"}), 400
    delete_files = bool(data.get("delete_files", False))
    success = delete_model_entry(name, delete_files=delete_files)
    if not success:
        return jsonify({"error": f"Model '{name}' not found"}), 404
    return jsonify({"status": "model deleted", "name": name, "delete_files": delete_files})


@models_bp.route("/api/update-model-repo", methods=["POST"])
def api_update_model_repo():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    repo = data.get("repo", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    if not repo:
        return jsonify({"error": "Missing repo"}), 400

    models = get_models()
    # Case-insensitive lookup
    model_key = None
    for k in models:
        if k.lower() == model.lower():
            model_key = k
            break
    if model_key is None:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    models[model_key]["repo"] = repo
    save_models(models)

    return jsonify({"status": "repo updated", "model": model, "repo": repo})


@models_bp.route("/api/update-model-meta", methods=["POST"])
def api_update_model_meta():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400

    models = get_models()
    # Case-insensitive lookup
    model_key = None
    for k in models:
        if k.lower() == model.lower():
            model_key = k
            break
    if model_key is None:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    meta = models[model_key]
    allowed_fields = {
        "port": int,
        "threads": int,
        "threads_batch": int,
        "timeout": int,
        "mlock": bool,
        "flash_attn": str,
        "no_mmap": bool,
        "parallel": str,
        "ubatch_size": int,
        # Agent/Tool parameters
        "jinja": bool,
        "reasoning_preserve": bool,
        # CPU-only optimizations
        "gpu_layers": int,
        "cache_type_k": str,
        "cache_type_v": str,
        "numa": bool,
        "numa_value": str,
        "cpu_affinity": str,
        "cpu_strict": bool,
        "batch_size": int,
        "ctx_size": int,
        "rope_freq_base": float,
        "rope_freq_scale": float,
        "rope_scaling_type": str,
        "rope_scale": float,
        "yarn_orig_ctx": int,
        "yarn_ext_factor": float,
        "yarn_attn_factor": float,
        "yarn_beta_slow": float,
        "yarn_beta_fast": float,
        # Speculative decoding parameters
        "speculative_decoding_model": bool,
        "spec_type": str,
        "spec_draft_model": str,
        "spec_draft_hf_repo": str,
        "spec_draft_n_max": int,
        "spec_draft_n_min": int,
        "spec_draft_p_split": float,
        "spec_draft_p_min": float,
        "spec_draft_ngl": str,
        "spec_draft_temp": float,
        "spec_draft_top_p": float,
        "spec_draft_min_p": float,
        "spec_draft_typical": float,
        "spec_ngram_size_n": int,
        "spec_ngram_size_m": int,
        "spec_ngram_min_hits": int,
        # Ngram-mod parameters
        "spec_ngram_mod_n_match": int,
        "spec_ngram_mod_n_min": int,
        "spec_ngram_mod_n_max": int,
        # Draft model KV cache type
        "spec_draft_type_k": str,
        "spec_draft_type_v": str,
        # Warmup parameters
        "warmup_enabled": bool,
        "warmup_message": str,
        "warmup_max_tokens": int,
        # User rating fields
        "comment": str,
        "rating_speed": int,
        "rating_coding": int,
        # Slot KV Cache Persistence
        "slot_save_path": str,
        "cache_reuse": int,
        "swa_full": bool,
    # Chat template
    "chat_template_file": str,
    # Custom HTTP Header for Kilo Code Config
    "custom_header_key": str,
    "custom_header_value": str,
    # Generation / Sampling parameters
    "use_generation_params": bool,
    "temperature": float,
    "top_k": int,
    "top_p": float,
    "min_p": float,
    "repeat_penalty": float,
    "presence_penalty": float,
    "reasoning": str,
    "reasoning_effort": str,
    "reasoning_budget": int,
    "reasoning_budget_message": str,
    }
    # Validate rating fields before any mutation: an invalid rating must be
    # rejected without leaving a bad value behind in the cached model meta.
    for field in ("rating_speed", "rating_coding"):
        if field in data:
            val = data[field]
            try:
                val_int = int(val)
            except (TypeError, ValueError, OverflowError):
                return jsonify({"error": f"Invalid rating value for {field}: must be an integer 0-5"}), 400
            if val_int < 0 or val_int > 5:
                return jsonify({"error": f"Rating {field} must be between 0 and 5"}), 400

    # Validate comment length (max 500 chars) before mutation
    if "comment" in data:
        comment = data["comment"]
        if not isinstance(comment, str):
            try:
                comment = str(comment)
            except Exception:
                return jsonify({"error": "Comment must be a string"}), 400
        if len(comment) > 500:
            return jsonify({"error": "Comment too long (max 500 characters)"}), 400

    changes = {}
    for field, typ in allowed_fields.items():
        if field in data:
            value = data[field]
            # Handle null as "delete this field" - use server default
            if value is None:
                if field in meta:
                    del meta[field]
                    changes[field] = None
                continue
            try:
                if typ is int:
                    value = int(value)
                elif typ is bool:
                    # bool("false") is True, so normalise string input explicitly
                    # instead of relying on truthiness.
                    if isinstance(value, str):
                        value = value.strip().lower() in ("true", "1", "yes", "on")
                    else:
                        value = bool(value)
                elif typ is float:
                    value = float(value)
                else:
                    value = str(value)
                if field == "spec_draft_model" and value:
                    # llama-server.exe runs in a Windows context, so the draft
                    # model path is stored in Windows form (C:/...), not the
                    # WSL mount form (/mnt/c/...).
                    value = normalize_path_for_binary(value)
                meta[field] = value
                changes[field] = value
            except Exception as e:
                return jsonify({"error": f"Invalid value for {field}: {value}"}), 400

    save_models(models)
    log.info(f"Updated model {model} meta: {changes}")
    return jsonify({"status": "meta updated", "model": model, "changes": changes})


@models_bp.route("/api/model-file", methods=["POST"])
def api_model_file():
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


@models_bp.route("/api/export-metrics", methods=["POST"])
def api_export_metrics():
    """Export current model metrics and configuration to CSV."""
    import csv
    from datetime import datetime
    import os
    from config import BASE_DIR
    
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    models = get_models()
    # Case-insensitive lookup
    model_key = None
    for k in models:
        if k.lower() == model.lower():
            model_key = k
            break
    if model_key is None:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    
    meta = models[model_key]
    port = meta.get("port", 0)
    if not port:
        return jsonify({"error": "Model has no port assigned"}), 400
    
    # Check if model is running
    if not check_port_active(port):
        return jsonify({"error": "Model is not running"}), 400
    
    # Fetch metrics from the model's port
    try:
        import requests
        resp = requests.get(f"http://127.0.0.1:{port}/metrics", timeout=5)
        if not resp.ok:
            return jsonify({"error": "Failed to fetch metrics from model"}), 500
        metrics_text = resp.text
    except Exception as e:
        return jsonify({"error": f"Error fetching metrics: {e}"}), 500
    
    # Parse metrics
    metrics = {}
    for line in metrics_text.split('\n'):
        if line.startswith('#') or not line.strip():
            continue
        match = line.match(r'^([^{}\s]+)(?:\{([^}]*)\})?\s+(.+)$') if False else None
        # Simple parsing for Prometheus format
        parts = line.split()
        if len(parts) >= 2:
            name = parts[0]
            try:
                value = float(parts[-1])
                metrics[name] = value
            except ValueError:
                pass
    
    # Calculate derived metrics
    prompt_sec = metrics.get('llamacpp:prompt_seconds_total', 0)
    prompt_tokens = metrics.get('llamacpp:prompt_tokens_total', 0)
    gen_sec = metrics.get('llamacpp:tokens_predicted_seconds_total', 0)
    gen_tokens = metrics.get('llamacpp:tokens_predicted_total', 0)
    # Calculate rates from counters (more reliable than gauge metrics)
    prompt_rate = (prompt_tokens / prompt_sec) if prompt_sec > 0 else 0
    gen_rate = (gen_tokens / gen_sec) if gen_sec > 0 else 0
    
    prompt_eval_ms = (prompt_sec / prompt_tokens * 1000) if prompt_tokens > 0 else 0
    eval_ms = (gen_sec / gen_tokens * 1000) if gen_tokens > 0 else 0
    
    # Speculative decoding
    draft_tokens = metrics.get('llamacpp:spec_decode_num_draft_tokens_total', 0)
    accepted_tokens = metrics.get('llamacpp:spec_decode_num_accepted_tokens_total', 0)
    drafts = metrics.get('llamacpp:spec_decode_num_drafts_total', 0)
    acceptance_rate = (accepted_tokens / draft_tokens * 100) if draft_tokens > 0 else 0
    avg_draft_len = (draft_tokens / drafts) if drafts > 0 else 0
    accepted_per_step = (accepted_tokens / drafts) if drafts > 0 else 0
    speedup = (1 + accepted_per_step) if drafts > 0 else 1
    
    # CSV file path - one per model
    csv_dir = BASE_DIR / "metrics_exports"
    csv_dir.mkdir(exist_ok=True)
    safe_name = model.replace('/', '_').replace(' ', '_')
    csv_path = csv_dir / f"{safe_name}_metrics.csv"
    
    # Prepare row data
    now = datetime.now()
    row = {
        "timestamp": now.isoformat(),
        "model": model,
        "port": port,
        # Prompt metrics
        "prompt_seconds_total": prompt_sec,
        "prompt_tokens_total": prompt_tokens,
        "prompt_eval_ms_per_token": round(prompt_eval_ms, 1),
        "prompt_eval_rate_tokens_per_sec": round(prompt_rate, 1),
        # Generation metrics
        "gen_seconds_total": gen_sec,
        "gen_tokens_total": gen_tokens,
        "gen_eval_ms_per_token": round(eval_ms, 1),
        "gen_rate_tokens_per_sec": round(gen_rate, 1),
        # Speculative decoding
        "spec_draft_tokens_total": draft_tokens,
        "spec_accepted_tokens_total": accepted_tokens,
        "spec_drafts_total": drafts,
        "spec_acceptance_rate_pct": round(acceptance_rate, 1),
        "spec_avg_draft_len": round(avg_draft_len, 1),
        "spec_est_speedup": round(speedup, 2),
        # Configuration
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

    # Write/append to CSV
    file_exists = csv_path.exists()
    fieldnames = list(row.keys())

    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    return jsonify({
        "status": "exported",
        "csv_path": str(csv_path),
        "row": row
    })


@models_bp.route("/api/get-metrics-from-csv", methods=["POST"])
def api_get_metrics_from_csv():
    """Get historical metrics from CSV for stopped models."""
    import csv
    import os
    from config import BASE_DIR
    
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    time_range = data.get("time_range", "24h")  # Default to last 24 hours
    if not model:
        return jsonify({"error": "Missing model name"}), 400

    # Parse time range and calculate cutoff
    cutoff = None
    now = datetime.now()
    if time_range == "1h":
        cutoff = now - timedelta(hours=1)
    elif time_range == "6h":
        cutoff = now - timedelta(hours=6)
    elif time_range == "12h":
        cutoff = now - timedelta(hours=12)
    elif time_range == "24h":
        cutoff = now - timedelta(hours=24)
    elif time_range == "7d":
        cutoff = now - timedelta(days=7)
    
    # Case-insensitive lookup
    model_key = None
    models = get_models()
    for k in models:
        if k.lower() == model.lower():
            model_key = k
            break
    
    if model_key is None:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    
    # Construct CSV path
    csv_dir = BASE_DIR / "metrics_exports"
    csv_dir.mkdir(exist_ok=True)
    safe_name = model.replace('/', '_').replace(' ', '_')
    csv_path = csv_dir / f"{safe_name}_metrics.csv"
    
    if not csv_path.exists():
        return jsonify({
            "error": f"No historical metrics found for model '{model}'. Please enable metrics export in the configuration and run for at least one iteration.",
            "models": models,
        })
    
    # Read CSV file
    metrics_list = []
    fieldnames = None
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            # Convert string values to appropriate types
            metrics_list.append({
                "timestamp": row.get("timestamp", ""),
                "model": row.get("model", model),
                "port": int(row.get("port", 0)),
                "prompt_seconds_total": float(row.get("prompt_seconds_total", 0)),
                "prompt_tokens_total": float(row.get("prompt_tokens_total", 0)),
                "prompt_eval_ms_per_token": float(row.get("prompt_eval_ms_per_token", 0)),
                "prompt_eval_rate_tokens_per_sec": float(row.get("prompt_eval_rate_tokens_per_sec", 0)),
                "gen_seconds_total": float(row.get("gen_seconds_total", 0)),
                "gen_tokens_total": float(row.get("gen_tokens_total", 0)),
                "gen_eval_ms_per_token": float(row.get("gen_eval_ms_per_token", 0)),
                "gen_rate_tokens_per_sec": float(row.get("gen_rate_tokens_per_sec", 0)),
                "spec_draft_tokens_total": float(row.get("spec_draft_tokens_total", 0)),
                "spec_accepted_tokens_total": float(row.get("spec_accepted_tokens_total", 0)),
                "spec_drafts_total": float(row.get("spec_drafts_total", 0)),
                "spec_acceptance_rate_pct": float(row.get("spec_acceptance_rate_pct", 0)),
                "spec_avg_draft_len": float(row.get("spec_avg_draft_len", 0)),
                "spec_est_speedup": float(row.get("spec_est_speedup", 1)),
            })
    
    if not metrics_list:
        return jsonify({"error": "CSV file is empty"}), 400
    
    # Filter by time range if cutoff is set
    if cutoff is not None:
        filtered_metrics = []
        for m in metrics_list:
            ts_str = m.get("timestamp", "")
            if ts_str:
                try:
                    # Try parsing ISO format timestamp
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    if ts >= cutoff:
                        filtered_metrics.append(m)
                except ValueError:
                    # If parsing fails, include the entry (can't filter)
                    filtered_metrics.append(m)
            else:
                filtered_metrics.append(m)
        metrics_list = filtered_metrics
    
    return jsonify({
        "status": "success",
        "metrics": metrics_list,
        "csv_path": str(csv_path),
        "num_rows": len(metrics_list),
    })


@models_bp.route("/api/get-metrics", methods=["POST"])
def api_get_metrics():
    """Get current model metrics for performance graph."""
    from datetime import datetime, timedelta

    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    
    models = get_models()
    # Case-insensitive lookup
    model_key = None
    for k in models:
        if k.lower() == model.lower():
            model_key = k
            break
    if model_key is None:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    
    meta = models[model_key]
    port = meta.get("port", 0)
    if not port:
        return jsonify({"error": "Model has no port assigned"}), 400
    
    # Fetch metrics from the model's port. fetch_metrics_snapshot tries
    # get_server_host() first, then 127.0.0.1 (e.g. when get_server_host()
    # returns a WSL/Windows host IP that is unreachable for a locally-running
    # llama-server) and finally falls back to the last successful snapshot
    # when the model is too busy to answer within the timeout.
    snapshot = fetch_metrics_snapshot(port, timeout=GET_METRICS_TIMEOUT_SECONDS)

    if not snapshot.values:
        # Nothing to show: the model is not running or did not answer in time.
        return jsonify({
            "status": "unavailable",
            "available": False,
            "stale": True,
            "warning": f"Failed to fetch metrics from model: {snapshot.error}",
            "metrics": {},
        })

    metrics = snapshot.values

    # Calculate derived metrics
    prompt_sec = metrics.get('llamacpp:prompt_seconds_total', 0)
    prompt_tokens = metrics.get('llamacpp:prompt_tokens_total', 0)
    gen_sec = metrics.get('llamacpp:tokens_predicted_seconds_total', 0)
    gen_tokens = metrics.get('llamacpp:tokens_predicted_total', 0)
    # Calculate rates from counters (more reliable than gauge metrics)
    prompt_rate = (prompt_tokens / prompt_sec) if prompt_sec > 0 else 0
    gen_rate = (gen_tokens / gen_sec) if gen_sec > 0 else 0
    
    prompt_eval_ms = (prompt_sec / prompt_tokens * 1000) if prompt_tokens > 0 else 0
    eval_ms = (gen_sec / gen_tokens * 1000) if gen_tokens > 0 else 0
    
    # Speculative decoding
    draft_tokens = metrics.get('llamacpp:spec_decode_num_draft_tokens_total', 0)
    accepted_tokens = metrics.get('llamacpp:spec_decode_num_accepted_tokens_total', 0)
    drafts = metrics.get('llamacpp:spec_decode_num_drafts_total', 0)
    acceptance_rate = (accepted_tokens / draft_tokens * 100) if draft_tokens > 0 else 0
    avg_draft_len = (draft_tokens / drafts) if drafts > 0 else 0
    accepted_per_step = (accepted_tokens / drafts) if drafts > 0 else 0
    speedup = (1 + accepted_per_step) if drafts > 0 else 1
    
    # Prepare the response data
    sampled_at = datetime.now()
    if snapshot.age_seconds:
        # Stale snapshot: report when the data was actually scraped.
        sampled_at = sampled_at - timedelta(seconds=snapshot.age_seconds)
    response_data = {
        "model": model,
        "timestamp": sampled_at.isoformat(),
        # Prompt metrics
        "prompt_seconds_total": prompt_sec,
        "prompt_tokens_total": prompt_tokens,
        "prompt_eval_ms_per_token": round(prompt_eval_ms, 1),
        "prompt_rate_tokens_per_sec": round(prompt_rate, 1),
        # Generation metrics
        "gen_seconds_total": gen_sec,
        "gen_tokens_total": gen_tokens,
        "gen_eval_ms_per_token": round(eval_ms, 1),
        "gen_rate_tokens_per_sec": round(gen_rate, 1),
        # Speculative decoding
        "spec_draft_tokens_total": draft_tokens,
        "spec_accepted_tokens_total": accepted_tokens,
        "spec_drafts_total": drafts,
        "spec_acceptance_rate_pct": round(acceptance_rate, 1),
        "spec_avg_draft_len": round(avg_draft_len, 1),
        "spec_est_speedup": round(speedup, 2),
        # Configuration
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
    
    return jsonify({
        "status": "success",
        "available": True,
        "stale": snapshot.stale,
        "warning": snapshot.error,
        "cache_age_seconds": (
            round(snapshot.age_seconds, 1) if snapshot.age_seconds is not None else None
        ),
        "metrics": response_data
    })


@models_bp.route("/api/export-config", methods=["GET"])
def api_get_export_config():
    """Get the current metrics export configuration."""
    scheduler = current_app.config.get("SCHEDULER")
    if scheduler is None:
        from config import (
            METRICS_EXPORT_ENABLED,
            METRICS_EXPORT_INTERVAL_SECONDS,
            METRICS_EXPORT_MODELS,
        )
        return jsonify({
            "enabled": METRICS_EXPORT_ENABLED,
            "interval_seconds": METRICS_EXPORT_INTERVAL_SECONDS,
            "models": list(METRICS_EXPORT_MODELS),
        })
    return jsonify(scheduler.get_config())


@models_bp.route("/api/export-config", methods=["POST"])
def api_set_export_config():
    """Update the metrics export configuration at runtime."""
    scheduler = current_app.config.get("SCHEDULER")
    if scheduler is None:
        return jsonify({"error": "Scheduler not initialized"}), 503

    data = request.get_json(force=True) or {}
    try:
        config = scheduler.update_config(
            enabled=data.get("enabled"),
            interval_seconds=data.get("interval_seconds"),
            models=data.get("models"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({
        "status": "export config updated",
        "config": config,
    })


def check_port_active(port):
    """Check if a port is active."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except Exception:
        return False


@models_bp.route("/api/refresh-models", methods=["POST"])
def api_refresh_models():
    """Force refresh of the models cache by re-reading models.json and scanning filesystem."""
    try:
        refresh_models_cache()
        models = get_models()
        # Return the refreshed model list
        return jsonify({
            "status": "refreshed",
            "models": {name: meta for name, meta in models.items()},
            "count": len(models)
        })
    except Exception as e:
        log.exception("Failed to refresh models cache")
        return jsonify({"error": f"Failed to refresh models: {str(e)}"}), 500
