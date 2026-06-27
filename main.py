#!/usr/bin/env python3
"""
llama.cpp web frontend - simplified entry point
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from flask import Flask

# Import configuration
from config import BASE_DIR

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

    # Load models cache
    log.info("Loading models cache...")
    load_models_cache()

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="llama.cpp web frontend")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Flask listen port (default 5000)")
    args = parser.parse_args()

    log.info("Starting web UI on http://%s:%d from %s", args.host, args.port, BASE_DIR)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
