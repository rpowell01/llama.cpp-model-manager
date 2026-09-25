#!/usr/bin/env python3
"""
llama.cpp web frontend - simplified entry point
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
from pathlib import Path

from flask import Flask

# Import configuration
from config import BASE_DIR, Config, WSL

# Import models functions
from models import load_models_cache

# Import route blueprints
from routes.chat import chat_bp
from routes.downloads import downloads_bp
from routes.huggingface import hf_bp
from routes.lifecycle import lifecycle_bp
from routes.models import models_bp
from routes.status import status_bp
from routes.system import system_bp


def _get_wsl_ip() -> str | None:
    """Get the WSL IP address for display purposes."""
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
                        # Skip loopback and docker interfaces
                        if not ip.startswith("127.") and not ip.startswith("172.17."):
                            return ip
        
        # Method 2: Fallback to hostname resolution
        hostname = socket.gethostname()
        ips = socket.gethostbyname_ex(hostname)[2]
        for ip in ips:
            if ip.startswith(("172.", "192.168.", "10.")) and not ip.startswith("127."):
                return ip
        
        # Method 3: Fallback to socket connection
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return None


def main() -> None:
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(BASE_DIR / "webapp.log"),
            logging.StreamHandler(sys.stderr)
        ]
    )
    log = logging.getLogger("llamaweb")

    # Check remote mode config
    cfg = Config()
    log.info("Remote mode: %s, URL: %s", cfg.is_remote_mode, cfg.remote_url)

    # Create Flask app
    app = Flask(__name__)

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

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="llama.cpp web frontend")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Flask listen port (default 5000)")
    args = parser.parse_args()

    log.info("Starting web UI on http://%s:%d from %s", args.host, args.port, BASE_DIR)
    
    # Show WSL-specific connection info
    if WSL:
        wsl_ip = _get_wsl_ip()
        if wsl_ip:
            log.info("WSL detected: Access the web UI at http://%s:%d (WSL IP) or http://127.0.0.1:%d (via port forwarding)", 
                     wsl_ip, args.port, args.port)
            log.info("Note: The Windows IP shown by Flask below is NOT accessible in WSL2 default networking mode.")
    
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
