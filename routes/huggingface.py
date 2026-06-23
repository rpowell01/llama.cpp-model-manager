"""Hugging Face integration: search, metadata, and discovery."""
from flask import Blueprint, jsonify, request
import requests
from logging import getLogger

from config import BASE_DIR
from utils import estimate_gguf_size

hf_bp = Blueprint("huggingface", __name__)
log = getLogger("llamaweb")


@hf_bp.route("/api/hf-repo-search", methods=["POST"])
def api_hf_repo_search():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    # Get models to validate existence (optional)
    from models import get_models
    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    try:
        resp = requests.get(
            "https://huggingface.co/api/models",
            params={"search": model, "limit": 10, "full": "true"},
            timeout=10
        )
        if resp.status_code != 200:
            return jsonify({"error": f"HF search failed: HTTP {resp.status_code}"}), 500
        hf_models = resp.json()
        if not hf_models:
            return jsonify({"error": f"No model found on Hugging Face for '{model}'"}), 404

        results = []
        for hf_model in hf_models:
            repo = hf_model["id"]
            for file in hf_model.get("siblings", []):
                if file.get("rfilename", "").lower().endswith(".gguf"):
                    filename = file["rfilename"]
                    size = file.get("size", 0)
                    if not size:
                        size = estimate_gguf_size(repo, filename)
                    results.append({
                        "repo": repo,
                        "filename": filename,
                        "size": size,
                        "downloads": hf_model.get("downloads", 0),
                        "likes": hf_model.get("likes", 0)
                    })
        if not results:
            return jsonify({"error": f"No GGUF file found for '{model}' on Hugging Face"}), 404
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        log.error(f"HF search error: {e}")
        return jsonify({"error": str(e)}), 500


@hf_bp.route("/api/hf-metadata", methods=["GET"])
def api_hf_metadata():
    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing 'model' query param"}), 400

    from models import get_models
    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    meta = models[model]
    repo = meta.get("repo", "")
    if not repo:
        return jsonify({"error": "Model has no Hugging Face repo configured"}), 400

    try:
        resp = requests.get(f"https://huggingface.co/api/models/{repo}", timeout=10)
        if resp.status_code != 200:
            log.warning(f"HF metadata fetch failed for {repo}: HTTP {resp.status_code}")
            return jsonify({"error": f"Failed to fetch metadata: HTTP {resp.status_code}"}), 502

        data = resp.json()
        result = {
            "repo": repo,
            "downloads": data.get("downloads", 0),
            "likes": data.get("likes", 0),
            "tags": data.get("tags", []),
            "author": repo.split("/")[0] if "/" in repo else repo,
            "lastModified": data.get("lastModified", ""),
            "description": data.get("description", "")[:500] if data.get("description") else "",
            "pipeline_tag": data.get("pipeline_tag", ""),
        }
        return jsonify(result)
    except requests.exceptions.RequestException as e:
        log.error(f"HF metadata request error for {repo}: {e}")
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        log.error(f"HF metadata unexpected error for {repo}: {e}")
        return jsonify({"error": "Unexpected error"}), 500


@hf_bp.route("/api/search-models", methods=["POST"])
def api_search_models():
    try:
        data = request.get_json()
        query = data.get("query", "").strip()
        if not query:
            return jsonify([])

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
            gguf_file = None
            for file in model.get("siblings", []):
                if file.get("rfilename", "").lower().endswith(".gguf"):
                    gguf_file = file
                    break
            if not gguf_file:
                continue

            filename = gguf_file.get("rfilename", "")
            size = gguf_file.get("size", 0)

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


@hf_bp.route("/api/hf-repo-files", methods=["POST"])
def api_hf_repo_files():
    """Return all GGUF files from a specific Hugging Face repository."""
    data = request.get_json(force=True) or {}
    repo = data.get("repo", "").strip()
    if not repo:
        return jsonify({"error": "Missing 'repo' parameter"}), 400

    try:
        resp = requests.get(f"https://huggingface.co/api/models/{repo}", timeout=10)
        if resp.status_code != 200:
            log.warning(f"HF repo fetch failed for {repo}: HTTP {resp.status_code}")
            return jsonify({"error": f"Repository not found or inaccessible"}), 404

        hf_model = resp.json()
        files = []
        for file in hf_model.get("siblings", []):
            if file.get("rfilename", "").lower().endswith(".gguf"):
                filename = file["rfilename"]
                size = file.get("size", 0)
                if not size:
                    size = estimate_gguf_size(repo, filename)
                files.append({
                    "filename": filename,
                    "size": size,
                    "url": f"https://huggingface.co/{repo}/resolve/main/{filename}",
                    "downloads": hf_model.get("downloads", 0),
                    "likes": hf_model.get("likes", 0),
                    "repo": repo
                })

        if not files:
            return jsonify({"error": f"No GGUF files found in repository '{repo}'"}), 404

        return jsonify({"repo": repo, "files": files})
    except requests.exceptions.RequestException as e:
        log.error(f"HF repo files network error for {repo}: {e}")
        return jsonify({"error": f"Network error: {e}"}), 502
    except Exception as e:
        log.error(f"HF repo files error for {repo}: {e}")
        return jsonify({"error": str(e)}), 500
