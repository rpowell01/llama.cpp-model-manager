"""System operations: logs and shutdown."""
from flask import Blueprint, jsonify
import threading
import time
import os
from logging import getLogger

from config import LOG_FILE

system_bp = Blueprint("system", __name__)
log = getLogger("llamaweb")


@system_bp.route("/api/logs")
def api_logs():
    if not LOG_FILE.exists():
        return jsonify([])
    lines = LOG_FILE.read_text("utf-8", errors="replace").strip().splitlines()
    return jsonify(lines[-100:])


@system_bp.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    log.info("Shutdown requested via API")
    def shutdown():
        time.sleep(0.1)
        os._exit(0)
    threading.Thread(target=shutdown, daemon=True).start()
    return jsonify({"status": "shutdown"})
