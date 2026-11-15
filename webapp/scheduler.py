#!/usr/bin/env python3
"""
Background scheduler service that periodically exports metrics for all running models.

This module provides a Scheduler class that runs as a background thread, periodically
checking for running models and exporting metrics for each one via the exporter module.

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

from webapp.models import get_models, load_models_cache
from webapp.routes.status import get_status_registry
from webapp.processes import check_port_active, _check_model_ready

from webapp.config import (
    METRICS_EXPORT_ENABLED,
    METRICS_EXPORT_INTERVAL_SECONDS,
    METRICS_EXPORT_MODELS,
)

log = logging.getLogger("llamaweb.scheduler")


class Scheduler:
    """
    Background scheduler that periodically exports metrics for running models.

    The scheduler runs as a daemon thread and periodically:
    1. Gets the list of model configurations
    2. Checks which models are currently running (port active)
    3. Exports metrics for each running model via the exporter module
    4. Logs successes and failures

    Usage:
        scheduler = Scheduler(app, interval=60)
        scheduler.start()
        # ... later ...
        scheduler.stop()
    """

    def __init__(self, app, interval: Optional[int] = None):
        """
        Initialize the scheduler.

        Args:
            app: Flask application instance
            interval: Export interval in seconds. Defaults to the
                METRICS_EXPORT_INTERVAL_SECONDS config value (default 60).
                Minimum 1 second.
        """
        self.app = app
        # Whether automatic metrics export is enabled at all
        self.enabled = METRICS_EXPORT_ENABLED
        # Resolve interval: explicit argument > config value > 60 default
        if interval is None:
            interval = METRICS_EXPORT_INTERVAL_SECONDS
        # Validate interval - enforce 1-3600 second range
        if interval < 1:
            log.warning("Interval %d is less than 1 second, defaulting to 60", interval)
            interval = 60
        elif interval > 3600:
            log.warning("Interval %d is greater than 3600 seconds, clamping to 3600", interval)
            interval = 3600
        self.interval = interval
        # Optional allowlist of models to export (empty = all running models)
        self.export_models = set(METRICS_EXPORT_MODELS)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        """Start the scheduler background thread."""
        if not self.enabled:
            log.info("Metrics export is disabled; scheduler will not run")
            return

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
        """One cycle: check running models and export metrics for each.

        Returns immediately when automatic export is disabled, so toggling
        ``enabled`` at runtime stops further exports without stopping the
        thread itself.
        """
        if not self.enabled:
            return
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
            # Respect the configured model allowlist, if any
            if self.export_models:
                running_models = [m for m in running_models if m in self.export_models]

            for model_name in running_models:
                if self._stop_event.is_set():
                    break
                try:
                    self._export_model_metrics(model_name)
                except Exception as e:
                    # A model can go down between the port-activity check and the
                    # metrics fetch (race condition), or its /metrics endpoint can
                    # be briefly unresponsive. These are expected operational events,
                    # not bugs — the scheduler retries on the next cycle. Log at
                    # WARNING without a traceback to avoid noisy console spam.
                    log.warning(
                        "Failed to export metrics for model '%s': %s",
                        model_name, e,
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

            # In remote mode, models are managed by the remote server. A model
            # returned by the registry is loaded/running there; there is no
            # local port to probe. In local mode, require a port and verify it
            # is active before considering the model running.
            if registry.is_remote_mode:
                if model is not None:
                    running.append(model_name)
                continue

            if not port:
                continue

            # Check if port is active AND the specific model is running on it
            if _check_model_ready(port, model_name):
                running.append(model_name)

        return running

    def _export_model_metrics(self, model_name: str) -> dict:
        """
        Export metrics for a single model using the exporter module.

        Calls :func:`exporter.export_metrics_for_model` in-process instead of
        making an HTTP round-trip to ``/api/export-metrics``. This keeps the
        periodic export independent of the UI/Flask request stack and avoids
        the overhead of an HTTP request per model per cycle.

        Args:
            model_name: Name of the model to export metrics for.

        Returns:
            The result dict returned by :func:`exporter.export_metrics_for_model`.

        Raises:
            Any exception raised by the exporter (for example when the model
            is not running or its metrics endpoint is unreachable). These are
            caught and logged by :meth:`_export_cycle`.
        """
        # Imported locally to keep the exporter module optional at import time
        # and to avoid any import-order coupling with the Flask app.
        from webapp.exporter import export_metrics_for_model

        result = export_metrics_for_model(model_name)
        log.debug(
            "Exported metrics for model '%s': %s",
            model_name,
            result.get("status"),
        )
        return result

    def get_config(self) -> dict:
        """Return the current export configuration as a plain dict."""
        with self._lock:
            enabled = self.enabled
            interval = self.interval
            models = sorted(self.export_models)
        return {
            "enabled": enabled,
            "interval_seconds": interval,
            "models": models,
        }

    def update_config(
        self,
        enabled: Optional[bool] = None,
        interval_seconds: Optional[int] = None,
        models: Optional[list[str]] = None,
    ) -> dict:
        """
        Update the live export configuration.

        Changes to ``interval_seconds`` take effect on the next scheduler
        cycle because the wait timeout is re-read each loop. Changes to the
        model allowlist take effect on the next cycle as well.

        The ``enabled`` flag is persisted immediately and a running scheduler
        stops exporting as soon as it is toggled off (the background thread
        becomes a no-op). Starting a scheduler that was disabled at
        initialization requires restarting the application.

        All mutations are performed under ``self._lock`` so concurrent reads
        from the scheduler thread see a consistent state.

        Args:
            enabled: Whether automatic export is enabled.
            interval_seconds: Export interval in seconds (1-3600).
            models: Allowlist of model names to export (empty = all running).

        Returns:
            The updated configuration dict.

        Raises:
            ValueError: If interval_seconds is not a valid integer, or if
                models is not a list of model names.
        """
        if models is not None and not isinstance(models, list):
            raise ValueError("models must be a list of model names")

        with self._lock:
            if enabled is not None:
                self.enabled = bool(enabled)

            if interval_seconds is not None:
                try:
                    interval = int(interval_seconds)
                except (TypeError, ValueError):
                    raise ValueError("interval_seconds must be an integer")
                if interval < 1:
                    interval = 1
                elif interval > 3600:
                    interval = 3600
                self.interval = interval

            if models is not None:
                self.export_models = set(m.strip() for m in models if m.strip())

        return self.get_config()


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