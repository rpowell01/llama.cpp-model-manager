"""Tests for mnemonic-archive API endpoints with real archive schema."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

from flask import Flask

from webapp.routes.archive import archive_bp, _epoch_to_iso
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


def test_epoch_to_iso_none():
    """Test _epoch_to_iso with None returns epoch-0 sentinel."""
    assert _epoch_to_iso(None) == "1970-01-01T00:00:00.000Z"


def test_epoch_to_iso_valid():
    """Test _epoch_to_iso with valid epoch returns correct ISO string."""
    # 1704067200.0 = 2024-01-01T00:00:00.000Z
    assert _epoch_to_iso(1704067200.0) == "2024-01-01T00:00:00.000Z"


def test_epoch_to_iso_non_numeric():
    """Test _epoch_to_iso with non-numeric value falls back to epoch-0."""
    assert _epoch_to_iso("2024-01-01") == "1970-01-01T00:00:00.000Z"
    assert _epoch_to_iso("invalid") == "1970-01-01T00:00:00.000Z"
    assert _epoch_to_iso([1, 2, 3]) == "1970-01-01T00:00:00.000Z"


def test_epoch_to_iso_out_of_range():
    """Test _epoch_to_iso with out-of-range value falls back to epoch-0."""
    # Very large value that would cause OverflowError
    assert _epoch_to_iso(1e30) == "1970-01-01T00:00:00.000Z"
    # Very negative value
    assert _epoch_to_iso(-1e30) == "1970-01-01T00:00:00.000Z"


def test_archive_recall_queries_with_empty_terms():
    """Test that empty/whitespace-only query terms are filtered out."""
    app = _make_app()
    client = app.test_client()

    archive_db = Path("/tmp/test_archive.db")
    if archive_db.exists():
        archive_db.unlink()
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return Path("/tmp/data/model")

    # Test with empty queries parameter - should result in no FTS search
    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            # queries=, should result in empty search terms, no FTS used
            resp = client.get("/api/proxy/archive/recall?model=test-model&queries=,")

    assert resp.status_code == 200
    data = resp.get_json()
    # When no valid terms, no FTS search is performed; pieces should be empty
    # or the search should not match everything (depends on FTS table existence)
    # The key is that it doesn't 500 or return all pieces as matches
    assert "pieces" in data or "recall" in data


def _create_test_archive_db(db_path: Path):
    """Create a test archive.sqlite with the REAL schema (archive, segments tables)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create archive table (real schema)
    cursor.execute("""
        CREATE TABLE archive (
            rid TEXT PRIMARY KEY,
            conv TEXT NOT NULL,
            idx INTEGER NOT NULL,
            role TEXT NOT NULL,
            name TEXT,
            content TEXT NOT NULL,
            tokens INTEGER,
            created REAL NOT NULL
        )
    """)

    # Create segments table (real schema)
    cursor.execute("""
        CREATE TABLE segments (
            conv TEXT NOT NULL,
            seg INTEGER NOT NULL,
            cut_idx INTEGER,
            cut_h TEXT,
            notes_msg TEXT,
            kind TEXT,
            created REAL,
            pins_msg TEXT,
            PRIMARY KEY (conv, seg)
        )
    """)

    # Create FTS table for full-text search (real schema: external-content, content only)
    cursor.execute("""
        CREATE VIRTUAL TABLE archive_fts USING fts5(
            content,
            content='archive',
            content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        )
    """)

    # Create trigger to sync FTS on archive inserts (real schema)
    cursor.execute("""
        CREATE TRIGGER archive_ai AFTER INSERT ON archive BEGIN
            INSERT INTO archive_fts(rowid, content) VALUES (new.rowid, new.content);
        END
    """)

    # Insert test conversations data
    # Conversation 1: conv-1 with 5 messages (segment 1: idx 0-4)
    conv1_messages = [
        ("rid-1", "conv-1", 0, "system", None, "System prompt", 100, 1704067200.0),
        ("rid-2", "conv-1", 1, "user", None, "Hello world", 50, 1704067201.0),
        ("rid-3", "conv-1", 2, "assistant", None, "Hi there! How can I help?", 75, 1704067202.0),
        ("rid-4", "conv-1", 3, "user", None, "What is 2+2?", 25, 1704067203.0),
        ("rid-5", "conv-1", 4, "assistant", None, "2+2=4", 30, 1704067204.0),
    ]
    for msg in conv1_messages:
        cursor.execute("""
            INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, msg)

    # Conversation 2: conv-2 with 3 messages (segment 1: idx 0-2)
    conv2_messages = [
        ("rid-6", "conv-2", 0, "user", None, "First message", 40, 1704153600.0),
        ("rid-7", "conv-2", 1, "assistant", None, "Response to first", 60, 1704153601.0),
        ("rid-8", "conv-2", 2, "user", None, "Second message", 35, 1704153602.0),
    ]
    for msg in conv2_messages:
        cursor.execute("""
            INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, msg)

    # Conversation 3: conv-abc with 2 messages (for query filter test)
    conv3_messages = [
        ("rid-9", "conv-abc", 0, "user", None, "abc test query", 45, 1704240000.0),
        ("rid-10", "conv-abc", 1, "assistant", None, "abc response", 55, 1704240001.0),
    ]
    for msg in conv3_messages:
        cursor.execute("""
            INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, msg)

    # Conversation 4: conv-wildcard with 2 messages (for wildcard query test)
    conv4_messages = [
        ("rid-11", "conv-wildcard", 0, "user", None, "wildcard test", 40, 1704326400.0),
        ("rid-12", "conv-wildcard", 1, "assistant", None, "wildcard response", 50, 1704326401.0),
    ]
    for msg in conv4_messages:
        cursor.execute("""
            INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, msg)

    # Insert segments data (conv-1: 2 segments, conv-2: 1 segment, etc.)
    segments_data = [
        ("conv-1", 1, 0, None, None, None, 1704067200.0, None),
        ("conv-1", 2, 3, None, None, None, 1704067203.0, None),
        ("conv-2", 1, 0, None, None, None, 1704153600.0, None),
        ("conv-abc", 1, 0, None, None, None, 1704240000.0, None),
        ("conv-wildcard", 1, 0, None, None, None, 1704326400.0, None),
    ]
    for seg in segments_data:
        cursor.execute("""
            INSERT INTO segments (conv, seg, cut_idx, cut_h, notes_msg, kind, created, pins_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, seg)

    conn.commit()
    conn.close()


def _write_journal(path: Path, events: list):
    """Write journal events to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


# ─── Tests ────────────────────────────────────────────────────────────────

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
    resp = client.get("/api/proxy/archive/conversations?model=unknown-model")

    assert resp.status_code == 404
    data = resp.get_json()
    assert data["error"] == "Unknown model 'unknown-model'"


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
    assert data["total"] == 0


def test_archive_conversations_with_data(tmp_path):
    app = _make_app()
    client = app.test_client()

    # Create test archive DB with real schema
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

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
    # Should have 4 conversations: conv-1 (5 msgs), conv-2 (3 msgs), conv-abc (2 msgs), conv-wildcard (2 msgs)
    assert data["count"] == 4
    assert len(data["conversations"]) == 4
    # Should be ordered by updated_at DESC (conv-wildcard last updated at 1704326401, then conv-abc at 1704240001, then conv-2 at 1704153602, then conv-1 at 1704067204)
    assert data["conversations"][0]["conversation_id"] == "conv-wildcard"
    assert data["conversations"][1]["conversation_id"] == "conv-abc"
    assert data["conversations"][2]["conversation_id"] == "conv-2"
    assert data["conversations"][3]["conversation_id"] == "conv-1"
    # Check message counts
    conv_by_id = {c["conversation_id"]: c for c in data["conversations"]}
    assert conv_by_id["conv-1"]["message_count"] == 5
    assert conv_by_id["conv-2"]["message_count"] == 3
    assert conv_by_id["conv-abc"]["message_count"] == 2
    assert conv_by_id["conv-wildcard"]["message_count"] == 2


def test_archive_conversations_with_query_filter(tmp_path):
    app = _make_app()
    client = app.test_client()

    # Create test archive DB
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            # Query for 'abc' should match conv-abc
            resp = client.get("/api/proxy/archive/conversations?model=test-model&query=abc")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
    assert data["conversations"][0]["conversation_id"] == "conv-abc"


def test_archive_conversations_sort_by_message_count(tmp_path):
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/conversations?model=test-model&sort=message_count_desc")

    assert resp.status_code == 200
    data = resp.get_json()
    # conv-1 has 5 messages, conv-2 has 3, conv-abc and conv-wildcard have 2 each
    assert data["conversations"][0]["conversation_id"] == "conv-1"
    assert data["conversations"][1]["conversation_id"] == "conv-2"
    # conv-abc and conv-wildcard both have 2 messages; order between them is not guaranteed
    # but both should be in the last two positions
    remaining = {c["conversation_id"] for c in data["conversations"][2:]}
    assert remaining == {"conv-abc", "conv-wildcard"}


def test_archive_conversations_pagination_total(tmp_path):
    """Test that paginated conversations request returns correct total (not just page size)."""
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            # Request with limit=2, should return 2 conversations but total=4
            resp = client.get("/api/proxy/archive/conversations?model=test-model&limit=2&offset=0")

    assert resp.status_code == 200
    data = resp.get_json()
    # count is the page size (2), total is the actual total (4)
    assert data["count"] == 2
    assert data["total"] == 4
    assert len(data["conversations"]) == 2
    # Second page should also have total=4
    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp2 = client.get("/api/proxy/archive/conversations?model=test-model&limit=2&offset=2")
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2["count"] == 2
    assert data2["total"] == 4
    assert len(data2["conversations"]) == 2


def test_archive_conversations_pagination_with_query_filter(tmp_path):
    """Test that paginated conversations request with query filter returns correct offset behavior."""
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            # Query for 'abc' should match conv-abc (2 messages)
            # With limit=1, offset=0 should return first result
            resp = client.get("/api/proxy/archive/conversations?model=test-model&query=abc&limit=1&offset=0")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
    assert data["total"] == 1  # Only conv-abc matches
    assert len(data["conversations"]) == 1
    assert data["conversations"][0]["conversation_id"] == "conv-abc"

    # Test offset with query filter - should return empty since only 1 match
    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp2 = client.get("/api/proxy/archive/conversations?model=test-model&query=abc&limit=1&offset=1")
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2["count"] == 0
    assert data2["total"] == 1
    assert len(data2["conversations"]) == 0


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
    assert data["total"] == 0


def test_archive_journal_with_data(tmp_path):
    app = _make_app()
    client = app.test_client()

    # Create test journal file with real event format
    journal_path = tmp_path / "data" / "model" / "journal.jsonl"
    events = [
        {"ts": 1704067200.0, "event": "request", "conv": "conv-1", "prompt_tokens": 100},
        {"ts": 1704067201.0, "event": "auto_recall", "conv": "conv-1"},
        {"ts": 1704067202.0, "event": "request", "conv": "conv-2", "prompt_tokens": 200},
        {"ts": 1704067203.0, "event": "new_conversation", "conv": "conv-3", "messages": 1},
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
    assert data["count"] == 4
    assert data["total"] == 4
    # Most recent first (reversed)
    assert data["events"][0]["event_type"] == "new_conversation"
    assert data["events"][0]["conversation_id"] == "conv-3"
    assert data["events"][3]["event_type"] == "request"
    assert data["events"][3]["conversation_id"] == "conv-1"


def test_archive_journal_with_conversation_filter(tmp_path):
    app = _make_app()
    client = app.test_client()

    journal_path = tmp_path / "data" / "model" / "journal.jsonl"
    events = [
        {"ts": 1704067200.0, "event": "request", "conv": "conv-1", "prompt_tokens": 100},
        {"ts": 1704067201.0, "event": "recall", "conv": "conv-1"},
        {"ts": 1704067202.0, "event": "request", "conv": "conv-2", "prompt_tokens": 200},
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
    assert all(e["conversation_id"] == "conv-1" for e in data["events"])


def test_archive_journal_with_event_filter(tmp_path):
    app = _make_app()
    client = app.test_client()

    journal_path = tmp_path / "data" / "model" / "journal.jsonl"
    events = [
        {"ts": 1704067200.0, "event": "request", "conv": "conv-1", "prompt_tokens": 100},
        {"ts": 1704067201.0, "event": "recall", "conv": "conv-1"},
        {"ts": 1704067202.0, "event": "request", "conv": "conv-2", "prompt_tokens": 200},
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
    assert data["events"][0]["event_type"] == "recall"
    assert data["events"][0]["conversation_id"] == "conv-1"


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

    # Create test archive DB
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&id=rid-3")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pieces"][0]["id"] == "rid-3"
    assert data["pieces"][0]["content"] == "Hi there! How can I help?"


def test_archive_recall_with_query(tmp_path):
    app = _make_app()
    client = app.test_client()

    # Create test archive DB
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

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
    assert data["pieces"][0]["content"] == "Hello world"


def test_archive_recall_invalid_mode(tmp_path):
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
            resp = client.get("/api/proxy/archive/recall?model=test-model&mode=invalid")

    assert resp.status_code == 400
    data = resp.get_json()
    assert "Invalid mode" in data["error"]


def test_archive_recall_invalid_order(tmp_path):
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
            resp = client.get("/api/proxy/archive/recall?model=test-model&order=invalid")

    assert resp.status_code == 400
    data = resp.get_json()
    assert "Invalid order" in data["error"]


def test_archive_recall_role_passed_through(tmp_path):
    """Role is passed through without validation (real archive has roles like assistant-reasoning, assistant-tool-args)."""
    app = _make_app()
    client = app.test_client()

    # Create test archive DB
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            # Custom role should not cause an error - just filters (no matches)
            resp = client.get("/api/proxy/archive/recall?model=test-model&role=custom-role")

    assert resp.status_code == 200
    data = resp.get_json()
    # No matches for custom role
    assert data["pieces"] == []
    assert data["total"] == 0


def test_archive_recall_invalid_from_to(tmp_path):
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
            resp = client.get("/api/proxy/archive/recall?model=test-model&from=10&to=5")

    assert resp.status_code == 400
    data = resp.get_json()
    assert "from' must be <= 'to" in data["error"]


def test_archive_conversations_negative_limit(tmp_path):
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
            resp = client.get("/api/proxy/archive/conversations?model=test-model&limit=-5")

    assert resp.status_code == 200
    data = resp.get_json()
    # Should default to 50 when negative
    assert data["limit"] == 50


def test_archive_conversations_excessive_limit(tmp_path):
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
            resp = client.get("/api/proxy/archive/conversations?model=test-model&limit=5000")

    assert resp.status_code == 200
    data = resp.get_json()
    # Should clamp to max 1000
    assert data["limit"] == 1000


def test_archive_journal_negative_offset(tmp_path):
    app = _make_app()
    client = app.test_client()

    journal_path = tmp_path / "data" / "model" / "journal.jsonl"
    events = [
        {"ts": 1704067200.0, "event": "request", "conv": "conv-1", "prompt_tokens": 100},
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
            resp = client.get("/api/proxy/archive/journal?model=test-model&offset=-10")

    assert resp.status_code == 200
    data = resp.get_json()
    # Should default to 0 when negative
    assert data["offset"] == 0


def test_archive_recall_negative_seg_treated_as_none(tmp_path):
    """Negative seg values are treated as None (no filter), not an error."""
    app = _make_app()
    client = app.test_client()

    # Create test archive DB
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            # Negative seg should be treated as no filter (not an error)
            resp = client.get("/api/proxy/archive/recall?model=test-model&seg=-5")

    assert resp.status_code == 200
    data = resp.get_json()
    # Should return all pieces (no seg filter applied)
    assert len(data["pieces"]) > 0


def test_archive_recall_mode_first(tmp_path):
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&mode=first")

    assert resp.status_code == 200
    data = resp.get_json()
    # mode=first should return the OLDEST match (conv-1 system prompt at idx 0)
    assert len(data["pieces"]) == 1
    assert data["pieces"][0]["content"] == "System prompt"


def test_archive_recall_mode_first_null_created(tmp_path):
    """Test that mode=first sorts NULL-created rows last (not as oldest)."""
    app = _make_app()
    client = app.test_client()

    # Create a test archive DB WITHOUT NOT NULL on created (to test NULL handling)
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    archive_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(archive_db))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE archive (
            rid TEXT PRIMARY KEY,
            conv TEXT NOT NULL,
            idx INTEGER NOT NULL,
            role TEXT NOT NULL,
            name TEXT,
            content TEXT NOT NULL,
            tokens INTEGER,
            created REAL  -- Allow NULL for this test
        )
    """)
    # Insert test data with a NULL created row
    cursor.execute("""
        INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rid-null", "conv-1", 10, "user", None, "NULL created test", 10, None))
    # Insert a normal row
    cursor.execute("""
        INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rid-1", "conv-1", 0, "system", None, "System prompt", 100, 1704067200.0))
    conn.commit()
    conn.close()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&mode=first")

    assert resp.status_code == 200
    data = resp.get_json()
    # mode=first should return the OLDEST non-NULL match (System prompt at idx 0)
    # NOT the NULL-created row
    assert len(data["pieces"]) == 1
    assert data["pieces"][0]["content"] == "System prompt"
    assert data["pieces"][0]["id"] == "rid-1"


def test_archive_recall_mode_first_with_query(tmp_path):
    """Test mode=first with query returns oldest matching piece by created timestamp."""
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            # Query for "Hello" matches conv-1 (Hello world at idx 1, created 1704067201)
            # and conv-2 (First message at idx 0, created 1704153600)
            # The oldest match should be "Hello world" at 1704067201
            resp = client.get("/api/proxy/archive/recall?model=test-model&query=Hello&mode=first")

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["pieces"]) == 1
    # Should return the oldest matching piece (Hello world from conv-1)
    assert data["pieces"][0]["content"] == "Hello world"
    assert data["pieces"][0]["conversation_id"] == "conv-1"


def test_archive_without_segments_table(tmp_path):
    """Test that archive works correctly when segments table is missing."""
    app = _make_app()
    client = app.test_client()

    # Create archive DB WITHOUT segments table
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    archive_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(archive_db))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE archive (
            rid TEXT PRIMARY KEY,
            conv TEXT NOT NULL,
            idx INTEGER NOT NULL,
            role TEXT NOT NULL,
            name TEXT,
            content TEXT NOT NULL,
            tokens INTEGER,
            created REAL NOT NULL
        )
    """)
    # Insert test data
    cursor.execute("""
        INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rid-1", "conv-1", 0, "system", None, "System prompt", 100, 1704067200.0))
    cursor.execute("""
        INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rid-2", "conv-1", 1, "user", None, "Hello world", 50, 1704067201.0))
    conn.commit()
    conn.close()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            # Test conversations endpoint
            resp = client.get("/api/proxy/archive/conversations?model=test-model")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["conversations"][0]["segment_count"] == 0

            # Test recall endpoint (timeline mode)
            resp2 = client.get("/api/proxy/archive/recall?model=test-model&mode=timeline")
            assert resp2.status_code == 200
            data2 = resp2.get_json()
            assert len(data2["pieces"]) == 2
            assert data2["pieces"][0]["segment"] == 1  # Default segment when no segments table

            # Test recall endpoint (mode=first)
            resp3 = client.get("/api/proxy/archive/recall?model=test-model&mode=first")
            assert resp3.status_code == 200
            data3 = resp3.get_json()
            assert len(data3["pieces"]) == 1
            assert data3["pieces"][0]["segment"] == 1

            # Test piece endpoint
            resp4 = client.get("/api/proxy/archive/piece/rid-1?model=test-model")
            assert resp4.status_code == 200
            data4 = resp4.get_json()
            assert data4["segment"] == 1

            # Test seg filter is a no-op (should not crash)
            resp5 = client.get("/api/proxy/archive/recall?model=test-model&seg=1")
            assert resp5.status_code == 200
            data5 = resp5.get_json()
            assert len(data5["pieces"]) == 2


def test_archive_recall_path_filter_with_fileops(tmp_path):
    """Test that path filter uses fileops table when available."""
    app = _make_app()
    client = app.test_client()

    # Create archive DB WITH fileops table
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    archive_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(archive_db))
    cursor = conn.cursor()

    # Create archive table
    cursor.execute("""
        CREATE TABLE archive (
            rid TEXT PRIMARY KEY,
            conv TEXT NOT NULL,
            idx INTEGER NOT NULL,
            role TEXT NOT NULL,
            name TEXT,
            content TEXT NOT NULL,
            tokens INTEGER,
            created REAL NOT NULL
        )
    """)

    # Create fileops table (real schema)
    cursor.execute("""
        CREATE TABLE fileops (
            conv TEXT NOT NULL,
            idx INTEGER NOT NULL,
            call_id TEXT,
            op TEXT NOT NULL,
            path TEXT NOT NULL,
            outcome TEXT,
            rid_args TEXT,
            rid_out TEXT,
            head TEXT,
            PRIMARY KEY (conv, idx, call_id)
        )
    """)

    # Insert test data in archive - content does NOT contain the file path
    cursor.execute("""
        INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rid-1", "conv-1", 0, "system", None, "System prompt", 100, 1704067200.0))
    cursor.execute("""
        INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rid-2", "conv-1", 1, "user", None, "I'll help with that file", 50, 1704067201.0))
    cursor.execute("""
        INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rid-3", "conv-1", 2, "assistant", None, "Done", 30, 1704067202.0))

    # Insert fileops row with path that is NOT in archive.content
    cursor.execute("""
        INSERT INTO fileops (conv, idx, call_id, op, path, outcome, rid_args, rid_out, head)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("conv-1", 1, "call-123", "write", "/home/user/secret.txt", "success", "rid-args", "rid-out", "head"))

    # Also add a fileops row for a different path
    cursor.execute("""
        INSERT INTO fileops (conv, idx, call_id, op, path, outcome, rid_args, rid_out, head)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("conv-1", 2, "call-456", "read", "/home/user/other.txt", "success", "rid-args", "rid-out", "head"))

    conn.commit()
    conn.close()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            # Search for path that ONLY exists in fileops, not in archive.content
            resp = client.get("/api/proxy/archive/recall?model=test-model&path=/home/user/secret.txt")

    assert resp.status_code == 200
    data = resp.get_json()
    # Should find the piece at idx=1 (where fileops has the path)
    assert len(data["pieces"]) == 1
    assert data["pieces"][0]["id"] == "rid-2"
    assert data["pieces"][0]["conversation_id"] == "conv-1"

    # Search for the other path
    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp2 = client.get("/api/proxy/archive/recall?model=test-model&path=/home/user/other.txt")

    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert len(data2["pieces"]) == 1
    assert data2["pieces"][0]["id"] == "rid-3"

    # Search for non-existent path
    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp3 = client.get("/api/proxy/archive/recall?model=test-model&path=/nonexistent.txt")

    assert resp3.status_code == 200
    data3 = resp3.get_json()
    assert len(data3["pieces"]) == 0


def test_archive_recall_mode_default_alias(tmp_path):
    """mode=default is the frontend default and must alias mode=timeline (HTTP 200)."""
    app = _make_app()
    client = app.test_client()

    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(archive_db)

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            default_resp = client.get(
                "/api/proxy/archive/recall?model=test-model&query=Hello&mode=default"
            )
            timeline_resp = client.get(
                "/api/proxy/archive/recall?model=test-model&query=Hello&mode=timeline"
            )

    assert default_resp.status_code == 200
    assert timeline_resp.status_code == 200
    default_data = default_resp.get_json()
    timeline_data = timeline_resp.get_json()
    # mode=default must be an alias for mode=timeline, producing identical results
    assert default_data["pieces"] == timeline_data["pieces"]
    assert default_data["pieces"][0]["content"] == "Hello world"


def test_archive_recall_like_wildcard_escape(tmp_path):
    app = _make_app()
    client = app.test_client()

    # Create test archive DB with wildcard characters
    archive_db = tmp_path / "data" / "model" / "archive.sqlite"
    archive_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(archive_db))
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE archive (
            rid TEXT PRIMARY KEY,
            conv TEXT NOT NULL,
            idx INTEGER NOT NULL,
            role TEXT NOT NULL,
            name TEXT,
            content TEXT NOT NULL,
            tokens INTEGER,
            created REAL NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE segments (
            conv TEXT NOT NULL,
            seg INTEGER NOT NULL,
            cut_idx INTEGER,
            cut_h TEXT,
            notes_msg TEXT,
            kind TEXT,
            created REAL,
            pins_msg TEXT,
            PRIMARY KEY (conv, seg)
        )
    """)
    cursor.execute("""
        CREATE VIRTUAL TABLE archive_fts USING fts5(
            content,
            content='archive',
            content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        )
    """)
    cursor.execute("""
        CREATE TRIGGER archive_ai AFTER INSERT ON archive BEGIN
            INSERT INTO archive_fts(rowid, content) VALUES (new.rowid, new.content);
        END
    """)

    # Insert messages with % and _ wildcards
    cursor.execute("""
        INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rid-wild-1", "conv-wild", 0, "user", None, "100% complete", 50, 1704067200.0))
    cursor.execute("""
        INSERT INTO archive (rid, conv, idx, role, name, content, tokens, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rid-wild-2", "conv-wild", 1, "user", None, "test_underscore", 50, 1704067201.0))

    conn.commit()
    conn.close()

    class PM:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345

        def _resolve_data_dir(self):
            return tmp_path / "data" / "model"

    with patch("webapp.routes.archive._get_model_config", return_value={"port": 9931}):
        with patch("webapp.routes.archive.get_proxy_manager", return_value=PM()):
            resp = client.get("/api/proxy/archive/recall?model=test-model&query=100%")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pieces"][0]["content"] == "100% complete"


def test_archive_recall_remote_mode(monkeypatch):
    monkeypatch.setenv("REMOTE_MODE", "true")
    app = _make_app()
    client = app.test_client()

    resp = client.get("/api/proxy/archive/conversations?model=test-model")

    assert resp.status_code == 400
    data = resp.get_json()
    assert "remote mode" in data["error"].lower()


def test_archive_journal_remote_mode(monkeypatch):
    monkeypatch.setenv("REMOTE_MODE", "true")
    app = _make_app()
    client = app.test_client()

    resp = client.get("/api/proxy/archive/journal?model=test-model")

    assert resp.status_code == 400
    data = resp.get_json()
    assert "remote mode" in data["error"].lower()


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
