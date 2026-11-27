# Remote Mode Removal Plan

## Files to Modify

### 1. webapp/config.py
- Remove `Config` class remote mode properties: `_remote_mode`, `_remote_url`, `is_remote_mode()`, `remote_url()`
- Remove `REMOTE_MODE` env var handling
- Keep only local mode configuration

### 2. webapp/models.py
- Move `Model` dataclass and `from_dict()` from `remote_client.py` to here
- Remove `RemoteModelRegistry` import and usage
- Remove all `if self._config.is_remote_mode:` checks
- Simplify `ModelRegistry` to only handle local mode
- Remove `RemoteModelRegistry` import

### 3. webapp/remote_client.py
- **DELETE THIS FILE** - All remote mode specific code removed
- The `Model` dataclass moved to `models.py`

### 4. webapp/config.py
- Remove `Config` class remote mode properties (`_remote_mode`, `_remote_url`, `is_remote_mode()`, `remote_url()`)
- Remove `REMOTE_MODE` env var handling
- Keep only local mode configuration

### 4. webapp/routes/lifecycle.py
- Remove remote mode checks in start/stop endpoints
- Simplify start/stop logic for local mode only

### 5. webapp/routes/status.py
- Remove remote mode branches in status endpoints
- Simplify to only use local mode logic

### 5. webapp/routes/chat.py
- Remove remote mode check in chat endpoint

### 6. webapp/routes/status.py
- Remove remote mode branches in status endpoints
- Remove remote mode specific status logic

### 6. webapp/scheduler.py
- Remove remote mode checks

### 7. webapp/main.py
- Remove remote mode logging

### 8. webapp/remote_client.py
- **DELETE THIS FILE** - Move `Model` dataclass to `models.py`

### 9. Tests
- Remove remote mode test files: `test_integration.py`
- Remove remote mode test cases from `test_routes.py`, `test_archive_routes.py`, `test_config.py`

## Implementation Order

1. Move `Model` dataclass from `remote_client.py` to `models.py`
2. Update `models.py` to use local `Model` class
3. Remove `remote_client.py`
4. Remove remote mode from `config.py` (`Config` class)
7. Update `models.py` - remove `RemoteModelRegistry` and remote mode logic
8. Update `routes/lifecycle.py` - remove remote mode branches
9. Update `routes/status.py` - remove remote mode branches
10. Update `routes/chat.py` - remove remote mode check
11. Update `routes/status.py` - remove remote mode branches
12. Update `scheduler.py` - remove remote mode checks
13. Update `main.py` - remove remote mode logging
14. Delete `remote_client.py`
15. Update tests