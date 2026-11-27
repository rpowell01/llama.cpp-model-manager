#!/usr/bin/env python3
"""
llama.cpp web frontend - simplified entry point
"""

from __future__ import annotations

import atexit
import argparse
import logging
import socket
import sys
import threading
from pathlib import Path

# Ensure the repository root is on sys.path so that the `webapp` package
# and `routes` package are importable when running this script directly
# (e.g., `python webapp/main.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, render_template, request, jsonify

# Import configuration
from webapp.config import BASE_DIR, Config, METRICS_EXPORT_INTERVAL_SECONDS, WSL

# Import models functions
from webapp.models import load_models_cache

# Import scheduler
from webapp.scheduler import init_scheduler

# Import hardware detection
from webapp.routes.system import get_hardware_info

# Import route blueprints
from webapp.routes.archive import archive_bp
from webapp.routes.chat import chat_bp
from webapp.routes.downloads import downloads_bp
from webapp.routes.huggingface import hf_bp
from webapp.routes.lifecycle import lifecycle_bp
from webapp.routes.models import models_bp
from webapp.routes.status import status_bp
from webapp.routes.system import system_bp


def _get_wsl_ips() -> list[str]:
    """Get all WSL IP addresses in the 100.x - 172.x range (excluding docker 172.17.x)."""
    ips: list[str] = []
    try:
        # Method 1: Check network interfaces directly (most reliable for WSL)
        import subprocess
        result = subprocess.run(
            ["ip", "-4", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet ") and "scope global" in line:
                    # Format: "inet 172.x.x.x/20 brd ... scope global eth0"
                    parts = line.split()
                    if len(parts) >= 2:
                        ip = parts[1].split("/")[0]
                        # Skip loopback and docker interfaces (172.17.x.x)
                        if not ip.startswith("127.") and not ip.startswith("172.17."):
                            # Check if first octet is 100-172
                            try:
                                first_octet = int(ip.split(".")[0])
                                if 100 <= first_octet <= 172:
                                    ips.append(ip)
                            except (ValueError, IndexError):
                                pass
        
        # Method 2: Fallback to hostname resolution
        if not ips:
            hostname = socket.gethostname()
            host_ips = socket.gethostbyname_ex(hostname)[2]
            for ip in host_ips:
                if not ip.startswith("127.") and not ip.startswith("172.17."):
                    try:
                        first_octet = int(ip.split(".")[0])
                        if 100 <= first_octet <= 172:
                            ips.append(ip)
                    except (ValueError, IndexError):
                        pass
        
        # Remove duplicates while preserving order
        seen = set()
        unique_ips = []
        for ip in ips:
            if ip not in seen:
                seen.add(ip)
                unique_ips.append(ip)
        return unique_ips
    except Exception:
        return []


def _get_wsl_ip() -> str | None:
    """Get the first WSL IP address for backward compatibility."""
    ips = _get_wsl_ips()
    return ips[0] if ips else None


def main() -> None:
    # Configure logging with SSE handler for web console
    from webapp.state import add_log_entry
    
    # Suppress Werkzeug access logs for frequent polling endpoints
    # (e.g., /api/status/<model> called every 2s during model startup)
    werkzeug_logger = logging.getLogger("werkzeug")
    
    class StatusEndpointFilter(logging.Filter):
        """Filter to suppress access logs for frequent polling endpoints."""
        def filter(self, record):
            # Check if this is an access log message for endpoints we want to suppress
            msg = record.getMessage()
            if "GET" in msg:
                # Suppress individual model status checks (polled every 2s during startup)
                if "/api/status/" in msg:
                    return False
                # Suppress proxy archive journal polling
                if "/api/proxy/archive/journal" in msg:
                    return False
            return True
    
    werkzeug_logger.addFilter(StatusEndpointFilter())
    
    class SSELogHandler(logging.Handler):
        """Custom logging handler that forwards logs to SSE clients."""
        def emit(self, record):
            try:
                msg = self.format(record)
                color = "inherit"
                if record.levelno >= logging.ERROR:
                    color = "red"
                elif record.levelno >= logging.WARNING:
                    color = "yellow"
                add_log_entry(msg, color)
            except Exception:
                pass  # Don't let logging errors break the app

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(BASE_DIR / "logs" / "webapp.log"),
            logging.StreamHandler(sys.stderr),
            SSELogHandler()
        ]
    )
    log = logging.getLogger("llamaweb")

    cfg = Config()

    # Parse command line arguments early so we can set app config
    parser = argparse.ArgumentParser(description="llama.cpp web frontend")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Flask listen port (default 5000)")
    args = parser.parse_args()

    log.info("Creating Flask app...")
    # Create Flask app with static/template folders inside webapp/
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "webapp" / "static"),
        template_folder=str(BASE_DIR / "webapp" / "templates"),
    )
    log.info("Flask app created")
    
    # Set app config for template variables
    app.config["SERVER_HOST"] = args.host if args.host != "0.0.0.0" else "127.0.0.1"
    app.config["FLASK_PORT"] = args.port
    app.config["CACHE_VERSION"] = "1"

    # Register blueprints
    log.info("Registering blueprints...")
    app.register_blueprint(archive_bp)
    log.info("archive_bp registered")
    app.register_blueprint(chat_bp)
    log.info("chat_bp registered")
    app.register_blueprint(downloads_bp)
    log.info("downloads_bp registered")
    app.register_blueprint(hf_bp)
    log.info("hf_bp registered")
    app.register_blueprint(lifecycle_bp)
    log.info("lifecycle_bp registered")
    app.register_blueprint(models_bp)
    log.info("models_bp registered")
    app.register_blueprint(status_bp)
    log.info("status_bp registered")
    app.register_blueprint(system_bp)
    log.info("system_bp registered")
    log.info("All blueprints registered")

    # Add after_request wrapper for archive API envelope format
    @app.after_request
    def _wrap_archive_api_response(response):
        """Wrap archive API responses in {success: true, data: ...} envelope."""
        # Only apply to archive API routes
        if not request.path.startswith("/api/proxy/archive/"):
            return response
        
        # Don't wrap if already wrapped (check for success key in JSON)
        if response.is_json:
            try:
                data = response.get_json()
            except Exception:
                data = None
            
            if isinstance(data, dict) and "success" in data:
                return response  # Already wrapped
            
            # Wrap successful responses
            if 200 <= response.status_code < 300:
                wrapped = jsonify({"success": True, "data": data})
            else:
                # Wrap error responses
                error_msg = data.get("error", "Unknown error") if isinstance(data, dict) else "Unknown error"
                wrapped = jsonify({"success": False, "error": error_msg})
            
            # Create new response with wrapped body, preserving status and headers (except Content-Length)
            # Use Flask's Response class directly, not response.response_class (which doesn't exist on Response objects)
            from flask import Response as FlaskResponse
            new_response = FlaskResponse(
                response=wrapped.get_data(),
                status=response.status_code,
                headers=[(k, v) for k, v in response.headers if k.lower() != "content-length"],
                mimetype=wrapped.mimetype,
            )
            return new_response
        
        return response

    # Load models cache
    log.info("Loading models cache...")
    load_models_cache()
    log.info("Models cache loaded")

    # Start the export scheduler on the first request, once the Flask
    # server is accepting connections. This prevents the first export
    # cycle from running before the server is ready.
    scheduler_started = False
    scheduler_lock = threading.Lock()

    @app.before_request
    def _start_scheduler_on_first_request():
        nonlocal scheduler_started
        with scheduler_lock:
            if scheduler_started:
                return
            log.info("Initializing scheduler...")
            scheduler = init_scheduler(app, interval=METRICS_EXPORT_INTERVAL_SECONDS)
            app.config["SCHEDULER"] = scheduler
            atexit.register(scheduler.stop)
            scheduler_started = True
            log.info("Scheduler initialized")

    log.info("Starting web UI on http://%s:%d from %s", args.host, args.port, BASE_DIR)
    
    # Show WSL-specific connection info
    if WSL:
        wsl_ips = _get_wsl_ips()
        if wsl_ips:
            log.info("WSL detected - Access the web UI at:")
            for ip in wsl_ips:
                log.info("  http://%s:%d (WSL IP)", ip, args.port)
            log.info("  http://127.0.0.1:%d (via port forwarding)", args.port)
            log.info("Note: The Windows IP shown by Flask below is NOT accessible in WSL2 default networking mode.")

    log.info("Calling app.run()...")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    log.info("app.run() returned")


if __name__ == "__main__":
    main()
