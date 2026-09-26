"""Download management endpoints."""
import json
import re
from logging import getLogger
import time
from pathlib import Path
from typing import Generator

from flask import Blueprint, jsonify, request, Response
import requests

from config import BASE_DIR, DEFAULTS, MODELS_DIR, resolve_model_path
from downloads import start_download, download_status, download_lock
from models import get_models, save_models
from utils import estimate_gguf_size

log = getLogger("llamaweb")

downloads_bp = Blueprint("downloads", __name__)

@downloads_bp.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    url = data.get("url", "").strip()
    try:
        expected_size = int(data.get("expected_size", 0))
    except ValueError:
        expected_size = 0
    resume = data.get("resume", False)
    force = data.get("force", False)
    if not model or not url:
        return jsonify({"error": "Missing model or url"}), 400

    models = get_models()

    if force and model in models:
        meta = models[model]
        existing_filename = meta.get("filename", "")
        url_filename = url.split("/")[-1] if "/" in url else ""

        existing_normalized = existing_filename.lower()
        if existing_normalized.endswith(".gguf"):
            existing_normalized = existing_normalized[:-5]
        url_normalized = url_filename.lower()
        if url_normalized.endswith(".gguf"):
            url_normalized = url_normalized[:-5]

        existing_normalized = existing_normalized.replace('.', '-')
        url_normalized = url_normalized.replace('.', '-')

        if url_filename and url_normalized != existing_normalized:
            base_filename = url_filename[:-5] if url_filename.lower().endswith(".gguf") else url_filename
            new_model_name = base_filename.replace("_", " ").replace("-", " ").replace(".", " ")
            counter = 1
            original_name = new_model_name
            while new_model_name in models:
                new_model_name = f"{original_name} {counter}"
                counter += 1

            import re
            # Sanitize directory name for Windows
            safe_dir_name = re.sub(r'[<>:"|?*]', '', new_model_name.lower().replace(" ", "-"))
            model_dir = MODELS_DIR / safe_dir_name
            model_dir.mkdir(parents=True, exist_ok=True)
            model_path = model_dir / url_filename
            # Store path relative to BASE_DIR for compatibility
            try:
                rel_path = model_path.relative_to(BASE_DIR)
            except ValueError:
                # Model is outside BASE_DIR, store absolute path
                rel_path = model_path

            repo = ""
            if "huggingface.co/" in url:
                parts = url.split("huggingface.co/")[1].split("/")
                repo = "/".join(parts[:2]) if len(parts) >= 2 else ""

            port = 11000 + len(models) * 10

            new_meta = {
                "model_path": str(rel_path),
                "repo": repo,
                "filename": url_filename,
                "expected_size": expected_size,
                "port": port,
                "threads": DEFAULTS["threads"],
                "threads_batch": DEFAULTS["threads_batch"],
                "timeout": DEFAULTS["timeout"],
                "mlock": DEFAULTS["mlock"],
                "flash_attn": DEFAULTS["flash_attn"],
                "no_mmap": DEFAULTS["no_mmap"],
                "parallel": DEFAULTS["parallel"],
                "ubatch_size": DEFAULTS["ubatch_size"],
            }

            models[new_model_name] = new_meta
            save_models(models)
            log.info(f"Created new model entry for variant: {new_model_name}")

            start_download(new_model_name, url, expected_size, force=True)
            return jsonify({"status": "download started", "model": new_model_name, "is_variant": True})

    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    meta = models[model]
    # Handle both relative and absolute model paths using helper (respects MODEL_DIR env var)
    model_path_str = meta.get("model_path", "")
    target = resolve_model_path(model_path_str)
    target.parent.mkdir(parents=True, exist_ok=True)

    if force and target.exists():
        log.info(f"Force redownload requested for {model}: deleting existing file")
        target.unlink()
        start_download(model, url, expected_size, force=True)
        return jsonify({"status": "download started", "model": model, "force": True})

    actual_size = 0
    if resume and target.exists():
        stat = target.stat()
        actual_size = stat.st_size
        if expected_size and actual_size < expected_size * 0.9:
            pass
        else:
            return jsonify({"status": "complete", "model": model, "size": actual_size})

    # Start the download - the worker will handle resuming from existing file position
    start_download(model, url, expected_size, force=force)
    return jsonify({"status": "download started", "model": model, "resumed": resume})


@downloads_bp.route("/api/download-search", methods=["POST"])
def api_download_search():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    force = data.get("force", False)
    if not model:
        return jsonify({"error": "Missing model name"}), 400
    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404
    meta = models[model]

    # Handle both relative and absolute model paths using helper (respects MODEL_DIR env var)
    model_path_str = meta.get("model_path", "")
    out_path = resolve_model_path(model_path_str)
    if out_path.exists() and not force:
        existing_size = out_path.stat().st_size
        expected_size = meta.get("expected_size", 0)
        if expected_size and existing_size >= expected_size * 0.9:
            return jsonify({"status": "complete", "model": model, "size": existing_size, "results": []})

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
                    url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
                    results.append({
                        "repo": repo,
                        "filename": filename,
                        "size": size,
                        "url": url,
                        "downloads": hf_model.get("downloads", 0),
                        "likes": hf_model.get("likes", 0)
                    })

        if not results:
            return jsonify({"error": f"No GGUF file found for '{model}' on Hugging Face"}), 404

        if len(results) == 1:
            url = results[0]["url"]
            size = results[0]["size"]
            # Handle both relative and absolute model paths using helper (respects MODEL_DIR env var)
            model_path_str = meta.get("model_path", "")
            target = resolve_model_path(model_path_str)
            target.parent.mkdir(parents=True, exist_ok=True)
            start_download(model, url, size, force=force)
            return jsonify({"status": "download started", "model": model, "results": results})

        return jsonify({"status": "multiple_results", "model": model, "results": results})
    except Exception as e:
        log.error(f"HF search error: {e}")
        return jsonify({"error": str(e)}), 500


@downloads_bp.route("/api/download-search-filename", methods=["POST"])
def api_download_search_filename():
    data = request.get_json(force=True) or {}
    filename = data.get("filename", "").strip()
    force = data.get("force", False)
    if not filename:
        return jsonify({"error": "Missing filename"}), 400

    target_filename = filename.lower()
    if target_filename.endswith(".gguf"):
        target_filename = target_filename[:-5]

    try:
        base_name = filename[:-5] if filename.lower().endswith(".gguf") else filename

        quant_patterns = ['Q2_K', 'Q2_K_S', 'Q2_K_M', 'Q3_K_S', 'Q3_K_M', 'Q3_K_L',
                         'Q4_0', 'Q4_K_S', 'Q4_K_M', 'Q4_K_L',
                         'Q5_0', 'Q5_K_S', 'Q5_K_M', 'Q5_K_L',
                         'Q6_K', 'Q8_0', 'Q8_K', 'F16', 'BF16', 'F32']

        search_term = base_name
        stripped = False
        for sep in ['_', '-', '.']:
            for quant in quant_patterns:
                if base_name.upper().endswith(sep + quant):
                    search_term = base_name[:-len(sep + quant)]
                    stripped = True
                    break
            if stripped:
                break
        if not stripped:
            for quant in quant_patterns:
                if base_name.upper().endswith(quant):
                    search_term = base_name[:-len(quant)]
                    stripped = True
                    break

        if not search_term:
            fallback = base_name.split('_')[0].split('-')[0].split('.')[0]
            search_term = fallback if fallback else base_name

        log.info(f"Filename search: original='{filename}', extracted='{search_term}'")

        resp = requests.get(
            "https://huggingface.co/api/models",
            params={"search": search_term, "limit": 20, "full": "true"},
            timeout=10
        )
        if resp.status_code != 200:
            return jsonify({"error": f"HF search failed: HTTP {resp.status_code}"}), 500

        hf_models = resp.json()
        if not hf_models:
            return jsonify({"error": f"No model found on Hugging Face for '{search_term}'"}), 404

        results = []
        debug_candidates = []

        target_normalized = target_filename.replace('.', '-')
        for quant in ['q4_k_m', 'q4_k_s', 'q5_k_m', 'q8_0', 'f16', 'bf16', 'f32']:
            target_normalized = target_normalized.replace('.' + quant, '-' + quant)

        for hf_model in hf_models:
            repo = hf_model["id"]
            for file in hf_model.get("siblings", []):
                file_rfilename = file.get("rfilename", "")
                if file_rfilename.lower().endswith(".gguf"):
                    candidate = file_rfilename[:-5].lower() if file_rfilename.lower().endswith(".gguf") else file_rfilename.lower()
                    candidate_normalized = candidate.replace('.', '-')
                    for quant in ['q4_k_m', 'q4_k_s', 'q5_k_m', 'q8_0', 'f16', 'bf16', 'f32']:
                        candidate_normalized = candidate_normalized.replace('.' + quant, '-' + quant)

                    debug_candidates.append({
                        "file_rfilename": file_rfilename,
                        "candidate_normalized": candidate_normalized,
                        "target_normalized": target_normalized,
                        "match": candidate_normalized == target_normalized
                    })
                    if candidate_normalized == target_normalized:
                        size = file.get("size", 0)
                        if not size:
                            size = estimate_gguf_size(repo, file_rfilename)
                        url = f"https://huggingface.co/{repo}/resolve/main/{file_rfilename}"
                        results.append({
                            "repo": repo,
                            "filename": file_rfilename,
                            "size": size,
                            "url": url,
                            "downloads": hf_model.get("downloads", 0),
                            "likes": hf_model.get("likes", 0)
                        })

        log.info(f"Filename search debug: target='{filename}', target_normalized='{target_filename}', search_term='{search_term}'")
        log.info(f"HF returned {len(hf_models)} models, {len(debug_candidates)} GGUF files total")
        for dbg in debug_candidates[:10]:
            log.info(f"  Candidate: {dbg['file_rfilename']} -> normalized='{dbg['candidate_normalized']}' match={dbg['match']}")

        if not results:
            return jsonify({"error": f"No GGUF file matching '{filename}' found on Hugging Face"}), 404

        if len(results) == 1:
            url = results[0]["url"]
            size = results[0]["size"]
            return jsonify({"status": "single_result", "filename": filename, "results": results})

        return jsonify({"status": "multiple_results", "filename": filename, "results": results})
    except Exception as e:
        log.error(f"HF filename search error: {e}")
        return jsonify({"error": str(e)}), 500


@downloads_bp.route("/api/download-status")
def api_download_status():
    with download_lock:
        response = jsonify(download_status)
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response


@downloads_bp.route("/api/download-stream/<model_name>")
def api_download_stream(model_name: str):
    from urllib.parse import unquote
    model_name = unquote(model_name)

    def generate() -> Generator[str, None, None]:
        try:
            with download_lock:
                status = download_status.get(model_name, {"status": "waiting", "progress": 0})
            yield f"data: {json.dumps(status)}\n\n"

            last_status = status.get("status", "")
            while last_status not in ("complete", "error"):
                time.sleep(0.5)
                with download_lock:
                    status = download_status.get(model_name, {"status": "waiting", "progress": 0})
                yield f"data: {json.dumps(status)}\n\n"
                last_status = status.get("status", "")

            yield f"data: {json.dumps(status)}\n\n"
        except GeneratorExit:
            pass

    return Response(generate(), mimetype="text/event-stream", headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
    })
