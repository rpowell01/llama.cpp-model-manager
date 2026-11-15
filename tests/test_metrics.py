"""Unit tests for shared metrics fetching/parsing in metrics.py.

These tests cover the shared metrics logic that both the metrics tooltip
(status/_fetch_metrics_from_model path) and the automatic metrics exporter now
use, so that fetching and parsing happen in one place instead of being
duplicated.
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

# Add project root to path (matches existing test conventions).
sys.path.insert(0, str(Path(__file__).parent.parent))

from webapp.metrics import (  # noqa: E402
    _fetch_metrics_from_model,
    _metrics_snapshots,
    _parse_metrics_text,
    fetch_metrics_snapshot,
)

METRICS_TEXT = """\
# HELP llamacpp:prompt_seconds_total Prompt processing seconds
# TYPE llamacpp:prompt_seconds_total counter
llamacpp:prompt_seconds_total 10.5
llamacpp:prompt_tokens_total 1000
# TYPE llamacpp:tokens_predicted_seconds_total counter
llamacpp:tokens_predicted_seconds_total 20.3
llamacpp:tokens_predicted_total 2000
llamacpp:spec_decode_num_draft_tokens_total 500
llamacpp:spec_decode_num_accepted_tokens_total 450
llamacpp:spec_decode_num_drafts_total 100
llamacpp_generated_tokens_total 2000
"""


class TestParseMetricsText:
    """Tests for the shared Prometheus text parser."""

    def test_parses_simple_metric_lines(self):
        text = "llamacpp:prompt_seconds_total 10.5\nllamacpp:prompt_tokens_total 1000\n"
        values = _parse_metrics_text(text)
        assert values["llamacpp:prompt_seconds_total"] == 10.5
        assert values["llamacpp:prompt_tokens_total"] == 1000.0

    def test_skips_comments_and_blank_lines(self):
        text = "# a comment\n\n# TYPE x counter\nllamacpp:prompt_seconds_total 5\n"
        values = _parse_metrics_text(text)
        assert values == {"llamacpp:prompt_seconds_total": 5.0}

    def test_strips_label_suffix(self):
        text = 'llamacpp:prompt_seconds_total{instance="x",port="8080"} 10.5\n'
        values = _parse_metrics_text(text)
        assert values["llamacpp:prompt_seconds_total"] == 10.5

    def test_skips_unparseable_lines(self):
        text = "not a metric\nllamacpp:prompt_seconds_total 10.5\nabc def\n123\n"
        values = _parse_metrics_text(text)
        assert values == {"llamacpp:prompt_seconds_total": 10.5}

    def test_returns_empty_dict_for_empty_input(self):
        assert _parse_metrics_text("") == {}
        assert _parse_metrics_text("# only comments\n\n") == {}


class TestFetchMetricsFromModel:
    """Tests for the shared metrics fetch used by tooltip and exporter."""

    def _mock_response(self, text, status_code=200):
        resp = Mock()
        resp.status_code = status_code
        resp.text = text
        return resp

    def test_returns_derived_and_raw_counters_on_success(self):
        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get", return_value=self._mock_response(METRICS_TEXT)
        ):
            result = _fetch_metrics_from_model(8080)

        # Derived metrics (tooltip/status path). Values are rounded in the
        # source, so round the same way in the assertion.
        assert result["pp"] == 10500.0  # 10.5s * 1000
        assert result["tg"] == round(2000 / 20.3, 2)
        assert result["pg"] == 3000  # 1000 + 2000

        # Raw counters exposed for the exporter to build full CSV rows.
        assert result["llamacpp:prompt_seconds_total"] == 10.5
        assert result["llamacpp:prompt_tokens_total"] == 1000.0
        assert result["llamacpp:tokens_predicted_total"] == 2000.0
        assert result["llamacpp:spec_decode_num_draft_tokens_total"] == 500.0

    def test_returns_defaults_on_non_ok_status(self):
        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get", return_value=self._mock_response("x", status_code=500)
        ):
            result = _fetch_metrics_from_model(8080)

        assert result == {"pp": None, "tg": None, "pg": None}
        # No raw counters present on failure.
        assert "llamacpp:prompt_seconds_total" not in result

    def test_returns_defaults_on_fetch_exception(self):
        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get",
            side_effect=ConnectionError("connection refused"),
        ):
            result = _fetch_metrics_from_model(8080)

        assert result == {"pp": None, "tg": None, "pg": None}


class TestFetchMetricsSnapshot:
    """Tests for the cached metrics snapshot used by the performance graph."""

    def _mock_response(self, text, status_code=200):
        resp = Mock()
        resp.status_code = status_code
        resp.text = text
        return resp

    def setup_method(self):
        _metrics_snapshots.clear()

    def teardown_method(self):
        _metrics_snapshots.clear()

    def test_returns_parsed_values_on_success(self):
        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get", return_value=self._mock_response(METRICS_TEXT)
        ):
            snapshot = fetch_metrics_snapshot(8080)

        assert snapshot.values["llamacpp:prompt_seconds_total"] == 10.5
        assert snapshot.error is None
        assert snapshot.stale is False
        assert snapshot.age_seconds is None

    def test_caches_the_successful_snapshot(self):
        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get", return_value=self._mock_response(METRICS_TEXT)
        ):
            fetch_metrics_snapshot(8080)

        assert 8080 in _metrics_snapshots

    def test_falls_back_to_cached_snapshot_when_fetch_fails(self):
        """A busy llama-server times out; the last known values are reused."""
        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get", return_value=self._mock_response(METRICS_TEXT)
        ):
            fetch_metrics_snapshot(8080)

        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get",
            side_effect=requests.exceptions.ReadTimeout("read timed out"),
        ):
            snapshot = fetch_metrics_snapshot(8080)

        assert snapshot.values["llamacpp:prompt_seconds_total"] == 10.5
        assert snapshot.stale is True
        assert snapshot.error is not None
        assert "ReadTimeout" in snapshot.error
        assert snapshot.age_seconds is not None
        assert snapshot.age_seconds >= 0

    def test_returns_empty_snapshot_when_fetch_fails_without_cache(self):
        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get",
            side_effect=requests.exceptions.ReadTimeout("read timed out"),
        ):
            snapshot = fetch_metrics_snapshot(8080)

        assert snapshot.values == {}
        assert snapshot.stale is True
        assert snapshot.error is not None

    def test_expired_cache_is_not_served(self):
        """A snapshot older than the max age must not be shown as current."""
        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get", return_value=self._mock_response(METRICS_TEXT)
        ):
            fetch_metrics_snapshot(8080)

        _metrics_snapshots[8080]["fetched_at"] -= 3600

        with patch("webapp.metrics.get_server_host", return_value="127.0.0.1"), patch(
            "webapp.metrics.requests.get",
            side_effect=requests.exceptions.ReadTimeout("read timed out"),
        ):
            snapshot = fetch_metrics_snapshot(8080)

        assert snapshot.values == {}
        assert snapshot.stale is True
