"""Unit tests for automatic metrics export in exporter.py.

The exporter now fetches metrics via the shared ``metrics._fetch_metrics_from_model``
function (the same one the metrics tooltip uses) instead of duplicating fetch and
parse logic. CSV writes remain thread-/process-safe via ``_write_lock`` and fcntl
flock, so the scheduled export can run concurrently with the on-demand tooltip
without conflicting.
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add project root to path (matches existing test conventions).
sys.path.insert(0, str(Path(__file__).parent.parent))

from exporter import (  # noqa: E402
    _append_row_to_csv,
    build_metrics_row,
    export_metrics_for_model,
)

META = {
    "port": 8080,
    "threads": 8,
    "threads_batch": 4,
    "gpu_layers": 30,
    "ctx_size": 4096,
    "batch_size": 1,
    "ubatch_size": 1,
    "flash_attn": "on",
    "numa": "distribute",
    "numa_value": "distribute",
    "parallel": "1",
    "rope_scaling_type": "none",
    "cache_type_k": "f16",
    "cache_type_v": "f16",
    "spec_type": "none",
    "spec_draft_n_max": 1,
    "spec_draft_n_min": 1,
}

METRICS = {
    "llamacpp:prompt_seconds_total": 10.5,
    "llamacpp:prompt_tokens_total": 1000,
    "llamacpp:tokens_predicted_seconds_total": 20.3,
    "llamacpp:tokens_predicted_total": 2000,
    "llamacpp:spec_decode_num_draft_tokens_total": 500,
    "llamacpp:spec_decode_num_accepted_tokens_total": 450,
    "llamacpp:spec_decode_num_drafts_total": 100,
}


class TestBuildMetricsRow:
    """Tests for build_metrics_row using the shared metrics fetch."""

    def test_builds_row_from_shared_fetch(self):
        with patch("exporter.check_port_active", return_value=True), patch(
            "exporter._fetch_metrics_from_model", return_value=dict(METRICS)
        ):
            row = build_metrics_row("test-model", dict(META))

        assert row["model"] == "test-model"
        assert row["port"] == 8080
        # Prompt metrics.
        assert row["prompt_seconds_total"] == 10.5
        assert row["prompt_tokens_total"] == 1000
        assert row["prompt_eval_ms_per_token"] == 10.5  # 10.5 / 1000 * 1000
        assert row["prompt_eval_rate_tokens_per_sec"] == round(1000 / 10.5, 1)
        # Generation metrics.
        assert row["gen_seconds_total"] == 20.3
        assert row["gen_tokens_total"] == 2000
        assert row["gen_eval_ms_per_token"] == round(20.3 / 2000 * 1000, 1)
        assert row["gen_rate_tokens_per_sec"] == round(2000 / 20.3, 1)
        # Speculative decoding.
        assert row["spec_draft_tokens_total"] == 500
        assert row["spec_accepted_tokens_total"] == 450
        assert row["spec_drafts_total"] == 100
        assert row["spec_acceptance_rate_pct"] == 90.0  # 450 / 500 * 100
        assert row["spec_avg_draft_len"] == 5.0  # 500 / 100
        assert row["spec_est_speedup"] == 5.5  # 1 + (450 / 100)
        # Configuration carried through from meta.
        assert row["threads"] == 8
        assert row["ctx_size"] == 4096

    def test_raises_value_error_without_port(self):
        with pytest.raises(ValueError, match="no port assigned"):
            build_metrics_row("test-model", {})

    def test_raises_runtime_error_when_not_running(self):
        with patch("exporter.check_port_active", return_value=False):
            with pytest.raises(RuntimeError, match="is not running"):
                build_metrics_row("test-model", dict(META))

    def test_raises_runtime_error_when_fetch_returns_no_counters(self):
        # _fetch_metrics_from_model returns only {"pp", "tg", "pg"} on failure;
        # build_metrics_row must surface that as an error rather than writing
        # a row of zeros.
        with patch("exporter.check_port_active", return_value=True), patch(
            "exporter._fetch_metrics_from_model",
            return_value={"pp": None, "tg": None, "pg": None},
        ):
            with pytest.raises(RuntimeError, match="Failed to fetch metrics"):
                build_metrics_row("test-model", dict(META))


class TestExportMetricsForModel:
    """Tests for the top-level export_metrics_for_model entry point."""

    def test_exports_and_returns_result(self):
        fake_row = {"timestamp": "2026-01-01T00:00:00", "model": "test-model", "port": 8080}
        with patch(
            "exporter.get_models", return_value={"test-model": dict(META)}
        ), patch("exporter.build_metrics_row", return_value=fake_row), patch(
            "exporter._append_row_to_csv", return_value=Path("/tmp/x_metrics.csv")
        ) as append_mock:
            result = export_metrics_for_model("test-model")

        assert result == {
            "status": "exported",
            "csv_path": "/tmp/x_metrics.csv",
            "row": fake_row,
        }
        append_mock.assert_called_once_with("test-model", fake_row)

    def test_raises_value_error_for_unknown_model(self):
        with patch("exporter.get_models", return_value={}):
            with pytest.raises(ValueError, match="Unknown model"):
                export_metrics_for_model("no-such-model")


class TestAppendRowToCSV:
    """Tests for the thread-safe CSV append."""

    def test_writes_header_and_row(self, tmp_path, monkeypatch):
        monkeypatch.setattr("exporter.CSV_DIR", tmp_path)
        row = {"timestamp": "t", "model": "m", "port": 1}
        path = _append_row_to_csv("m", row)

        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert content.startswith("timestamp,model,port\n")
        assert content.endswith("t,m,1\n")

    def test_appends_without_rewriting_header(self, tmp_path, monkeypatch):
        monkeypatch.setattr("exporter.CSV_DIR", tmp_path)
        row = {"timestamp": "t", "model": "m", "port": 1}
        _append_row_to_csv("m", row)
        _append_row_to_csv("m", row)

        content = Path(tmp_path, "m_metrics.csv").read_text(encoding="utf-8")
        # Header written exactly once.
        assert content.count("timestamp,model,port\n") == 1
        # Two data rows appended.
        assert content.count("t,m,1\n") == 2
