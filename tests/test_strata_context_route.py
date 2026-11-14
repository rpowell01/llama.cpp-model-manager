import json
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from routes.lifecycle import lifecycle_bp


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


def _make_app():
    app = Flask(__name__)
    app.register_blueprint(lifecycle_bp)
    app.config["TESTING"] = True
    return app


def _write_journal(path: Path, conv_id: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "event": "request",
            "conv": conv_id,
            "prompt_tokens": 123,
            "est_tokens": 120,
            "virtual_tokens": 999,
            "reused": 50,
            "seg": 2,
            "mode": "chat",
        },
        {"event": "recall", "conv": conv_id},
        {"event": "recall_use", "conv": conv_id},
    ]
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_strata_context_falls_back_to_journal_when_conversations_api_unavailable(tmp_path):
    app_cwd_dir = tmp_path / "worktree"
    proxy_cwd_dir = tmp_path / "repo"
    wrong_journal = app_cwd_dir / "data" / "model" / "journal.jsonl"
    real_journal = proxy_cwd_dir / "data" / "model" / "journal.jsonl"
    wrong_journal.parent.mkdir(parents=True, exist_ok=True)
    wrong_journal.write_text("", encoding="utf-8")
    _write_journal(real_journal, "ca4d535424e56")

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return app_cwd_dir / "data" / "model"

    def fake_get(url, timeout=10, headers=None):
        return FakeResp(404, {})

    app = _make_app()
    client = app.test_client()
    orig_resolve = Path.resolve

    def resolve_side_effect(self):
        if str(self) == "/proc/12345/cwd":
            return proxy_cwd_dir
        return orig_resolve(self)

    with patch("routes.lifecycle.get_proxy_manager", return_value=PM()):
        with patch.object(Path, "resolve", autospec=True, side_effect=resolve_side_effect):
            with patch("requests.get", side_effect=fake_get):
                resp = client.get("/api/proxy/strata-context/test-model")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status_code"] == 200
    assert data["strata_context"]["conversation_id"] == "ca4d535424e56"
    assert data["strata_context"]["active_tokens"] == 123
    assert data["strata_context"]["recalls"] == 2


def test_strata_context_uses_candidate_journal_for_explicit_conversation(tmp_path):
    proxy_cwd_dir = tmp_path / "repo"
    real_journal = proxy_cwd_dir / "data" / "model" / "journal.jsonl"
    _write_journal(real_journal, "conv-xyz")

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 222

        def _resolve_data_dir(self):
            return tmp_path / "wrong" / "data" / "model"

    def fake_get(url, timeout=10, headers=None):
        if "/v1/strata/conversations/conv-xyz" in url:
            return FakeResp(200, {
                "archived": 1,
                "archived_tokens": 10,
                "masked": 0,
                "masked_tokens": 0,
                "segments": 3,
                "segment_list": [],
            })
        return FakeResp(404, {})

    app = _make_app()
    client = app.test_client()
    orig_resolve = Path.resolve

    def resolve_side_effect(self):
        if str(self) == "/proc/222/cwd":
            return proxy_cwd_dir
        return orig_resolve(self)

    with patch("routes.lifecycle.get_proxy_manager", return_value=PM()):
        with patch.object(Path, "resolve", autospec=True, side_effect=resolve_side_effect):
            with patch("requests.get", side_effect=fake_get):
                resp = client.get("/api/proxy/strata-context/test-model?conversation_id=conv-xyz")

    assert resp.status_code == 200
    ctx = resp.get_json()["strata_context"]
    assert ctx["conversation_id"] == "conv-xyz"
    assert ctx["source"] == "live+store"
    assert ctx["archived"] == 1
    assert ctx["segments"] == 3
    assert ctx["active_tokens"] == 123
    assert ctx["recalls"] == 2
