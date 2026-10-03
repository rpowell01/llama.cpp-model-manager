"""Model CRUD operations."""
import requests
from flask import Blueprint, jsonify, request
from logging import getLogger

from config import DEFAULTS
from models import get_models, save_models, add_model_entry, delete_model_entry

log = getLogger("llamaweb")

models_bp = Blueprint("models", __name__)

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
        # Chat template
        chat_template_file=str(data.get("chat_template_file", DEFAULTS["chat_template_file"])),
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
    }
    changes = {}
    for field, typ in allowed_fields.items():
        if field in data:
            value = data[field]
            try:
                if typ is int:
                    value = int(value)
                elif typ is bool:
                    value = bool(value)
                elif typ is float:
                    value = float(value)
                else:
                    value = str(value)
                meta[field] = value
                changes[field] = value
            except Exception as e:
                return jsonify({"error": f"Invalid value for {field}: {value}"}), 400

    # Validate rating fields are within 1-5 range
    for field in ("rating_speed", "rating_coding"):
        if field in data:
            val = data[field]
            try:
                val_int = int(val)
            except (TypeError, ValueError):
                return jsonify({"error": f"Invalid rating value for {field}: must be an integer 1-5"}), 400
            if val_int < 1 or val_int > 5:
                return jsonify({"error": f"Rating {field} must be between 1 and 5"}), 400
            meta[field] = val_int

    # Validate comment length (max 500 chars)
    if "comment" in data:
        comment = data["comment"]
        if not isinstance(comment, str):
            try:
                comment = str(comment)
            except:
                return jsonify({"error": "Comment must be a string"}), 400
        if len(comment) > 500:
            return jsonify({"error": "Comment too long (max 500 characters)"}), 400
        meta["comment"] = comment

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


def check_port_active(port):
    """Check if a port is active."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except Exception:
        return False
