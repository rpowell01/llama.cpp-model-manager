#!/usr/bin/env python3
"""
Background scheduler service that periodically exports metrics for all running models.

This module provides a Scheduler class that runs as a background thread, periodically
checking for running models and calling the /api/export-metrics endpoint for each one.

Features:
- Configurable interval (default 60 seconds, minimum 1 second)
- Thread-safe operation
- Graceful error handling (logs but continues)
- Proper cleanup on shutdown
- Integration with Flask application context
"""

import logging
import threading
from typing import Optional

import requests

from models import get_models, load_models_cache
from routes.status import get_status_registry
from routes.models import check_port_active

log = logging.getLogger("llamaweb.scheduler")


class Scheduler:
    """
    Background scheduler that periodically exports metrics for running models.

    The scheduler runs as a daemon thread and periodically:
    1. Gets the list of model configurations
    2. Checks which models are currently running (port active)
    3. Calls /api/export-metrics for each running model
    4. Logs successes and failures

    Usage:
        scheduler = Scheduler(app, interval=60)
        scheduler.start()
        # ... later ...
        scheduler.stop()
    """

    def __init__(self, app, interval: int = 60):
        """
        Initialize the scheduler.

        Args:
            app: Flask application instance
            interval: Export interval in seconds (default 60, minimum 1)
        """
        self.app = app
        # Validate interval - enforce minimum of 1 second to prevent busy loop
        if interval < 1:
            log.warning("Interval %d is less than 1 second, defaulting to 60", interval)
            interval = 60
        self.interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        """Start the scheduler background thread."""
        with self._lock:
            if self._running:
                log.warning("Scheduler is already running")
                return

            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self._running = True
            log.info("Scheduler started with %d-second interval", self.interval)

    def stop(self) -> None:
        """Stop the scheduler background thread."""
        with self._lock:
            if not self._running:
                return

            self._stop_event.set()
            # Wait for thread to finish with a short fixed timeout (daemon thread will be terminated at exit)
            if self._thread is not None:
                self._thread.join(timeout=10)
            self._running = False
            log.info("Scheduler stopped")

    def _run(self) -> None:
        """Main scheduler loop running in a background thread."""
        log.info("Scheduler thread started")

        try:
            # Initialize models cache within the app context
            with self.app.app_context():
                load_models_cache()

            # Defer first cycle to allow Flask server to start accepting connections
            # Wait for a short initial delay before first export
            if self._stop_event.wait(timeout=5):
                log.info("Scheduler stopped before first cycle")
                return

            while not self._stop_event.is_set():
                try:
                    self._export_cycle()
                except Exception as e:
                    log.error("Error in scheduler export cycle: %s", e, exc_info=True)

                # Wait for next cycle, but check stop_event periodically
                self._stop_event.wait(timeout=self.interval)

        finally:
            # Ensure _running flag is reset even if thread exits unexpectedly
            with self._lock:
                self._running = False
            log.info("Scheduler thread exiting")

    def _export_cycle(self) -> None:
        """One cycle: check running models and export metrics for each."""
        # Use Flask app context
        with self.app.app_context():
            # Get model registry
            registry = get_status_registry()

            # Get all models
            if registry.is_remote_mode:
                remote_models = registry.list_models()
                model_names = [m.name for m in remote_models]
            else:
                models = get_models()
                model_names = list(models.keys())

            if not model_names:
                return

            # Check which models are running
            running_models = self._get_running_models(registry, model_names)

            # Export metrics for each running model
            for model_name in running_models:
                if self._stop_event.is_set():
                    break
                try:
                    self._export_model_metrics(model_name)
                except Exception as e:
                    log.error(
                        "Failed to export metrics for model '%s': %s",
                        model_name, e,
                        exc_info=True,
                    )

    def _get_running_models(self, registry, model_names: list[str]) -> list[str]:
        """Check which models from the list are currently running."""
        running = []

        for model_name in model_names:
            if self._stop_event.is_set():
                break

            # Get model metadata - use registry for both local and remote modes
            model = registry.get_model(model_name)
            if model is None:
                # Fallback to local cache if registry doesn't have it
                models = get_models()
                if model_name not in models:
                    continue
                meta = models[model_name]
                port = meta.get("port", 0)
            else:
                # Use port from the Model object (works for both local and remote)
                port = getattr(model, 'port', 0)

            if not port:
                continue

            # Check if port is active
            if check_port_active(port):
                running.append(model_name)

        return running

    def _export_model_metrics(self, model_name: str) -> dict:
        """
        Export metrics for a single model by calling the /api/export-metrics endpoint.

        Args:
            model_name: Name of the model to export metrics for

        Returns:
            Response dict from the export API
        """
        # Use Flask app config for host and port (defaults to 127.0.0.1 and configured port)
        host = self.app.config.get("SERVER_HOST", "127.0.0.1")
        port = self.app.config.get("FLASK_PORT", 5000)
        url = f"http://{host}:{port}/api/export-metrics"

        # Call the /api/export-metrics endpoint
        try:
            resp = requests.post(
                url,
                json={"model": model_name},
                timeout=10,
            )

            if resp.status_code == 200:
                result = resp.json()
                log.debug(
                    "Exported metrics for model '%s': %s",
                    model_name, result.get('status')
                )
                return result
            else:
                log.warning(
                    "Export metrics API returned %d for model '%s': %s",
                    resp.status_code, model_name, resp.text
                )
                return {"error": f"API returned {resp.status_code}"}

        except requests.exceptions.Timeout:
            log.warning("Timeout exporting metrics for model '%s'", model_name)
            raise
        except requests.exceptions.ConnectionError:
            log.warning("Connection error exporting metrics for model '%s'", model_name)
            raise
        except Exception as e:
            log.error("Unexpected error exporting metrics for '%s': %s", model_name, e)
            raise


# Convenience function to create and start a scheduler with a Flask app
def init_scheduler(app, interval: int = 60) -> Scheduler:
    """
    Create and start a scheduler for the given Flask application.

    Args:
        app: Flask application instance
        interval: Export interval in seconds (default 60, minimum 1)

    Returns:
        Scheduler instance
    """
    scheduler = Scheduler(app, interval=interval)
    scheduler.start()
    return scheduler