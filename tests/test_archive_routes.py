"""Tests for mnemonic-archive API endpoints with real archive schema."""
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
    assert data["events"][0]["event"] == "new_conversation"
    assert data["events"][0]["conv"] == "conv-3"
    assert data["events"][3]["event"] == "request"
    assert data["events"][3]["conv"] == "conv-1"


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
    assert all(e["conv"] == "conv-1" for e in data["events"])


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
    assert data["events"][0]["event"] == "recall"
    assert data["events"][0]["conv"] == "conv-1"


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