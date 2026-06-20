"""Chat proxy to running model."""
from flask import Blueprint, jsonify, request
import requests
import logging

from config import BASE_DIR
from models import get_models
from processes import check_port_active
from routes.status import _status_for_model

chat_bp = Blueprint("chat", __name__)
log = logging.getLogger("llamaweb")


@chat_bp.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400

    models = get_models()
    if model not in models:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    meta = models[model]
    port = meta.get("port")
    if not port:
        return jsonify({"error": "Model has no assigned port"}), 400
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        log.error(f"Invalid port type for model {model}: {port!r}")
        return jsonify({"error": "Model has invalid port configuration"}), 500

    # Check if model is starting
    status_info = _status_for_model(model, meta)
    if status_info.get("status") == "starting":
        return jsonify({"error": "Model is starting", "status": "starting"}), 503

    if not check_port_active(port_int):
        return jsonify({"error": f"Model '{model}' is not running (port {port} inactive)"}), 400

    payload = dict(data)
    payload["stream"] = False
    if "messages" not in payload:
        return jsonify({"error": "Missing 'messages' field"}), 400

    target_url = f"http://127.0.0.1:{port_int}/v1/chat/completions"
    try:
        client_timeout = data.get("timeout")
        if client_timeout is not None:
            try:
                timeout = max(1, min(int(client_timeout), 600))
            except (ValueError, TypeError):
                timeout = 300
        else:
            timeout = 300
        resp = requests.post(target_url, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return jsonify({"error": f"Model API returned {resp.status_code}", "details": resp.text}), resp.status_code
        try:
            json_data = resp.json()
        except Exception as e:
            log.error(f"Invalid JSON from model {model} on port {port_int}: {resp.text[:500]}")
            return jsonify({"error": "Model returned invalid JSON response", "details": resp.text[:500]}), 500
        return jsonify(json_data)
    except requests.exceptions.RequestException as e:
        log.error(f"Chat proxy error for {model} on port {port_int}: {e}")
        return jsonify({"error": str(e)}), 500
