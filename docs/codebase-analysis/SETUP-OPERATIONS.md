# Setup, Operations & Gaps

## Local Setup

### Prerequisites
- Python 3.10+
- `llama-server` binary (from llama.cpp) available in PATH or configured
- Optional: `mnemonic-proxy` for context management features

### Installation
```bash
pip install -r requirements.txt
```

**Dependencies:**
| Package | Purpose |
|---------|---------|
| `flask>=3.0.0` | Web framework |
| `requests>=2.31.0` | HTTP client for proxy/chat |
| `urllib3>=2.0.0` | HTTP utilities |
| `python-dotenv>=1.0.0` | Environment variable loading |
| `gpu-detector` | GPU detection |
| `psutil>=5.9.0` | Process/system monitoring |
| `gguf>=0.10.0` | GGUF model file handling |

### Configuration

Configuration is managed through `webapp/config.py` and environment variables:

**Key Environment Variables:**
- `LLAMA_MODEL_PATH` - Default path to models directory
- `LLAMA_SERVER_PATH` - Path to llama-server binary (if not in PATH)
- `HOST` / `PORT` - Server bind address and port

**Configurable Parameters (in `config.py`):**
- `DEFAULT_PORT_RANGE` - Port range for auto-assigned model ports
- `METRICS_EXPORT_INTERVAL` - Seconds between metrics exports
- `LOG_LEVEL` - Logging verbosity

### Running the Application
```bash
cd webapp
python main.py
# or
flask run --host=0.0.0.0 --port=5000
```

## Operations

### Model Management
- Models are registered via the UI or API (`POST /api/models`)
- Each model runs as a separate `llama-server` process
- Ports are auto-assigned from a configurable range
- Model state (running/stopped) is tracked in memory

### Logging & Monitoring
- Real-time logs streamed via SSE to the frontend
- Metrics scraped from each running llama-server instance
- Background scheduler exports metrics periodically

### Archive Browser
- Provides access to mnemonic-proxy journal/archive data
- Endpoints under `/api/archive/*`
- Only available for running models with mnemonic-proxy

## Testing

### Test Suite Location
Tests are located in `/tests/` directory.

**Test Files Found:**
- `test_strata_context_route.py` - Context/proxy route tests
- (Additional test files present but not fully enumerated)

### Running Tests
```bash
pytest tests/
# or
python -m pytest
```

## Technical Debt & Gaps

### Missing Tests
- **No comprehensive unit tests** for core modules (`processes.py`, `models.py`, `state.py`)
- **No integration tests** for API endpoints
- Test coverage appears limited to specific route functionality

### Ambiguities / Inferred Points
1. **Persistence**: Model registry appears to be in-memory only. It's unclear if model registrations persist across restarts (config.py suggests some persistence via JSON config files, but this needs verification).
2. **Error Handling**: Error handling paths in `processes.py` and `mnemonic_proxy.py` are not fully documented - particularly around process failure recovery.
3. **Concurrency**: The `state.py` module uses threading for SSE broadcasting. Race conditions in log buffer management are not explicitly guarded.
4. **WSL Support**: The codebase has WSL-specific path handling (see `config.py`), but the full scope of WSL compatibility is not documented.

### Known Issues (from project memory)
- "Hide Model" checkbox functionality is broken (see `corrections.md`)
- Proxy health badge `/api/proxy/archive/journal/<model>` returns 404 errors for some models
- Duplicate archive button on model tiles (constraint: single archive button per tile)

### Potential Improvements
1. Add unit tests for process management and model registry
2. Implement persistent model configuration storage
3. Add proper error handling and recovery for failed llama-server processes
4. Document the WSL-specific workarounds and their rationale
5. Add API documentation (OpenAPI/Swagger) for the REST endpoints
