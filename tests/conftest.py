"""Shared fixtures for archive testing with real mnemonic-proxy archive schema."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


# ─── Helper Functions ────────────────────────────────────────────────────────

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


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_journal_events():
    """Sample journal events for testing."""
    return [
        {"ts": 1704067200.0, "event": "request", "conv": "conv-1", "prompt_tokens": 100},
        {"ts": 1704067201.0, "event": "auto_recall", "conv": "conv-1"},
        {"ts": 1704067202.0, "event": "request", "conv": "conv-2", "prompt_tokens": 200},
        {"ts": 1704067203.0, "event": "new_conversation", "conv": "conv-3", "messages": 1},
    ]


@pytest.fixture
def tmp_archive_db(tmp_path):
    """Create a temporary archive.sqlite with sample data in the expected location."""
    db_path = tmp_path / "data" / "model" / "archive.sqlite"
    _create_test_archive_db(db_path)
    return db_path


@pytest.fixture
def tmp_journal_file(tmp_path, sample_journal_events):
    """Create a temporary journal.jsonl with sample events in the expected location."""
    journal_path = tmp_path / "data" / "model" / "journal.jsonl"
    _write_journal(journal_path, sample_journal_events)
    return journal_path


@pytest.fixture
def mock_archive_model_config():
    """Mock model configuration for archive testing."""
    return {
        "name": "test-model",
        "port": 9931,
        "model_path": "/fake/path/model.gguf",
        "repo": "test/repo",
        "filename": "model.gguf",
        "expected_size": 1000000000,
        "mnemonic_proxy": {
            "enabled": True,
            "port": 8096,
            "data_dir": "./data/model",
            "slot_dir": "./data/model",
        },
    }


@pytest.fixture
def mock_proxy_manager(tmp_path):
    """Mock proxy manager for testing."""

    class MockProxyManager:
        proxy_port = 8096
        data_dir = "./data/model"
        _pid = 12345
        _tmp_path = tmp_path

        def _resolve_data_dir(self):
            return self._tmp_path / "data" / "model"

    return MockProxyManager()


@pytest.fixture
def test_app():
    """Create a test Flask app with archive blueprint registered."""
    from flask import Flask
    from webapp.routes.archive import archive_bp
    from webapp.config import BASE_DIR

    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "webapp" / "static"),
        template_folder=str(BASE_DIR / "webapp" / "templates"),
    )
    app.config["TESTING"] = True
    app.register_blueprint(archive_bp)
    return app


@pytest.fixture
def test_client(test_app):
    """Create a test client for the test app."""
    return test_app.test_client()


@pytest.fixture
def patched_archive_routes(mock_archive_model_config, mock_proxy_manager):
    """Patch archive routes with mock dependencies."""
    patches = [
        patch("webapp.routes.archive._get_model_config", return_value=mock_archive_model_config),
        patch("webapp.routes.archive.get_proxy_manager", return_value=mock_proxy_manager),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()