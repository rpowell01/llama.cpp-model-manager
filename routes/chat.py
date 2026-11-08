"""Chat proxy to running model."""
from flask import Blueprint, jsonify, request, g
import requests
import logging

from config import BASE_DIR, Config
from models import ModelRegistry, get_models
from processes import check_port_active, get_server_host, _check_model_ready
from config import WSL
from routes.status import _status_for_model

chat_bp = Blueprint("chat", __name__)
log = logging.getLogger("llamaweb")

# Global model registry instance for chat
_chat_registry = None


def _get_model_slot_save_path(model_name: str) -> str | None:
    """Get the slot_save_path for a model from local models."""
    local_models = get_models()
    model_data = local_models.get(model_name)
    if model_data and model_data.get("slot_save_path"):
        return model_data["slot_save_path"]
    return None


def _generate_session_id(model_name: str, user_id: str = "default") -> str:
    """Generate a session ID for slot persistence."""
    sanitized = model_name.replace(" ", "_").replace("/", "_")
    return f"{sanitized}_{user_id}"


def _restore_slot_cache(model_name: str, port: int, session_id: str) -> bool:
    """Restore KV cache from slot file before request."""
    slot_save_path = _get_model_slot_save_path(model_name)
    if not slot_save_path:
        return False
    
    session_id_safe = session_id.replace(" ", "_").replace("/", "_")
    filename = f"{slot_save_path}/slot_{session_id_safe}.bin"
    
    try:
        resp = requests.post(
            f"http://127.0.0.1:{port}/slots/0/restore",
            json={"filename": filename},
            timeout=10
        )
        if resp.status_code == 200:
            logging.getLogger("llamaweb").info(f"Restored slot cache for {model_name} session {session_id}")
            return True
        elif resp.status_code == 404:
            # First message in session - no cache file yet
            logging.getLogger("llamaweb").debug(f"No slot cache found for {model_name} session {session_id}")
            return False
        else:
            logging.getLogger("llamaweb").warning(f"Slot restore failed for {model_name}: {resp.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logging.getLogger("llamaweb").warning(f"Slot restore error for {model_name}: {e}")
        return False


def _save_slot_cache(model_name: str, port: int, session_id: str) -> bool:
    """Save KV cache to slot file after response."""
    slot_save_path = _get_model_slot_save_path(model_name)
    if not slot_save_path:
        return False
    
    session_id_safe = session_id.replace(" ", "_").replace("/", "_")
    filename = f"{slot_save_path}/slot_{session_id_safe}.bin"
    
    try:
        resp = requests.post(
            f"http://127.0.0.1:{port}/slots/0/save",
            json={"filename": filename},
            timeout=10
        )
        if resp.status_code == 200:
            logging.getLogger("llamaweb").info(f"Saved slot cache for {model_name} session {session_id}")
            return True
        else:
            logging.getLogger("llamaweb").warning(f"Slot save failed for {model_name}: {resp.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logging.getLogger("llamaweb").warning(f"Slot save error for {model_name}: {e}")
        return False


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
        # In remote mode, use the remote server URL directly
        remote_url = registry._config.remote_url.rstrip("/")
        port = 80  # Default HTTP port for URL parsing
        host = remote_url.replace("http://", "").replace("https://", "")
        if ":" in host:
            host, port_str = host.split(":", 1)
            port = int(port_str)
        
        # In remote mode, we don't have mnemonic-proxy - proxy directly
        target_url = f"{remote_url}/v1/chat/completions"
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

        if not _check_model_ready(port_int, model):
            return jsonify({"error": f"Model '{model}' is not running (port {port} inactive)"}), 400
        
        # Resolve the model's platform so WSL+Windows models use the right host set
        model_platform = getattr(model_obj, 'llama_cpp_platform', 'auto') or "auto"
        _is_wsl_windows_model = WSL and model_platform == "windows"

        # Check if mnemonic-proxy is enabled for this model
        mnemonic_proxy_enabled = getattr(model_obj, 'mnemonic_proxy_enabled', False)
        proxy_port = getattr(model_obj, 'mnemonic_proxy_port', 8096) if mnemonic_proxy_enabled else None
        
        # Determine target URL: use mnemonic-proxy if enabled, otherwise llama-server directly
        if mnemonic_proxy_enabled and proxy_port:
            # Route through mnemonic-proxy for strata context management
            hosts_to_try = []
            if _is_wsl_windows_model:
                wsl_host = get_server_host()
                if wsl_host and wsl_host != "127.0.0.1":
                    hosts_to_try.append(wsl_host)
                hosts_to_try.append("127.0.0.1")
            else:
                hosts_to_try.append(get_server_host())
            
            target_url = None
            for host in hosts_to_try:
                url = f"http://{host}:{proxy_port}/v1/chat/completions"
                log.info(f"Chat proxy to {model} via mnemonic-proxy at {url}")
                try:
                    resp = requests.post(url, json=payload, timeout=timeout)
                    if resp.status_code == 200:
                        target_url = url
                        break
                except requests.exceptions.RequestException as e:
                    log.warning(f"Failed to connect to mnemonic-proxy at {url}: {e}")
                    continue
            
            if not target_url:
                log.warning(f"mnemonic-proxy unavailable for {model}, falling back to direct llama-server")
                mnemonic_proxy_enabled = False
        
        if not mnemonic_proxy_enabled:
            # Fallback: direct to llama-server
            hosts_to_try = []
            if _is_wsl_windows_model:
                wsl_host = get_server_host()
                if wsl_host and wsl_host != "127.0.0.1":
                    hosts_to_try.append(wsl_host)
                hosts_to_try.append("127.0.0.1")
            else:
                hosts_to_try.append(get_server_host())
            
            target_url = None
            for host in hosts_to_try:
                url = f"http://{host}:{port_int}/v1/chat/completions"
                log.info(f"Chat proxy to {model} at {url}")
                try:
                    resp = requests.post(url, json=payload, timeout=timeout)
                    if resp.status_code == 200:
                        target_url = url
                        break
                except requests.exceptions.RequestException as e:
                    log.warning(f"Failed to connect to {url}: {e}")
                    continue
            if not target_url:
                return jsonify({"error": "Failed to communicate with model server"}), 500

    # Generate session ID for slot persistence
    session_id = _generate_session_id(model)
    has_slot_cache = _get_model_slot_save_path(model) is not None
    
    # Restore slot cache before request (only for direct llama-server)
    if has_slot_cache and not mnemonic_proxy_enabled:
        _restore_slot_cache(model, port_int, session_id)

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
        
        # Make the chat request
        log.info(f"Chat proxy to {model} at {target_url}")
        resp = requests.post(target_url, json=payload, timeout=timeout)
        
        if resp.status_code != 200:
            log.error(f"Model API returned {resp.status_code}: {resp.text[:500]}")
            return jsonify({"error": "Model API returned an error"}), resp.status_code
        try:
            json_data = resp.json()
        except Exception as e:
            log.error(f"Invalid JSON from model {model}: {resp.text[:500]}")
            return jsonify({"error": "Model returned invalid JSON response"}), 500
        
        # Save slot cache after successful response (only for direct llama-server)
        if has_slot_cache and not mnemonic_proxy_enabled:
            _save_slot_cache(model, port_int, session_id)
        
        return jsonify(json_data)
    except requests.exceptions.RequestException as e:
        log.error(f"Chat proxy error for {model}: {e}")
        return jsonify({"error": "Failed to communicate with model server"}), 500
