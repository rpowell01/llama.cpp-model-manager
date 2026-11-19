"""Mnemonic Archive API endpoints for conversation history, journal events, and recall."""
import json
import os
import re
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from flask import Blueprint, jsonify, request, g
import requests

from webapp.config import BASE_DIR
from webapp.mnemonic_proxy import get_proxy_manager
from webapp.models import get_models, InvalidModeError

log = logging.getLogger("llamaweb")

archive_bp = Blueprint("archive", __name__)


@archive_bp.errorhandler(InvalidModeError)
def _handle_invalid_mode_error(e: InvalidModeError):
    """Handle InvalidModeError for remote mode requests."""
    return jsonify({"error": str(e), "code": "REMOTE_MODE_INVALID"}), 400


def _validate_local_mode() -> None:
    """Validate that we're in local mode."""
    remote_mode = os.getenv("REMOTE_MODE", "").strip().lower() in ("true", "1", "yes", "on")
    if remote_mode:
        raise InvalidModeError("mnemonic-archive API endpoints not available in remote mode")


def _validate_limit(limit: int, max_limit: int = 1000, default: int = 50) -> int:
    """Validate and clamp limit parameter."""
    if limit < 1:
        return default
    if limit > max_limit:
        return max_limit
    return limit


def _validate_offset(offset: int, default: int = 0) -> int:
    """Validate and clamp offset parameter."""
    if offset < 0:
        return default
    return offset


def _validate_positive_int(value: Optional[int], default: Optional[int] = None) -> Optional[int]:
    """Validate that an integer parameter is positive."""
    if value is None:
        return default
    if value < 1:
        return default
    return value


def _escape_like_wildcards(text: str) -> str:
    """Escape LIKE wildcards (% and _) in user input."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _epoch_to_iso(epoch: Optional[float]) -> str:
    """Convert epoch seconds (float) to ISO-8601 UTC string with microseconds.
    
    Returns epoch 0 (1970-01-01T00:00:00.000Z) for NULL/None timestamps,
    non-numeric values, or out-of-range timestamps.
    """
    if epoch is None:
        return "1970-01-01T00:00:00.000Z"
    # Coerce to float, fall back to epoch-0 on any error
    try:
        epoch_float = float(epoch)
    except (TypeError, ValueError):
        return "1970-01-01T00:00:00.000Z"
    # datetime.fromtimestamp raises OverflowError for out-of-range values
    # and OSError for values outside platform's supported range
    try:
        dt = datetime.fromtimestamp(epoch_float, tz=timezone.utc)
    except (OverflowError, OSError):
        return "1970-01-01T00:00:00.000Z"
    # Always include microseconds for consistent string ordering
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _tokenize_fts_query(text: str) -> list[str]:
    """
    Tokenize text for FTS5 query, matching mnemonic-proxy behavior.

    Extracts word tokens (alphanumeric + underscore), strips quotes,
    and returns unique non-empty tokens.
    """
    # Use regex to find word tokens (matching \w+)
    tokens = re.findall(r"\w+", text)
    # Filter empty and deduplicate while preserving order
    seen = set()
    result = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _build_fts_query(terms: list[str]) -> str:
    """Build FTS5 MATCH query from search terms - OR of quoted tokens."""
    all_tokens = []
    for term in terms:
        all_tokens.extend(_tokenize_fts_query(term))
    if not all_tokens:
        return '""'  # Match nothing
    # FTS5: quote each token to avoid syntax errors on operators like OR/AND
    # ctxproxy wraps each token in double quotes
    return " OR ".join(f'"{t}"' for t in all_tokens)


def _candidate_journal_paths(manager) -> list[Path]:
    """Resolve candidate journal.jsonl paths for a proxy.

    The Flask app and mnemonic-proxy may run with different CWDs, so a
    relative data_dir can point to different locations. Probe the
    manager's resolved path, the process CWD (via /proc/<pid>/cwd), and
    common roots for the configured relative path.
    """
    candidates = []

    def add(p):
        try:
            rp = Path(p).resolve()
        except Exception:
            rp = Path(p)
        if rp not in candidates:
            candidates.append(rp)

    try:
        add(manager._resolve_data_dir() / "journal.jsonl")
    except Exception:
        pass

    raw_dir = getattr(manager, "data_dir", "") or ""
    raw_path = Path(raw_dir) if raw_dir else None
    if raw_path and raw_path.is_absolute():
        add(raw_path / "journal.jsonl")
    elif raw_path:
        add(BASE_DIR / raw_path / "journal.jsonl")
        pid = getattr(manager, "_pid", None) or getattr(getattr(manager, "_process", None), "pid", None)
        if pid:
            try:
                cwd = Path(f"/proc/{pid}/cwd").resolve()
                add(cwd / raw_path / "journal.jsonl")
            except Exception:
                pass
        # Fallback: repo root above a worktree-style app cwd.
        try:
            add(BASE_DIR.parent.parent / raw_path / "journal.jsonl")
        except Exception:
            pass

    return candidates


def _candidate_archive_paths(manager) -> list[Path]:
    """Resolve candidate archive.sqlite paths for a proxy."""
    candidates = []

    def add(p):
        try:
            rp = Path(p).resolve()
        except Exception:
            rp = Path(p)
        if rp not in candidates:
            candidates.append(rp)

    try:
        add(manager._resolve_data_dir() / "archive.sqlite")
    except Exception:
        pass

    raw_dir = getattr(manager, "data_dir", "") or ""
    raw_path = Path(raw_dir) if raw_dir else None
    if raw_path and raw_path.is_absolute():
        add(raw_path / "archive.sqlite")
    elif raw_path:
        add(BASE_DIR / raw_path / "archive.sqlite")
        pid = getattr(manager, "_pid", None) or getattr(getattr(manager, "_process", None), "pid", None)
        if pid:
            try:
                cwd = Path(f"/proc/{pid}/cwd").resolve()
                add(cwd / raw_path / "archive.sqlite")
            except Exception:
                pass
        try:
            add(BASE_DIR.parent.parent / raw_path / "archive.sqlite")
        except Exception:
            pass

    return candidates


def _find_archive_db(manager) -> Optional[Path]:
    """Find the first existing archive.sqlite file."""
    for path in _candidate_archive_paths(manager):
        if path.exists():
            return path
    return None


def _find_journal_file(manager) -> Optional[Path]:
    """Find the first existing journal.jsonl file."""
    for path in _candidate_journal_paths(manager):
        if path.exists():
            return path
    return None


def _get_model_config(model: str) -> Optional[dict]:
    """Get model configuration from models.json."""
    models = get_models()
    # Case-insensitive lookup
    for k, v in models.items():
        if k.lower() == model.lower():
            return v
    return None


def _get_proxy_manager(model: str):
    """Get or create proxy manager for a model."""
    proxy_manager = get_proxy_manager(model)
    if not proxy_manager:
        model_config = _get_model_config(model)
        if not model_config:
            return None
        from webapp.mnemonic_proxy import MnemonicProxyManager
        llama_port = model_config.get("port")
        if not llama_port:
            return None
        mp_config = model_config.get("mnemonic_proxy", {})
        return MnemonicProxyManager(
            model_name=model,
            llama_port=model_config.get("port"),
            proxy_port=mp_config.get("port", 8096),
            data_dir=mp_config.get("data_dir", ""),
            slot_dir=mp_config.get("slot_dir", ""),
            tokenizer_path=mp_config.get("tokenizer_path", ""),
            config_override=mp_config.get("config_override", {}),
            mcp_enabled=mp_config.get("mcp_enabled", False),
            mcp_port=mp_config.get("mcp_port", 8133),
        )
    return proxy_manager


def _query_conversations_from_archive(
    conn: sqlite3.Connection,
    limit: int,
    offset: int,
    sort: str,
    query: Optional[str] = None,
) -> tuple[list[dict], int]:
    """
    Query conversations from the real archive schema.

    The archive table has messages with conv (conversation ID), created (timestamp),
    tokens, role, content. We aggregate by conv to get conversation metadata.
    Also includes segment_count from the segments table (if it exists).
    """
    cursor = conn.cursor()

    # Check if archive table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archive'")
    if not cursor.fetchone():
        return [], 0

    # Check if segments table exists for segment_count
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='segments'")
    has_segments = cursor.fetchone() is not None

    # Build WHERE clause for query filtering
    where_clause = ""
    params = []
    if query:
        # Use a two-step approach: first find matching convs, then filter
        # This avoids the correlated EXISTS subquery which is slow on large datasets
        like_query = f"%{_escape_like_wildcards(query)}%"
        # Find conversations where any message content matches the query
        cursor.execute("""
            SELECT DISTINCT conv FROM archive
            WHERE content LIKE ? ESCAPE '\\'
        """, (like_query,))
        matching_convs = {row[0] for row in cursor.fetchall()}
        
        # Build WHERE clause using IN for the main query
        conv_placeholders = ",".join(["?"] * len(matching_convs)) if matching_convs else "0=1"
        where_clause = f"WHERE a.conv IN ({conv_placeholders})"
        params = list(matching_convs)
        # Also add conversation ID LIKE search as fallback
        if not matching_convs:
            where_clause = "WHERE conv LIKE ? ESCAPE '\\'"
            params = [like_query]

    # Build ORDER BY clause based on sort parameter
    sort_mapping = {
        "updated_desc": "updated_at DESC",
        "updated_asc": "updated_at ASC",
        "created_desc": "created_at DESC",
        "created_asc": "created_at ASC",
        "message_count_desc": "message_count DESC",
        "message_count_asc": "message_count ASC",
    }
    order_by = sort_mapping.get(sort, "updated_at DESC")

    # Get total count for pagination - count distinct conversations
    count_sql = f"""
        SELECT COUNT(DISTINCT conv) as total
        FROM archive a
        {where_clause}
    """
    cursor.execute(count_sql, params)
    total_row = cursor.fetchone()
    total = total_row["total"] if total_row else 0

    # Build query with pagination - aggregate messages by conversation
    # Include segment_count from segments table if it exists
    # Filter out NULL created values in MIN/MAX to avoid NULL results
    segment_count_sql = (
        "COALESCE((SELECT COUNT(*) FROM segments s WHERE s.conv = a.conv), 0)"
        if has_segments else "0"
    )
    sql = f"""
        SELECT
            a.conv as conversation_id,
            MIN(a.created) FILTER (WHERE a.created IS NOT NULL) as created_at,
            MAX(a.created) FILTER (WHERE a.created IS NOT NULL) as updated_at,
            COUNT(*) as message_count,
            SUM(a.tokens) as token_count,
            {segment_count_sql} as segment_count
        FROM archive a
        {where_clause}
        GROUP BY a.conv
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
    """
    params_with_pagination = params + [limit, offset]
    cursor.execute(sql, params_with_pagination)

    conversations = []
    for row in cursor.fetchall():
        conv = {
            "id": row["conversation_id"],
            "conversation_id": row["conversation_id"],
            "created_at": _epoch_to_iso(row["created_at"]),
            "updated_at": _epoch_to_iso(row["updated_at"]),
            "message_count": row["message_count"],
            "token_count": row["token_count"] or 0,
            "segment_count": row["segment_count"],
        }
        conversations.append(conv)

    return conversations, total


def _get_archive_table_info(
    conn: sqlite3.Connection,
    query: Optional[str] = None,
    queries: Optional[list[str]] = None,
) -> tuple[bool, bool, bool, bool, list[str]]:
    """
    Get archive table existence flags and build search terms.
    Returns (has_fts, has_segments, has_fileops, search_terms).
    """
    cursor = conn.cursor()

    # Check if FTS table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archive_fts'")
    has_fts = cursor.fetchone() is not None

    # Check if segments table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='segments'")
    has_segments = cursor.fetchone() is not None

    # Check if fileops table exists for path filtering
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fileops'")
    has_fileops = cursor.fetchone() is not None

    search_terms = []
    if query:
        search_terms.append(query)
    if queries:
        search_terms.extend(queries)

    return has_fts, has_segments, has_fileops, search_terms


def _build_pieces_where_clauses(
    conversation_id: Optional[str],
    role: Optional[str],
    seg: Optional[int],
    from_msg: Optional[int],
    to_msg: Optional[int],
    search_terms: list[str],
    path: Optional[str],
    has_fts: bool,
    has_segments: bool,
    has_fileops: bool = False,
) -> tuple[str, list]:
    """
    Build WHERE clauses and parameters for pieces queries.
    Returns (where_sql, params).
    """
    where_clauses = []
    params = []

    if conversation_id:
        where_clauses.append("a.conv = ?")
        params.append(conversation_id)

    # Role filter: accept any role from the archive
    if role:
        where_clauses.append("a.role = ?")
        params.append(role)

    # Segment filter: only if segments table exists
    if seg is not None and has_segments:
        where_clauses.append("""
            EXISTS (
                SELECT 1 FROM segments s
                WHERE s.conv = a.conv
                AND s.seg = ?
                AND a.idx >= s.cut_idx
                AND (NOT EXISTS (
                    SELECT 1 FROM segments s2
                    WHERE s2.conv = a.conv
                    AND s2.seg = s.seg + 1
                    AND a.idx >= s2.cut_idx
                ))
            )
        """)
        params.append(seg)

    if from_msg is not None:
        where_clauses.append("a.idx >= ?")
        params.append(from_msg)

    if to_msg is not None:
        where_clauses.append("a.idx <= ?")
        params.append(to_msg)

    use_fts = has_fts and not path and search_terms

    if use_fts:
        # Use FTS5 for full-text search - join on rowid
        fts_query = _build_fts_query(search_terms)
        where_clauses.append("a.rowid IN (SELECT rowid FROM archive_fts WHERE content MATCH ?)")
        params.append(fts_query)
    elif search_terms:
        # Fallback to LIKE search
        like_clauses = []
        for term in search_terms:
            escaped = _escape_like_wildcards(term)
            like_clauses.append("(a.content LIKE ? ESCAPE '\\' OR a.name LIKE ? ESCAPE '\\')")
            like_term = f"%{escaped}%"
            params.extend([like_term, like_term])
        where_clauses.append(f"({' OR '.join(like_clauses)})")

    if path:
        if has_fileops:
            # Use fileops table for path filtering when available
            where_clauses.append("""
                EXISTS (
                    SELECT 1 FROM fileops f
                    WHERE f.conv = a.conv
                    AND f.idx = a.idx
                    AND f.path LIKE ? ESCAPE '\\'
                )
            """)
            like_path = f"%{_escape_like_wildcards(path)}%"
            params.append(like_path)
        else:
            # Fall back to searching archive.content
            where_clauses.append("a.content LIKE ? ESCAPE '\\'")
            like_path = f"%{_escape_like_wildcards(path)}%"
            params.append(like_path)

    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    return where_sql, params


def _query_pieces_from_archive(
    conn: sqlite3.Connection,
    conversation_id: Optional[str] = None,
    role: Optional[str] = None,
    seg: Optional[int] = None,
    from_msg: Optional[int] = None,
    to_msg: Optional[int] = None,
    query: Optional[str] = None,
    queries: Optional[list[str]] = None,
    order: str = "pertinenza",
    limit: int = 20,
    offset: int = 0,
    path: Optional[str] = None,
) -> list[dict]:
    """
    Query pieces (messages) from the real archive schema.

    The archive table has: rid, conv, idx, role, name, content, tokens, created
    We map these to the expected piece format.
    Segment is derived from segments.cut_idx (if segments table exists).
    """
    cursor = conn.cursor()

    # Check if archive table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archive'")
    if not cursor.fetchone():
        return []

    # Check if FTS table exists for full-text search
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archive_fts'")
    has_fts = cursor.fetchone() is not None

    # Check if segments table exists for segment derivation
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='segments'")
    has_segments = cursor.fetchone() is not None

    # Check if fileops table exists for path filtering
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fileops'")
    has_fileops = cursor.fetchone() is not None

    search_terms = []
    if query:
        search_terms.append(query)
    if queries:
        search_terms.extend(queries)

    where_sql, params = _build_pieces_where_clauses(
        conversation_id=conversation_id,
        role=role,
        seg=seg,
        from_msg=from_msg,
        to_msg=to_msg,
        search_terms=search_terms,
        path=path,
        has_fts=has_fts,
        has_segments=has_segments,
        has_fileops=has_fileops,
    )

    # Order by
    if order == "tempo":
        order_sql = "ORDER BY a.created DESC, a.idx DESC, a.rid DESC"
    else:
        # Relevance: prioritize exact content matches, then by time
        if search_terms:
            first_escaped = _escape_like_wildcards(search_terms[0])
            order_sql = f"ORDER BY (CASE WHEN a.content LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END), a.created DESC, a.idx DESC, a.rid DESC"
            params.append(f"%{first_escaped}%")
        else:
            order_sql = "ORDER BY a.created DESC, a.idx DESC, a.rid DESC"

    # Include segment info from segments table if it exists
    if has_segments:
        segment_sql = """
            COALESCE((
                SELECT s.seg FROM segments s
                WHERE s.conv = a.conv
                AND s.cut_idx <= a.idx
                ORDER BY s.cut_idx DESC LIMIT 1
            ), 1)
        """
    else:
        segment_sql = "1"

    sql = f"""
        SELECT
            a.rid, a.conv, a.idx, a.role, a.name, a.content, a.tokens, a.created,
            {segment_sql} as segment
        FROM archive a
        {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    cursor.execute(sql, params)

    pieces = []
    for row in cursor.fetchall():
        piece = {
            "id": row["rid"],
            "conversation_id": row["conv"],
            "role": row["role"],
            "content": row["content"],
            "segment": row["segment"],
            "message_index": row["idx"],
            "created_at": _epoch_to_iso(row["created"]),
            "tokens": row["tokens"],
            "name": row["name"],
        }
        pieces.append(piece)

    return pieces


def _count_pieces_from_archive(
    conn: sqlite3.Connection,
    conversation_id: Optional[str] = None,
    role: Optional[str] = None,
    seg: Optional[int] = None,
    from_msg: Optional[int] = None,
    to_msg: Optional[int] = None,
    query: Optional[str] = None,
    queries: Optional[list[str]] = None,
    path: Optional[str] = None,
) -> int:
    """Count total matching pieces for pagination."""
    cursor = conn.cursor()

    # Check if archive table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archive'")
    if not cursor.fetchone():
        return 0

    has_fts, has_segments, has_fileops, search_terms = _get_archive_table_info(conn, query, queries)

    where_sql, params = _build_pieces_where_clauses(
        conversation_id=conversation_id,
        role=role,
        seg=seg,
        from_msg=from_msg,
        to_msg=to_msg,
        search_terms=search_terms,
        path=path,
        has_fts=has_fts,
        has_segments=has_segments,
        has_fileops=has_fileops,
    )

    sql = f"SELECT COUNT(*) as total FROM archive a {where_sql}"
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return row["total"] if row else 0


def _get_piece_by_id(conn: sqlite3.Connection, piece_id: str) -> Optional[dict]:
    """Get a single piece by rid from the archive table."""
    cursor = conn.cursor()

    # Check if archive table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archive'")
    if not cursor.fetchone():
        return None

    # Check if segments table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='segments'")
    has_segments = cursor.fetchone() is not None

    if has_segments:
        segment_sql = """
            COALESCE((
                SELECT s.seg FROM segments s
                WHERE s.conv = a.conv
                AND s.cut_idx <= a.idx
                ORDER BY s.cut_idx DESC LIMIT 1
            ), 1)
        """
    else:
        segment_sql = "1"

    cursor.execute(f"""
        SELECT
            a.rid, a.conv, a.idx, a.role, a.name, a.content, a.tokens, a.created,
            {segment_sql} as segment
        FROM archive a
        WHERE a.rid = ?
    """, (piece_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row["rid"],
        "conversation_id": row["conv"],
        "role": row["role"],
        "content": row["content"],
        "segment": row["segment"],
        "message_index": row["idx"],
        "created_at": _epoch_to_iso(row["created"]),
        "tokens": row["tokens"],
        "name": row["name"],
    }


def _get_oldest_piece(
    conn: sqlite3.Connection,
    conversation_id: Optional[str] = None,
    role: Optional[str] = None,
    seg: Optional[int] = None,
    from_msg: Optional[int] = None,
    to_msg: Optional[int] = None,
    query: Optional[str] = None,
    queries: Optional[list[str]] = None,
    path: Optional[str] = None,
) -> Optional[dict]:
    """Get the single oldest piece matching the filters (for mode=first)."""
    cursor = conn.cursor()

    # Check if archive table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archive'")
    if not cursor.fetchone():
        return None

    has_fts, has_segments, has_fileops, search_terms = _get_archive_table_info(conn, query, queries)

    where_sql, params = _build_pieces_where_clauses(
        conversation_id=conversation_id,
        role=role,
        seg=seg,
        from_msg=from_msg,
        to_msg=to_msg,
        search_terms=search_terms,
        path=path,
        has_fts=has_fts,
        has_segments=has_segments,
        has_fileops=has_fileops,
    )

    if has_segments:
        segment_sql = """
            COALESCE((
                SELECT s.seg FROM segments s
                WHERE s.conv = a.conv
                AND s.cut_idx <= a.idx
                ORDER BY s.cut_idx DESC LIMIT 1
            ), 1)
        """
    else:
        segment_sql = "1"

    sql = f"""
        SELECT
            a.rid, a.conv, a.idx, a.role, a.name, a.content, a.tokens, a.created,
            {segment_sql} as segment
        FROM archive a
        {where_sql}
        ORDER BY (a.created IS NULL), a.created ASC, a.idx ASC, a.rid ASC
        LIMIT 1
    """
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "id": row["rid"],
        "conversation_id": row["conv"],
        "role": row["role"],
        "content": row["content"],
        "segment": row["segment"],
        "message_index": row["idx"],
        "created_at": _epoch_to_iso(row["created"]),
        "tokens": row["tokens"],
        "name": row["name"],
    }


@archive_bp.route("/api/proxy/archive/conversations", methods=["GET"])
def api_archive_conversations():
    """List conversations from mnemonic-archive.

    Query parameters:
    - model: Model name (required)
    - limit: Maximum number of conversations to return (default: 50, max: 1000)
    - offset: Number of conversations to skip for pagination (default: 0)
    - sort: Sort order - 'updated_desc' (default), 'updated_asc', 'created_desc', 'created_asc', 'message_count_desc', 'message_count_asc'
    - query: Search query to filter conversations (optional)
    """
    _validate_local_mode()

    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model parameter"}), 400

    limit = _validate_limit(request.args.get("limit", 50, type=int))
    offset = _validate_offset(request.args.get("offset", 0, type=int))
    sort = request.args.get("sort", "updated_desc").strip()
    query = request.args.get("query", "").strip() or None

    # Verify model exists
    model_config = _get_model_config(model)
    if not model_config:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    # Get proxy manager (archive data exists per-model, proxy doesn't need to be running)
    proxy_manager = _get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "Failed to create proxy manager"}), 500

    # Find archive database
    archive_db = _find_archive_db(proxy_manager)
    if not archive_db or not archive_db.exists():
        return jsonify({
            "conversations": [],
            "count": 0,
            "total": 0,
            "model": model,
            "offset": offset,
            "limit": limit,
            "sort": sort,
        })

    # Query conversations from archive.sqlite (real schema)
    conversations = []
    total = 0
    conn = None
    try:
        conn = sqlite3.connect(str(archive_db))
        conn.row_factory = sqlite3.Row
        conversations, total = _query_conversations_from_archive(conn, limit, offset, sort, query)
    except Exception as e:
        log.error(f"Failed to query conversations for {model}: {e}")
        return jsonify({"error": f"Failed to query conversations: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()

    return jsonify({
        "conversations": conversations,
        "count": len(conversations),
        "total": total,
        "model": model,
        "offset": offset,
        "limit": limit,
        "sort": sort,
    })


@archive_bp.route("/api/proxy/archive/journal", methods=["GET"])
def api_archive_journal():
    """Fetch journal events from mnemonic-archive.

    Query parameters:
    - model: Model name (required)
    - limit: Maximum number of events to return (default: 100, max: 1000)
    - event: Filter by event type (e.g., 'request', 'recall', 'auto_recall', 'new_conversation') (optional)
    - conversation_id: Filter by conversation ID (optional)
    - offset: Number of events to skip (default: 0)
    """
    _validate_local_mode()

    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model parameter"}), 400

    limit = _validate_limit(request.args.get("limit", 100, type=int))
    event_filter = request.args.get("event", "").strip() or None
    event_type_filter = request.args.get("event_type", "").strip() or None
    conversation_id = request.args.get("conversation_id", "").strip() or None
    from_timestamp = request.args.get("from_timestamp", "").strip() or None
    to_timestamp = request.args.get("to_timestamp", "").strip() or None
    offset = _validate_offset(request.args.get("offset", 0, type=int))

    # Verify model exists
    model_config = _get_model_config(model)
    if not model_config:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    # Get proxy manager
    proxy_manager = _get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "Failed to create proxy manager"}), 500

    # Find journal file
    journal_path = _find_journal_file(proxy_manager)
    if not journal_path or not journal_path.exists():
        return jsonify({
            "events": [],
            "count": 0,
            "total": 0,
            "model": model,
            "offset": offset,
            "limit": limit,
        })

    # Parse journal.jsonl - read from end for efficiency
    max_collect = offset + limit
    events = []
    total = 0
    try:
        with open(journal_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return jsonify({
                    "events": [],
                    "count": 0,
                    "total": 0,
                    "model": model,
                    "offset": offset,
                    "limit": limit,
                })

            chunk_size = 8192
            position = file_size
            buffer = b""

            while position > 0 and len(events) < max_collect:
                read_size = min(chunk_size, position)
                position -= read_size
                f.seek(position)
                chunk = f.read(read_size)
                buffer = chunk + buffer

                lines = buffer.split(b"\n")
                buffer = lines[0]
                for line in reversed(lines[1:]):
                    if not line:
                        continue
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue

                    # Apply filters - real journal uses 'conv' field for conversation ID
                    if conversation_id and event.get("conv") != conversation_id:
                        continue
                    if event_filter and event.get("event") != event_filter:
                        continue
                    if event_type_filter and event.get("event") != event_type_filter:
                        continue
                    if from_timestamp or to_timestamp:
                        event_ts = event.get("ts")
                        if event_ts is not None:
                            event_ts_iso = _epoch_to_iso(event_ts)
                            if from_timestamp and event_ts_iso < from_timestamp:
                                continue
                            if to_timestamp and event_ts_iso > to_timestamp:
                                continue

                    events.append(event)
                    if len(events) >= max_collect:
                        break

            if buffer and len(events) < max_collect:
                try:
                    event = json.loads(buffer.decode("utf-8"))
                    if (not conversation_id or event.get("conv") == conversation_id) and \
                       (not event_filter or event.get("event") == event_filter) and \
                       (not event_type_filter or event.get("event") == event_type_filter) and \
                       (not from_timestamp or not to_timestamp or
                        (event.get("ts") is not None and
                         (_epoch_to_iso(event.get("ts")) >= from_timestamp if from_timestamp else True) and
                         (_epoch_to_iso(event.get("ts")) <= to_timestamp if to_timestamp else True))):
                        events.append(event)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

    except Exception as e:
        log.error(f"Failed to parse journal for {model}: {e}")
        return jsonify({"error": f"Failed to parse journal: {str(e)}"}), 500

    # Normalize journal records to match frontend expected shape:
    # frontend expects: id, event_type, timestamp, conversation_id, data
    # backend records have: ts, event, conv, + other flat fields
    normalized = []
    for event in events:
        normalized_event = {
            "id": event.get("rid", event.get("id", "")),
            "event_type": event.get("event", "unknown"),
            "timestamp": _epoch_to_iso(event.get("ts")),
            "conversation_id": event.get("conv", ""),
            "data": {k: v for k, v in event.items() if k not in ("ts", "event", "conv", "rid", "id")},
        }
        normalized.append(normalized_event)

    total = len(normalized)
    events = normalized[offset:offset + limit]

    return jsonify({
        "events": events,
        "count": total,
        "total": total,
        "model": model,
        "offset": offset,
        "limit": limit,
    })


@archive_bp.route("/api/proxy/archive/recall", methods=["GET"])
def api_archive_recall():
    """Search/recall from mnemonic-archive.

    Query parameters:
    - model: Model name (required)
    - query: Search query string (required unless id or path provided)
    - queries: Comma-separated list of alternative query strings (optional)
    - id: Recall a specific piece by ID (rid) (optional)
    - path: File path to search history for (optional)
    - mode: Recall mode - 'timeline' (default) or 'first' (return oldest match)
    - role: Filter by role (e.g., 'user', 'assistant', 'system', 'assistant-reasoning', 'assistant-tool-args') (optional)
    - seg: Filter by segment number (optional, positive integer)
    - from: Start message number (optional, positive integer)
    - to: End message number (optional, positive integer)
    - offset: Result offset for pagination (default: 0)
    - limit: Maximum results to return (default: 20, max: 100)
    - conversation_id: Filter by conversation ID (optional)
    - order: Sort order - 'pertinenza' (relevance) or 'tempo' (time) (default: 'pertinenza')
    """
    _validate_local_mode()

    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model parameter"}), 400

    # Verify model exists
    model_config = _get_model_config(model)
    if not model_config:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    # Get proxy manager
    proxy_manager = _get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "Failed to create proxy manager"}), 500

    # Parse and validate query parameters
    query = request.args.get("query", "").strip() or None
    queries_param = request.args.get("queries", "").strip()
    # Filter out empty/whitespace-only terms
    queries = [q.strip() for q in queries_param.split(",") if q.strip()] if queries_param else None
    id_param = request.args.get("id", "").strip() or None
    path = request.args.get("path", "").strip() or None
    mode = request.args.get("mode", "timeline").strip()
    # Accept 'default' as alias for 'timeline' (frontend default)
    if mode == "default":
        mode = "timeline"
    role = request.args.get("role", "").strip() or None
    seg = _validate_positive_int(request.args.get("seg", type=int))
    from_msg = _validate_positive_int(request.args.get("from", type=int))
    to_msg = _validate_positive_int(request.args.get("to", type=int))
    offset = _validate_offset(request.args.get("offset", 0, type=int))
    limit = _validate_limit(request.args.get("limit", 20, type=int), max_limit=100)
    conversation_id = request.args.get("conversation_id", "").strip() or None
    order = request.args.get("order", "pertinenza").strip()

    # Validate mode
    valid_modes = ("timeline", "first")
    if mode not in valid_modes:
        return jsonify({"error": f"Invalid mode: {mode}. Supported modes: {valid_modes}"}), 400

    # Validate order
    if order not in ("pertinenza", "tempo"):
        return jsonify({"error": f"Invalid order: {order}. Valid orders: pertinenza, tempo"}), 400

    # Role is passed through without whitelist - real archive has roles like assistant-reasoning, assistant-tool-args

    # Validate from/to ordering
    if from_msg is not None and to_msg is not None and from_msg > to_msg:
        return jsonify({"error": "Parameter 'from' must be <= 'to'"}), 400

    # Find archive database
    archive_db = _find_archive_db(proxy_manager)
    if not archive_db or not archive_db.exists():
        return jsonify({
            "pieces": [],
            "total": 0,
            "recall": {
                "results": [],
                "total": 0,
            },
            "model": model,
            "message": "No archive database found",
        })

    # Query pieces from archive table (real schema)
    conn = None
    try:
        conn = sqlite3.connect(str(archive_db))
        conn.row_factory = sqlite3.Row

        # If id_param is provided, do a direct lookup
        if id_param:
            piece = _get_piece_by_id(conn, id_param)
            if piece:
                if conversation_id and piece["conversation_id"] != conversation_id:
                    results = []
                    total = 0
                else:
                    results = [piece]
                    total = 1
            else:
                results = []
                total = 0
        elif mode == "first":
            # For mode=first, get the true oldest match using a dedicated query
            piece = _get_oldest_piece(
                conn=conn,
                conversation_id=conversation_id,
                role=role,
                seg=seg,
                from_msg=from_msg,
                to_msg=to_msg,
                query=query,
                queries=queries,
                path=path,
            )
            if piece:
                results = [piece]
                total = 1
            else:
                results = []
                total = 0
        else:
            # Get total count for pagination
            total = _count_pieces_from_archive(
                conn=conn,
                conversation_id=conversation_id,
                role=role,
                seg=seg,
                from_msg=from_msg,
                to_msg=to_msg,
                query=query,
                queries=queries,
                path=path,
            )
            # Fetch page with offset using SQLite OFFSET clause
            results = _query_pieces_from_archive(
                conn=conn,
                conversation_id=conversation_id,
                role=role,
                seg=seg,
                from_msg=from_msg,
                to_msg=to_msg,
                query=query,
                queries=queries,
                order=order,
                limit=limit,
                offset=offset,
                path=path,
            )
            conn.close()

        return jsonify({
            "pieces": results,
            "total": total,
            "recall": {
                "results": results,
                "total": total,
            },
            "query": query,
            "queries": queries,
            "mode": mode,
            "order": order,
            "model": model,
        })

    except Exception as e:
        log.error(f"Failed to recall from archive for {model}: {e}")
        return jsonify({"error": f"Failed to recall from archive: {str(e)}"}), 500


@archive_bp.route("/api/proxy/archive/piece/<piece_id>", methods=["GET"])
def api_archive_piece(piece_id):
    """Get a single piece by ID (rid) from mnemonic-archive.

    Query parameters:
    - model: Model name (required)
    """
    _validate_local_mode()

    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model parameter"}), 400

    # Verify model exists
    model_config = _get_model_config(model)
    if not model_config:
        return jsonify({"error": f"Unknown model '{model}'"}), 404

    # Get proxy manager
    proxy_manager = _get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "Failed to create proxy manager"}), 500

    # Find archive database
    archive_db = _find_archive_db(proxy_manager)
    if not archive_db or not archive_db.exists():
        return jsonify({"error": "No archive database found"}), 404

    conn = None
    try:
        conn = sqlite3.connect(str(archive_db))
        conn.row_factory = sqlite3.Row
        piece = _get_piece_by_id(conn, piece_id)

        if not piece:
            return jsonify({"error": f"Piece not found: {piece_id}"}), 404

        piece["model"] = model
        return jsonify(piece)

    except Exception as e:
        log.error(f"Failed to fetch piece {piece_id} for {model}: {e}")
        return jsonify({"error": f"Failed to fetch piece: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()
