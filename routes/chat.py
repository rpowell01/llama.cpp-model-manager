"""Chat proxy to running model."""
from flask import Blueprint, jsonify, request, g
import requests
import logging

from config import BASE_DIR, Config
from models import ModelRegistry, get_models
from processes import check_port_active, get_server_host, _get_wsl_host_ip
from config import WSL, LLAMA_SERVER
from routes.status import _status_for_model

chat_bp = Blueprint("chat", __name__)
log = logging.getLogger("llamaweb")

# Global model registry instance for chat
_chat_registry = None


def get_chat_registry() -> ModelRegistry:
    """Get or create the model registry instance for chat."""
    global _chat_registry
    if _chat_registry is None:
        _chat_registry = ModelRegistry(Config())
    return _chat_registry


@chat_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """Chat completion endpoint - queries ModelRegistry for loaded models."""
    # Use registry from context (injected by middleware) or create one
    registry = g.get('registry') or get_chat_registry()
    
    data = request.get_json(force=True) or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model name"}), 400

    # Always query ModelRegistry for loaded models
    if registry.is_remote_mode:
        # In remote mode, check if model is loaded via the remote API
        remote_model = registry._remote_client.get_model(model)
        if not remote_model:
            return jsonify({"error": f"Model '{model}' is not loaded on remote server"}), 400
        port = remote_model.port
    else:
        # In local mode, check local cache and port
        models = registry.list_models()
        model_obj = None
        for m in models:
            if m.name == model:
                model_obj = m
                break
        
        if not model_obj:
            return jsonify({"error": f"Unknown model '{model}'"}), 404
        
        port = model_obj.port
        if not port:
            return jsonify({"error": "Model has no assigned port"}), 400
        
        try:
            port_int = int(port)
        except (TypeError, ValueError):
            log.error(f"Invalid port type for model {model}: {port!r}")
            return jsonify({"error": "Model has invalid port configuration"}), 500

        # Check if model is starting
        local_models = get_models()
        if model in local_models:
            status_info = _status_for_model(model, local_models[model], registry)
            if status_info.get("status") == "starting":
                return jsonify({"error": "Model is starting", "status": "starting"}), 503

        if not check_port_active(port_int):
            return jsonify({"error": f"Model '{model}' is not running (port {port} inactive)"}), 400

    payload = dict(data)
    payload["stream"] = False
    if "messages" not in payload:
        return jsonify({"error": "Missing 'messages' field"}), 400

    try:
        client_timeout = data.get("timeout")
        if client_timeout is not None:
            try:
                timeout = max(1, min(int(client_timeout), 600))
            except (ValueError, TypeError):
                timeout = 300
        else:
            timeout = 300
        
        # Determine hosts to try - in WSL+Windows mode, try multiple options
        hosts_to_try = []
        if WSL and LLAMA_SERVER and str(LLAMA_SERVER).endswith('.exe'):
            wsl_host = _get_wsl_host_ip()
            if wsl_host:
                hosts_to_try.append(wsl_host)
            hosts_to_try.append("127.0.0.1")
        else:
            hosts_to_try.append(get_server_host())
        
        # Try each host until one succeeds
        last_error = None
        resp = None
        for host in hosts_to_try:
            target_url = f"http://{host}:{port_int}/v1/chat/completions"
            log.info(f"Chat proxy to {model} at {target_url}")
            try:
                resp = requests.post(target_url, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    break  # Success, exit the loop
                last_error = f"Model API returned {resp.status_code}"
            except requests.exceptions.RequestException as e:
                last_error = str(e)
                continue
        else:
            # All hosts failed
            log.error(f"Chat proxy error for {model} on port {port_int}: {last_error}")
            return jsonify({"error": "Failed to communicate with model server"}), 500
        
        if resp.status_code != 200:
            log.error(f"Model API returned {resp.status_code}: {resp.text[:500]}")
            return jsonify({"error": "Model API returned an error"}), resp.status_code
        try:
            json_data = resp.json()
        except Exception as e:
            log.error(f"Invalid JSON from model {model} on port {port_int}: {resp.text[:500]}")
            return jsonify({"error": "Model returned invalid JSON response"}), 500
        return jsonify(json_data)
    except requests.exceptions.RequestException as e:
        log.error(f"Chat proxy error for {model} on port {port_int}: {e}")
        return jsonify({"error": "Failed to communicate with model server"}), 500
