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
from pathlib import Path

from flask import Flask, render_template

# Import configuration
from config import BASE_DIR, Config, WSL

# Import models functions
from models import load_models_cache

# Import scheduler
from scheduler import init_scheduler

# Import hardware detection
from routes.system import get_hardware_info

# Import route blueprints
from routes.chat import chat_bp
from routes.downloads import downloads_bp
from routes.huggingface import hf_bp
from routes.lifecycle import lifecycle_bp
from routes.models import models_bp
from routes.status import status_bp
from routes.system import system_bp


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
    from state import add_log_entry
    
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
            logging.FileHandler(BASE_DIR / "webapp.log"),
            logging.StreamHandler(sys.stderr),
            SSELogHandler()
        ]
    )
    log = logging.getLogger("llamaweb")

    # Check remote mode config
    cfg = Config()
    log.info("Remote mode: %s, URL: %s", cfg.is_remote_mode, cfg.remote_url)

    # Parse command line arguments early so we can set app config
    parser = argparse.ArgumentParser(description="llama.cpp web frontend")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Flask listen port (default 5000)")
    args = parser.parse_args()

    # Create Flask app
    app = Flask(__name__)
    
    # Set app config for template variables
    app.config["SERVER_HOST"] = args.host if args.host != "0.0.0.0" else "127.0.0.1"
    app.config["FLASK_PORT"] = args.port
    app.config["CACHE_VERSION"] = "1"

    # Register blueprints
    app.register_blueprint(chat_bp)
    app.register_blueprint(downloads_bp)
    app.register_blueprint(hf_bp)
    app.register_blueprint(lifecycle_bp)
    app.register_blueprint(models_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(system_bp)

    # Load models cache (skip in remote mode)
    if not cfg.is_remote_mode:
        log.info("Loading models cache...")
        load_models_cache()
    else:
        log.info("Remote mode: skipping local models cache")

    # Initialize and start the export scheduler
    scheduler = init_scheduler(app, interval=60)
    atexit.register(scheduler.stop)

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

    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
