#!/usr/bin/env python3
"""
Standalone test server for Archive Browser Playwright tests.
This script sets up a Flask app with mocked data and runs it on port 5001.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add project root to path FIRST
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import shared test helpers
from tests.conftest import (
    _create_test_archive_db,
    _write_journal,
)

# Apply patches BEFORE importing any webapp modules
test_models = {
    "test-model": {
        "name": "test-model",
        "port": 9931,
        "model_path": "/fake/path/model.gguf",
        "repo": "test/repo",
        "filename": "model.gguf",
        "expected_size": 1000000000,
        "mnemonic_proxy": {
            "enabled": True,
            "port": 8096,
            "data_dir": "./data/model",
            "slot_dir": "./data/model",
        }
    }
}


class MockProxyManager:
    proxy_port = 8096
    data_dir = "./data/model"
    _pid = 12345

    def _resolve_data_dir(self):
        # This will be set after tmp_path is created
        return getattr(self, '_tmp_path', Path.cwd()) / "data" / "model"


mock_proxy = MockProxyManager()

# Start patches globally
patches = [
    patch("webapp.routes.system.get_hardware_info", return_value={
        "cpu_count_logical": 8,
        "cpu_count_physical": 4,
        "memory_total_gb": 16,
        "memory_available_gb": 8,
        "gpu_info": [],
        "platform": "Linux",
        "platform_version": "5.15.0",
    }),
    patch("webapp.models.get_models", return_value=test_models),
    patch("webapp.routes.archive._get_model_config", return_value={"port": 9931, "mnemonic_proxy": {"enabled": True, "port": 8096}}),
    patch("webapp.routes.archive.get_proxy_manager", return_value=mock_proxy),
    patch("webapp.routes.status.get_models", return_value=test_models),
    patch("webapp.routes.status.get_status_registry", return_value=type('MockRegistry', (), {'is_remote_mode': False})()),
    patch("webapp.routes.status._get_model_statuses_parallel", return_value=[{
        "model": "test-model",
        "port": 9931,
        "status": "running",
        "pid": 12345,
        "file_exists": True,
        "file_size_gb": 1.0,
        "model_path": "/fake/path/model.gguf",
        "mnemonic_proxy": {"enabled": True, "running": True, "port": 8096},
        "repo": "test/repo",
        "filename": "model.gguf",
        "expected_size": 1000000000,
    }]),
]

for p in patches:
    p.start()

# NOW import webapp modules (after patches are active)
from flask import Flask
from webapp.config import BASE_DIR
from webapp.routes.archive import archive_bp
from webapp.routes.status import status_bp
from webapp.routes.system import system_bp
from webapp.routes.lifecycle import lifecycle_bp
from webapp.routes.models import models_bp
from webapp.routes.downloads import downloads_bp
from webapp.routes.huggingface import hf_bp
from webapp.routes.chat import chat_bp


def setup_test_data(tmp_path: Path):
    """Create test archive data."""
    journal_events = [
        {"ts": 1704100800.0, "event": "request", "conv": "conv-abc123", "prompt_tokens": 100},
        {"ts": 1704100801.0, "event": "recall", "conv": "conv-abc123"},
        {"ts": 1704100802.0, "event": "request", "conv": "conv-def456", "prompt_tokens": 200},
        {"ts": 1704187200.0, "event": "request", "conv": "conv-ghi789", "prompt_tokens": 300},
        {"ts": 1704326400.0, "event": "request", "conv": "conv-wildcard", "prompt_tokens": 50},
        {"ts": 1704326401.0, "event": "recall", "conv": "conv-wildcard"},
    ]

    # Create archive DB and journal in the expected fixed location
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    journal_path = tmp_path / "data" / "model" / "journal.jsonl"
    _create_test_archive_db(archive_db)
    _write_journal(journal_path, journal_events)

    # Set the tmp_path on the mock proxy manager
    mock_proxy._tmp_path = tmp_path


def run_server():
    """Run the test server."""
    tmp_path = Path(tempfile.mkdtemp(prefix="archive_test_"))

    setup_test_data(tmp_path)

    # Create Flask app
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "webapp" / "static"),
        template_folder=str(BASE_DIR / "webapp" / "templates"),
    )
    app.config["TESTING"] = True
    app.config["SERVER_HOST"] = "127.0.0.1"
    app.config["FLASK_PORT"] = 5001
    app.config["CACHE_VERSION"] = "1"

    # Register all blueprints (like main.py does)
    app.register_blueprint(archive_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(lifecycle_bp)
    app.register_blueprint(models_bp)
    app.register_blueprint(downloads_bp)
    app.register_blueprint(hf_bp)
    app.register_blueprint(chat_bp)

    try:
        print("Starting test server on http://127.0.0.1:5001", file=sys.stderr)
        app.run(host="127.0.0.1", port=5001, debug=False, threaded=True, use_reloader=False)
    finally:
        for p in patches:
            p.stop()


if __name__ == "__main__":
    run_server()