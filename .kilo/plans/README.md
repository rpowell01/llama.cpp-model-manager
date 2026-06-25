# Remote Integration Design Documents

This directory contains the architecture analysis and design specifications for implementing remote mode integration in the llama.cpp web frontend.

## Documents

### 1. Architecture Analysis ([architecture-analysis.md](architecture-analysis.md))
Current vs target architecture, process flow diagrams, and design decisions.

### 2. Process Management Call Sites ([call-sites.md](call-sites.md))
Complete inventory of all function calls to `processes.py` with file:line references, call graphs, and migration impact simulation.

### 3. Remote Client Specification ([remote_client-spec.md](remote_client-spec.md))
Interface definition for `remote_client.py`, including function signatures, error handling, retry logic, and local fallback mechanism.

### 4. Migration Plan ([migration-plan.md](migration-plan.md))
Step-by-step implementation guide, code changes for each route file, REST API contract, environment variables, testing strategy, risks, and rollback.

## Summary

**Goal:** Enable the Flask web UI to manage llama-server instances on a remote machine via REST API, while preserving existing local-mode behavior.

**Key Components:**
- `remote_client.py` - abstraction layer
- Remote server Flask app (separate implementation)
- Environment variables: `REMOTE_MODE`, `LLAMA_CPP_SERVER_URL`

**Approach:** Conditional delegation; all process management calls go through `remote_client`, which routes to either local `processes` functions or remote HTTP API.

**Status:** Design complete, ready for code review before implementation.

---

## Quick Reference: Process Management Functions

| Function | Purpose | Local Impl | Remote API |
|----------|---------|------------|------------|
| `find_pid_by_port(port)` | Get PID for port | OS scan | GET `/api/status?port` |
| `pids_of_llama()` | List all PIDs | OS scan | GET `/api/status` |
| `_kill_tree(pid, model)` | Stop model | OS signals | POST `/api/models/unload` |
| `check_port_active(port)` | Port listening? | socket connect | GET `/api/status?port` |
| `_check_model_ready(port)` | Model loaded? | HTTP `/v1/models` | GET `/api/status?port` |
| `build_cmd()` | Build command | returns `list[str]` | error (unused) |
| `start_model()` | Start model | N/A (inline) | POST `/api/models/load` |

---

## Call Site Distribution

- `routes/lifecycle.py`: 12 usages (start/stop logic)
- `routes/status.py`: 8 usages (status queries)
- `routes/chat.py`: 1 usage (port check)
- **Total:** 21 call sites to be redirected through `remote_client`

---

## Next Steps After Approval

1. Implement `remote_client.py` per spec
2. Update route imports and conditional logic
3. Create remote_server.py (separate bead)
4. Write tests
5. Code review & merge
