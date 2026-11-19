"""Mnemonic Archive API endpoints for conversation history, journal events, and recall."""
import json
import os
import sqlite3
import logging
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
    return text.replace("%", "\\%").replace("_", "\\_")


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
            "success": True,
            "data": {
                "conversations": [],
                "total": 0,
            },
            "model": model,
            "offset": offset,
            "limit": limit,
            "sort": sort,
        })

    # Query conversations from archive.sqlite
    conversations = []
    total = 0
    try:
        import sqlite3
        conn = sqlite3.connect(str(archive_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if conversations table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'
        """)
        if not cursor.fetchone():
            conn.close()
            return jsonify({"conversations": [], "count": 0, "total": 0, "model": model, "offset": offset, "limit": limit})

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

        # Build WHERE clause for query filtering
        where_clause = ""
        params = []
        if query:
            where_clause = "WHERE conversation_id LIKE ? ESCAPE '\\' OR metadata LIKE ? ESCAPE '\\'"
            like_query = f"%{_escape_like_wildcards(query)}%"
            params = [like_query, like_query]

        # Get total count for pagination
        count_sql = f"SELECT COUNT(*) as total FROM conversations {where_clause}"
        cursor.execute(count_sql, params)
        total_row = cursor.fetchone()
        total = total_row["total"] if total_row else 0

        # Build query with pagination
        sql = f"""
            SELECT conversation_id, created_at, updated_at, message_count, token_count, metadata
            FROM conversations
            {where_clause}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """
        params_with_pagination = params + [limit, offset]
        cursor.execute(sql, params_with_pagination)

        for row in cursor.fetchall():
            conv = {
                "id": row["conversation_id"],  # Frontend expects 'id' field
                "conversation_id": row["conversation_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": row["message_count"],
                "token_count": row["token_count"],
            }
            # Parse metadata if present
            if row["metadata"]:
                try:
                    conv["metadata"] = json.loads(row["metadata"])
                    # Extract segment_count from metadata if available
                    meta = conv["metadata"]
                    if isinstance(meta, dict) and "segment_count" in meta:
                        conv["segment_count"] = meta["segment_count"]
                except json.JSONDecodeError:
                    conv["metadata"] = {}
            conversations.append(conv)

        conn.close()
    except Exception as e:
        log.error(f"Failed to query conversations for {model}: {e}")
        return jsonify({"error": f"Failed to query conversations: {str(e)}"}), 500

    return jsonify({
        "success": True,
        "data": {
            "conversations": conversations,
            "total": len(conversations),
        },
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
    - event: Filter by event type (e.g., 'request', 'recall', 'recall_use') (optional)
    - conversation_id: Filter by conversation ID (optional)
    - offset: Number of events to skip (default: 0)
    """
    _validate_local_mode()

    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"error": "Missing model parameter"}), 400

    limit = _validate_limit(request.args.get("limit", 100, type=int))
    event_filter = request.args.get("event", "").strip() or None
    conversation_id = request.args.get("conversation_id", "").strip() or None
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
            "success": True,
            "data": {
                "events": [],
                "total": 0,
            },
            "model": model,
            "offset": offset,
            "limit": limit,
        })

    # Parse journal.jsonl - read from end for efficiency
    # We need to collect (offset + limit) matching events from the end
    max_collect = offset + limit
    events = []
    total = 0
    try:
        # Read file in reverse to get most recent events first
        with open(journal_path, "rb") as f:
            f.seek(0, 2)  # Seek to end
            file_size = f.tell()
            if file_size == 0:
                return jsonify({
                    "success": True,
                    "data": {
                        "events": [],
                        "total": 0,
                    },
                    "model": model,
                    "offset": offset,
                    "limit": limit,
                })

            # Read chunks from end
            chunk_size = 8192
            position = file_size
            buffer = b""
            lines_buffer = []

            while position > 0 and len(events) < max_collect:
                read_size = min(chunk_size, position)
                position -= read_size
                f.seek(position)
                chunk = f.read(read_size)
                buffer = chunk + buffer

                # Split into lines
                lines = buffer.split(b"\n")
                # Keep the first (potentially incomplete) line in buffer
                buffer = lines[0]
                # Process complete lines in reverse order (most recent first)
                for line in reversed(lines[1:]):
                    if not line:
                        continue
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue

                    # Apply filters
                    if conversation_id and event.get("conv") != conversation_id:
                        continue
                    if event_filter and event.get("event") != event_filter:
                        continue

                    events.append(event)
                    if len(events) >= max_collect:
                        break

            # Process any remaining line in buffer
            if buffer and len(events) < max_collect:
                try:
                    event = json.loads(buffer.decode("utf-8"))
                    if (not conversation_id or event.get("conv") == conversation_id) and \
                       (not event_filter or event.get("event") == event_filter):
                        events.append(event)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

    except Exception as e:
        log.error(f"Failed to parse journal for {model}: {e}")
        return jsonify({"error": f"Failed to parse journal: {str(e)}"}), 500

    # Now we have up to (offset + limit) most recent matching events
    # Apply offset (skip the first 'offset' events which are the most recent)
    total = len(events)  # This is actually the count of matching events we found, not total in file
    events = events[offset:offset + limit]

    return jsonify({
        "success": True,
        "data": {
            "events": events,
            "total": total,
        },
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
    - id: Recall a specific piece by ID (optional)
    - path: File path to search history for (optional)
    - mode: Recall mode - 'timeline' (default) or 'first' (return oldest match)
    - role: Filter by role - 'user', 'assistant', 'reasoning', 'args', 'tool' (optional)
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
    queries = [q.strip() for q in queries_param.split(",")] if queries_param else None
    id_param = request.args.get("id", "").strip() or None
    path = request.args.get("path", "").strip() or None
    mode = request.args.get("mode", "timeline").strip()
    role = request.args.get("role", "").strip() or None
    seg = _validate_positive_int(request.args.get("seg", type=int))
    from_msg = _validate_positive_int(request.args.get("from", type=int))
    to_msg = _validate_positive_int(request.args.get("to", type=int))
    offset = _validate_offset(request.args.get("offset", 0, type=int))
    limit = _validate_limit(request.args.get("limit", 20, type=int), max_limit=100)
    conversation_id = request.args.get("conversation_id", "").strip() or None
    order = request.args.get("order", "pertinenza").strip()

    # Validate mode - only timeline and first are implemented
    valid_modes = ("timeline", "first")
    if mode not in valid_modes:
        return jsonify({"error": f"Invalid mode: {mode}. Supported modes: {valid_modes}"}), 400

    # Validate order
    if order not in ("pertinenza", "tempo"):
        return jsonify({"error": f"Invalid order: {order}. Valid orders: pertinenza, tempo"}), 400

    # Validate role
    if role and role not in ("user", "assistant", "reasoning", "args", "tool"):
        return jsonify({"error": f"Invalid role: {role}. Valid roles: user, assistant, reasoning, args, tool"}), 400

    # Validate from/to ordering
    if from_msg is not None and to_msg is not None and from_msg > to_msg:
        return jsonify({"error": "Parameter 'from' must be <= 'to'"}), 400

    # Find archive database
    proxy_manager = _get_proxy_manager(model)
    if not proxy_manager:
        return jsonify({"error": "Failed to create proxy manager"}), 500

    archive_db = _find_archive_db(proxy_manager)
    if not archive_db or not archive_db.exists():
        return jsonify({
            "pieces": [],
            "total": 0,
            "model": model,
            "message": "No archive database found",
        })

    # For the recall endpoint, we need to query the archive database
    # The mnemonic-archive stores conversation pieces with embeddings for search
    # We'll implement a basic text search for now, with structure matching the MCP tool
    try:
        import sqlite3
        conn = sqlite3.connect(str(archive_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        results = []

        if "pieces" in tables:
            # Build query based on parameters
            if id_param:
                # Recall by specific ID
                sql = """
                    SELECT id, conversation_id, role, content, segment, message_index,
                           created_at, metadata, embedding
                    FROM pieces
                    WHERE id = ?
                """
                params = [id_param]
                if conversation_id:
                    sql += " AND conversation_id = ?"
                    params.append(conversation_id)
                cursor.execute(sql, params)
                rows = cursor.fetchall()
            elif path:
                # Search by file path history
                sql = """
                    SELECT id, conversation_id, role, content, segment, message_index,
                           created_at, metadata, embedding
                    FROM pieces
                    WHERE metadata LIKE ? ESCAPE '\\'
                """
                like_path = f"%{_escape_like_wildcards(path)}%"
                params = [like_path]
                if conversation_id:
                    sql += " AND conversation_id = ?"
                    params.append(conversation_id)
                if role:
                    sql += " AND role = ?"
                    params.append(role)
                if seg is not None:
                    sql += " AND segment = ?"
                    params.append(seg)
                sql += " ORDER BY created_at DESC LIMIT ?"
                params.append(limit)
                cursor.execute(sql, params)
                rows = cursor.fetchall()
            elif query or queries:
                # Text search (basic LIKE for now; full-text search would need FTS5)
                search_terms = []
                if query:
                    search_terms.append(query)
                if queries:
                    search_terms.extend(queries)

                # Build WHERE clause with OR for multiple search terms
                where_clauses = []
                params = []
                for term in search_terms:
                    escaped_term = _escape_like_wildcards(term)
                    where_clauses.append("(content LIKE ? ESCAPE '\\' OR metadata LIKE ? ESCAPE '\\')")
                    like_term = f"%{escaped_term}%"
                    params.extend([like_term, like_term])

                sql = f"""
                    SELECT id, conversation_id, role, content, segment, message_index,
                           created_at, metadata, embedding
                    FROM pieces
                    WHERE {' OR '.join(where_clauses)}
                """
                if conversation_id:
                    sql += " AND conversation_id = ?"
                    params.append(conversation_id)
                if role:
                    sql += " AND role = ?"
                    params.append(role)
                if seg is not None:
                    sql += " AND segment = ?"
                    params.append(seg)
                if from_msg is not None:
                    sql += " AND message_index >= ?"
                    params.append(from_msg)
                if to_msg is not None:
                    sql += " AND message_index <= ?"
                    params.append(to_msg)

                # Order by relevance (simple: matches in content first) or time
                if order == "tempo":
                    sql += " ORDER BY created_at DESC"
                else:
                    # Simple relevance: prioritize content matches over metadata
                    first_escaped = _escape_like_wildcards(search_terms[0]) if search_terms else ""
                    sql += " ORDER BY (CASE WHEN content LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END), created_at DESC"
                    if search_terms:
                        params.append(f"%{first_escaped}%")

                sql += " LIMIT ?"
                params.append(limit)
                cursor.execute(sql, params)
                rows = cursor.fetchall()
            else:
                # No query provided, return recent pieces
                sql = """
                    SELECT id, conversation_id, role, content, segment, message_index,
                           created_at, metadata, embedding
                    FROM pieces
                    WHERE 1=1
                """
                params = []
                if conversation_id:
                    sql += " AND conversation_id = ?"
                    params.append(conversation_id)
                if role:
                    sql += " AND role = ?"
                    params.append(role)
                if seg is not None:
                    sql += " AND segment = ?"
                    params.append(seg)
                if from_msg is not None:
                    sql += " AND message_index >= ?"
                    params.append(from_msg)
                if to_msg is not None:
                    sql += " AND message_index <= ?"
                    params.append(to_msg)
                sql += " ORDER BY created_at DESC LIMIT ?"
                params.append(limit)
                cursor.execute(sql, params)
                rows = cursor.fetchall()

            for row in rows:
                piece = {
                    "id": row["id"],
                    "conversation_id": row["conversation_id"],
                    "role": row["role"],
                    "content": row["content"],
                    "segment": row["segment"],
                    "message_index": row["message_index"],
                    "created_at": row["created_at"],
                }
                if row["metadata"]:
                    try:
                        piece["metadata"] = json.loads(row["metadata"])
                    except json.JSONDecodeError:
                        piece["metadata"] = {}
                results.append(piece)

        conn.close()

        # Apply mode-specific processing
        if mode == "first" and results:
            # Return only the first (oldest) match - results are ordered by created_at DESC
            # so the oldest is at the end
            results = [results[-1]] if results else []

        total = len(results)

        return jsonify({
            "pieces": results,
            "total": total,
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
    """Get a single piece by ID from mnemonic-archive.

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

    try:
        import sqlite3
        conn = sqlite3.connect(str(archive_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if pieces table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='pieces'
        """)
        if not cursor.fetchone():
            conn.close()
            return jsonify({"error": "Pieces table not found"}), 404

        # Get column names from pieces table to handle schema variations
        cursor.execute("PRAGMA table_info(pieces)")
        columns = {row["name"] for row in cursor.fetchall()}

        # Build SELECT with only existing columns
        # Skip 'embedding' since it's a BLOB (bytes) not JSON serializable and frontend doesn't use it
        base_columns = ["id", "conversation_id", "role", "content", "segment", "message_index", "created_at", "metadata"]
        optional_columns = ["request_context", "recall_use", "output", "reasoning", "embedding"]
        select_columns = [c for c in base_columns if c in columns] + [c for c in optional_columns if c in columns]

        sql = f"SELECT {', '.join(select_columns)} FROM pieces WHERE id = ?"
        cursor.execute(sql, (piece_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({"error": f"Piece not found: {piece_id}"}), 404

        # Build piece object with all available fields
        piece = {"model": model}
        for col in select_columns:
            value = row[col]
            if col == "metadata" and value:
                try:
                    piece[col] = json.loads(value)
                except json.JSONDecodeError:
                    piece[col] = {}
            elif col in ("request_context", "recall_use", "output", "reasoning") and value:
                try:
                    piece[col] = json.loads(value)
                except json.JSONDecodeError:
                    piece[col] = value
            elif col == "embedding" and value:
                # Embedding is a BLOB - convert to base64 for JSON serialization if needed
                # Frontend doesn't use it, so we can skip or include as base64
                import base64
                piece[col] = base64.b64encode(value).decode("ascii")
            else:
                piece[col] = value

        return jsonify(piece)

    except Exception as e:
        log.error(f"Failed to fetch piece {piece_id} for {model}: {e}")
        return jsonify({"error": f"Failed to fetch piece: {str(e)}"}), 500
