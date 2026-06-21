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
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    models[model]["repo"] = repo
    save_models(models)

    return jsonify({"status": "repo updated", "model": model, "repo": repo})


@models_bp.route("/api/update-model-meta", methods=["POST"])
def api_update_model_meta():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400

    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    meta = models[model]
    allowed_fields = {
        "cache_size": int,
        "threads": int,
        "threads_batch": int,
        "timeout": int,
        "mlock": bool,
        "flash_attn": str,
        "no_mmap": bool,
        "parallel": int,
        "ubatch_size": int,
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
                else:
                    value = str(value)
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
