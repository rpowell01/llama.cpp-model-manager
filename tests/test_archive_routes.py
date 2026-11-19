"""Tests for mnemonic-archive API endpoints."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

from flask import Flask

from webapp.routes.archive import archive_bp
from webapp.mnemonic_proxy import MnemonicProxyManager


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


def _make_app():
    app = Flask(__name__)
    app.register_blueprint(archive_bp)
    app.config["TESTING"] = True
    return app


def _create_test_archive_db(db_path: Path, conversations_data: list, pieces_data: list):
    """Create a test archive.sqlite with conversations and pieces tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create conversations table
    cursor.execute("""
        CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            message_count INTEGER,
            token_count INTEGER,
            metadata TEXT
        )
    """)

    # Create pieces table
    cursor.execute("""
        CREATE TABLE pieces (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            role TEXT,
            content TEXT,
            segment INTEGER,
            message_index INTEGER,
            created_at TEXT,
            metadata TEXT,
            embedding BLOB
        )
    """)

    # Insert conversations
    for conv in conversations_data:
        cursor.execute("""
            INSERT INTO conversations (conversation_id, created_at, updated_at, message_count, token_count, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            conv["conversation_id"],
            conv.get("created_at", "2024-01-01T00:00:00"),
            conv.get("updated_at", "2024-01-01T00:00:00"),
            conv.get("message_count", 0),
            conv.get("token_count", 0),
            conv.get("metadata", "{}"),
        ))

    # Insert pieces
    for piece in pieces_data:
        cursor.execute("""
            INSERT INTO pieces (id, conversation_id, role, content, segment, message_index, created_at, metadata, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            piece["id"],
            piece["conversation_id"],
            piece["role"],
            piece["content"],
            piece.get("segment", 1),
            piece.get("message_index", 0),
            piece.get("created_at", "2024-01-01T00:00:00"),
            piece.get("metadata", "{}"),
            piece.get("embedding", None),
        ))

    conn.commit()
    conn.close()


def _write_journal(path: Path, events: list):
    """Write journal events to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_archive_conversations_missing_model_param():
    app = _make_app()
    client = app.test_client()
    resp = client.get("/api/proxy/archive/conversations")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "Missing model parameter"


def test_archive_conversations_unknown_model():
    app = _make_app()
    client = app.test_client()
    with patch("webapp.routes.archive._get_model_config", return_value=None):
        resp = client.get("/api/proxy/archive/conversations?model=unknown-model")
    assert resp.status_code == 404
    data = resp.get_json()
    assert "Unknown model" in data["error"]


def test_archive_conversations_no_archive_db(tmp_path):
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/conversations?model=test-model")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["conversations"] == []
    assert data["count"] == 0


def test_archive_conversations_with_data(tmp_path):
    app = _make_app()
    client = app.test_client()

    # Create test archive DB
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    conversations_data = [
        {"conversation_id": "conv-1", "created_at": "2024-01-01T10:00:00", "updated_at": "2024-01-01T11:00:00", "message_count": 5, "token_count": 1000},
        {"conversation_id": "conv-2", "created_at": "2024-01-02T10:00:00", "updated_at": "2024-01-02T11:00:00", "message_count": 3, "token_count": 500},
    ]
    pieces_data = []  # Not needed for conversations endpoint
    _create_test_archive_db(archive_db, conversations_data, pieces_data)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/conversations?model=test-model")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 2
    assert len(data["conversations"]) == 2
    # Should be ordered by updated_at DESC (conv-2 first)
    assert data["conversations"][0]["conversation_id"] == "conv-2"
    assert data["conversations"][1]["conversation_id"] == "conv-1"


def test_archive_conversations_with_query_filter(tmp_path):
    app = _make_app()
    client = app.test_client()

    # Create test archive DB
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    conversations_data = [
        {"conversation_id": "conv-abc", "created_at": "2024-01-01T10:00:00", "updated_at": "2024-01-01T11:00:00", "message_count": 5, "token_count": 1000},
        {"conversation_id": "conv-xyz", "created_at": "2024-01-02T10:00:00", "updated_at": "2024-01-02T11:00:00", "message_count": 3, "token_count": 500},
    ]
    pieces_data = []
    _create_test_archive_db(archive_db, conversations_data, pieces_data)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/conversations?model=test-model&query=abc")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
    assert data["conversations"][0]["conversation_id"] == "conv-abc"


def test_archive_journal_missing_model_param():
    app = _make_app()
    client = app.test_client()
    resp = client.get("/api/proxy/archive/journal")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "Missing model parameter"


def test_archive_journal_no_journal_file(tmp_path):
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/journal?model=test-model")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["events"] == []
    assert data["count"] == 0


def test_archive_journal_with_data(tmp_path):
    app = _make_app()
    client = app.test_client()

    # Create test journal file
    journal_path = tmp_path / "data" / "model" / "journal.jsonl"
    events = [
        {"event": "request", "conv": "conv-1", "prompt_tokens": 100},
        {"event": "recall", "conv": "conv-1"},
        {"event": "request", "conv": "conv-2", "prompt_tokens": 200},
    ]
    _write_journal(journal_path, events)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/journal?model=test-model")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 3
    assert data["total"] == 3
    # Most recent first (reversed)
    assert data["events"][0]["event"] == "request"
    assert data["events"][0]["conv"] == "conv-2"
    assert data["events"][2]["event"] == "request"
    assert data["events"][2]["conv"] == "conv-1"


def test_archive_journal_with_conversation_filter(tmp_path):
    app = _make_app()
    client = app.test_client()

    journal_path = tmp_path / "data" / "model" / "journal.jsonl"
    events = [
        {"event": "request", "conv": "conv-1", "prompt_tokens": 100},
        {"event": "recall", "conv": "conv-1"},
        {"event": "request", "conv": "conv-2", "prompt_tokens": 200},
    ]
    _write_journal(journal_path, events)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/journal?model=test-model&conversation_id=conv-1")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 2
    assert all(e["conv"] == "conv-1" for e in data["events"])


def test_archive_journal_with_event_filter(tmp_path):
    app = _make_app()
    client = app.test_client()

    journal_path = tmp_path / "data" / "model" / "journal.jsonl"
    events = [
        {"event": "request", "conv": "conv-1", "prompt_tokens": 100},
        {"event": "recall", "conv": "conv-1"},
        {"event": "request", "conv": "conv-2", "prompt_tokens": 200},
    ]
    _write_journal(journal_path, events)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/journal?model=test-model&event=recall")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
    assert data["events"][0]["event"] == "recall"


def test_archive_recall_missing_model_param():
    app = _make_app()
    client = app.test_client()
    resp = client.get("/api/proxy/archive/recall")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "Missing model parameter"


def test_archive_recall_no_archive_db(tmp_path):
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pieces"] == []
    assert data["total"] == 0


def test_archive_recall_with_id(tmp_path):
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    conversations_data = []
    pieces_data = [
        {"id": "piece-1", "conversation_id": "conv-1", "role": "user", "content": "Hello", "segment": 1, "message_index": 0, "created_at": "2024-01-01T10:00:00"},
        {"id": "piece-2", "conversation_id": "conv-1", "role": "assistant", "content": "Hi there", "segment": 1, "message_index": 1, "created_at": "2024-01-01T10:00:01"},
    ]
    _create_test_archive_db(archive_db, conversations_data, pieces_data)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&id=piece-1")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["pieces"][0]["id"] == "piece-1"
    assert data["pieces"][0]["content"] == "Hello"


def test_archive_recall_with_query(tmp_path):
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    conversations_data = []
    pieces_data = [
        {"id": "piece-1", "conversation_id": "conv-1", "role": "user", "content": "Hello world", "segment": 1, "message_index": 0, "created_at": "2024-01-01T10:00:00"},
        {"id": "piece-2", "conversation_id": "conv-1", "role": "assistant", "content": "Goodbye world", "segment": 1, "message_index": 1, "created_at": "2024-01-01T10:00:01"},
        {"id": "piece-3", "conversation_id": "conv-2", "role": "user", "content": "Foo bar", "segment": 1, "message_index": 0, "created_at": "2024-01-01T11:00:00"},
    ]
    _create_test_archive_db(archive_db, conversations_data, pieces_data)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&query=Hello")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["pieces"][0]["content"] == "Hello world"


def test_archive_recall_invalid_mode():
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return Path("/tmp/data/model")

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&mode=invalid")

    assert resp.status_code == 400
    data = resp.get_json()
    assert "Invalid mode" in data["error"]


def test_archive_recall_invalid_order():
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return Path("/tmp/data/model")

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&order=invalid")

    assert resp.status_code == 400
    data = resp.get_json()
    assert "Invalid order" in data["error"]


def test_archive_recall_invalid_role():
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return Path("/tmp/data/model")

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&role=invalid")

    assert resp.status_code == 400
    data = resp.get_json()
    assert "Invalid role" in data["error"]


def test_archive_recall_invalid_from_to():
    """Test that 'from' > 'to' returns 400."""
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return Path("/tmp/data/model")

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&from=10&to=5")

    assert resp.status_code == 400
    data = resp.get_json()
    assert "from" in data["error"].lower() or "must be <=" in data["error"]


def test_archive_conversations_negative_limit():
    """Test that negative limit is clamped to default."""
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return Path("/tmp/data/model")

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/conversations?model=test-model&limit=-5")

    # Should not error, should use default
    assert resp.status_code == 200


def test_archive_conversations_excessive_limit():
    """Test that excessive limit is clamped to max."""
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return Path("/tmp/data/model")

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/conversations?model=test-model&limit=5000")

    # Should not error, should use max
    assert resp.status_code == 200


def test_archive_journal_negative_offset():
    """Test that negative offset is clamped to 0."""
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return Path("/tmp/data/model")

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/journal?model=test-model&offset=-10")

    # Should not error, should use default 0
    assert resp.status_code == 200


def test_archive_recall_negative_seg():
    """Test that negative seg is treated as None."""
    app = _make_app()
    client = app.test_client()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return Path("/tmp/data/model")

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&seg=-5")

    # Should not error
    assert resp.status_code == 200


def test_archive_recall_mode_first(tmp_path):
    """Test mode=first returns oldest match."""
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "test_archive.sqlite"

    conversations_data = []
    pieces_data = [
        {"id": "piece-1", "conversation_id": "conv-1", "role": "user", "content": "First message", "segment": 1, "message_index": 0, "created_at": "2024-01-01T10:00:00"},
        {"id": "piece-2", "conversation_id": "conv-1", "role": "assistant", "content": "Second message", "segment": 1, "message_index": 1, "created_at": "2024-01-01T10:00:01"},
        {"id": "piece-3", "conversation_id": "conv-1", "role": "user", "content": "Third message", "segment": 1, "message_index": 2, "created_at": "2024-01-01T10:00:02"},
    ]
    _create_test_archive_db(archive_db, conversations_data, pieces_data)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            with patch("webapp.routes.archive._find_archive_db", return_value=archive_db):
                resp = client.get("/api/proxy/archive/recall?model=test-model&mode=first")

    assert resp.status_code == 200
    data = resp.get_json()
    # mode=first should return the oldest (first) message
    assert data["total"] == 1
    assert data["pieces"][0]["content"] == "First message"


def test_archive_recall_like_wildcard_escape(tmp_path):
    """Test that LIKE wildcards in query are escaped."""
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "test_archive.sqlite"

    conversations_data = []
    pieces_data = [
        {"id": "piece-1", "conversation_id": "conv-1", "role": "user", "content": "100% complete", "segment": 1, "message_index": 0, "created_at": "2024-01-01T10:00:00"},
        {"id": "piece-2", "conversation_id": "conv-1", "role": "assistant", "content": "50 percent done", "segment": 1, "message_index": 1, "created_at": "2024-01-01T10:00:01"},
    ]
    _create_test_archive_db(archive_db, conversations_data, pieces_data)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            with patch("webapp.routes.archive._find_archive_db", return_value=archive_db):
                # Query with % should be escaped and match literally
                resp = client.get("/api/proxy/archive/recall?model=test-model&query=100%")

    assert resp.status_code == 200
    data = resp.get_json()
    # Should match "100% complete" literally, not as wildcard
    assert data["total"] == 1
    assert data["pieces"][0]["content"] == "100% complete"


def test_archive_recall_remote_mode():
    """Test that remote mode returns 400 with proper error."""
    app = _make_app()
    client = app.test_client()

    with patch.dict("os.environ", {"REMOTE_MODE": "true"}):
        resp = client.get("/api/proxy/archive/conversations?model=test-model")

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["code"] == "REMOTE_MODE_INVALID"
    assert "remote mode" in data["error"].lower()


def test_archive_journal_remote_mode():
    """Test that remote mode returns 400 for journal endpoint."""
    app = _make_app()
    client = app.test_client()

    with patch.dict("os.environ", {"REMOTE_MODE": "true"}):
        resp = client.get("/api/proxy/archive/journal?model=test-model")

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["code"] == "REMOTE_MODE_INVALID"


def test_archive_recall_remote_mode():
    """Test that remote mode returns 400 for recall endpoint."""
    app = _make_app()
    client = app.test_client()

    with patch.dict("os.environ", {"REMOTE_MODE": "true"}):
        resp = client.get("/api/proxy/archive/recall?model=test-model")

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["code"] == "REMOTE_MODE_INVALID"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])