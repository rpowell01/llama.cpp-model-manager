#!/usr/bin/env python3
"""
End-to-end integration tests for local and remote modes.
"""

import json
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).parent.parent))


class MockRemoteServerHandler(BaseHTTPRequestHandler):
    """Handler for mock llama.cpp server."""
    
    def log_message(self, format, *args):
        pass
    
    def do_GET(self):
        if self.path == "/api/models":
            self._send_json({"models": [self.server.models_data]})
        elif self.path == "/" or self.path == "/status":
            # Real llama.cpp server has / at root for status (returns HTML)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>llama.cpp server</body></html>")
        elif self.path == "/api/status":
            # Also support /api/status for backwards compatibility
            self._send_json({"status": "running", "models": [self.server.models_data["name"]]})
        elif self.path.startswith("/api/models/"):
            model_name = self.path.split("/")[-1]
            if model_name == self.server.models_data["name"]:
                self._send_json(self.server.models_data)
            else:
                self.send_error(404, "Model not found")
        else:
            self.send_error(404, "Not found")
    
    def do_POST(self):
        if self.path == "/api/models/load":
            self._send_json({"status": "loaded"})
        elif self.path == "/api/models/unload":
            self._send_json({"status": "unloaded"})
        else:
            self.send_error(404, "Not found")
    
    def _send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


@pytest.fixture
def mock_remote_server():
    """Fixture for mock remote server."""
    port = 9999
    models_data = {
        "name": "test-model",
        "repo": "test/repo",
        "filename": "test-model.gguf",
        "model_path": "/models/test-model.gguf",
        "expected_size": 1000000000,
        "port": port,
        "cache_size": 32768,
        "threads": 16,
        "threads_batch": 22,
        "timeout": 3600,
        "mlock": True,
        "flash_attn": "off",
        "no_mmap": True,
        "parallel": 4,
        "ubatch_size": 512,
    }
    
    server = HTTPServer(("127.0.0.1", port), MockRemoteServerHandler)
    server.models_data = models_data
    server.ready = True
    
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    
    yield {"port": port, "models": models_data}
    
    server.shutdown()


@pytest.fixture
def app_with_remote_mode(mock_remote_server):
    """Create a Flask app configured for remote mode with mock server."""
    from routes import lifecycle, chat, status
    
    # Reset the global registries before setting up
    status._status_registry = None
    lifecycle._model_registry = None
    
    env = {
        "REMOTE_MODE": "true",
        "LLAMA_CPP_SERVER_URL": f"http://127.0.0.1:{mock_remote_server['port']}"
    }
    
    with patch.dict(os.environ, env, clear=False):
        # Create fresh Config
        from config import Config
        cfg = Config()
        
        app = Flask(__name__)
        app.register_blueprint(lifecycle.lifecycle_bp)
        app.register_blueprint(chat.chat_bp)
        app.register_blueprint(status.status_bp)
        app.config["TESTING"] = True
        yield app, cfg


@pytest.fixture
def app_with_local_mode():
    """Create a Flask app configured for local mode."""
    from routes import lifecycle, chat, status
    
    # Reset the global registries before setting up
    status._status_registry = None
    lifecycle._model_registry = None
    
    with patch.dict(os.environ, {"REMOTE_MODE": "false"}, clear=False):
        # Create fresh Config
        from config import Config
        cfg = Config()
        
        app = Flask(__name__)
        app.register_blueprint(lifecycle.lifecycle_bp)
        app.register_blueprint(chat.chat_bp)
        app.register_blueprint(status.status_bp)
        app.config["TESTING"] = True
        yield app, cfg


class TestRemoteModeIntegration:
    """End-to-end tests for remote mode."""
    
    def test_remote_mode_start_endpoint(self, app_with_remote_mode):
        """Test /api/start in remote mode returns 202 Accepted."""
        app, cfg = app_with_remote_mode
        client = app.test_client()
        response = client.post("/api/start",
            json={"model": "test-model"},
            content_type="application/json")
        assert response.status_code == 202
        data = response.get_json()
        assert data["status"] == "loading"
        assert data["model"] == "test-model"
    
    def test_remote_mode_stop_endpoint(self, app_with_remote_mode):
        """Test /api/stop in remote mode returns stopped status."""
        app, cfg = app_with_remote_mode
        client = app.test_client()
        response = client.post("/api/stop",
            json={"model": "test-model"},
            content_type="application/json")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "stopped"
    
    def test_remote_mode_status_endpoint(self, app_with_remote_mode):
        """Test /api/status in remote mode shows correct mode."""
        app, cfg = app_with_remote_mode
        client = app.test_client()
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["mode"] == "remote"
        assert "models" in data
    
    def test_remote_mode_chat_missing_model(self, app_with_remote_mode):
        """Test /api/chat in remote mode with missing model returns 400."""
        app, cfg = app_with_remote_mode
        client = app.test_client()
        response = client.post("/api/chat",
            json={"model": "nonexistent-model", "messages": [{"role": "user", "content": "hello"}]},
            content_type="application/json")
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data


class TestLocalModeIntegration:
    """End-to-end tests for local mode."""
    
    def test_local_mode_status_endpoint(self, app_with_local_mode):
        """Test /api/status in local mode shows correct mode."""
        app, cfg = app_with_local_mode
        client = app.test_client()
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["mode"] == "local"
        assert "models" in data
    
    def test_local_mode_next_port_endpoint(self, app_with_local_mode):
        """Test /api/next-port in local mode returns available port."""
        app, cfg = app_with_local_mode
        client = app.test_client()
        response = client.get("/api/next-port")
        assert response.status_code == 200
        data = response.get_json()
        assert "port" in data
        assert data["port"] > 0
    
    def test_local_mode_start_missing_model(self, app_with_local_mode):
        """Test /api/start in local mode with unknown model returns 404."""
        app, cfg = app_with_local_mode
        client = app.test_client()
        response = client.post("/api/start",
            json={"model": "unknown-model"},
            content_type="application/json")
        assert response.status_code == 404
        data = response.get_json()
        assert "error" in data