# Module Map

## Backend Modules

### `webapp/main.py` (Entry Point)
- **Purpose**: Flask application factory and WSGI entry point
- **Key Responsibilities**:
  - Creates Flask app instance
  - Registers all route blueprints
  - Initializes logging and state
  - Starts background scheduler
- **Key Symbols**: `create_app()`, `if __name__ == "__main__"`

### `webapp/config.py` (Configuration)
- **Purpose**: Centralized configuration management
- **Key Responsibilities**:
  - Default values for all configurable parameters
  - Environment variable loading
  - Path resolution (models, logs, temp)
  - WSL path handling
- **Key Symbols**: `Config` class, `get_config()`

### `webapp/models.py` (Model Registry)
- **Purpose**: Model registry and management
- **Key Responsibilities**:
  - Model registration/unregistration
  - Model lookup by name/path
  - Model state tracking
- **Key Symbols**: `ModelRegistry`, `register_model()`, `get_model()`

### `webapp/processes.py` (Process Lifecycle)
- **Purpose**: llama-server process management
- **Key Responsibilities**:
  - Start/stop llama-server processes
  - Process monitoring (PID, health)
  - Port allocation
- **Key Symbols**: `start_model_process()`, `stop_model_process()`, `get_process_status()`

### `webapp/state.py` (Shared State)
- **Purpose**: Runtime state management and SSE logging
- **Key Responsibilities**:
  - Shared in-memory state across requests
  - Real-time log streaming via Server-Sent Events
  - Event broadcasting
- **Key Symbols**: `StateManager`, `add_log()`, `subscribe()`

### `webapp/mnemonic_proxy.py` (Proxy Manager)
- **Purpose**: Mnemonic-proxy context management
- **Key Responsibilities**:
  - Start/stop mnemonic-proxy instances
  - KV cache slot management (save/restore/recall)
  - Proxy health monitoring
  - Port management for proxy instances
- **Key Symbols**: `MnemonicProxyManager`, `start_proxy()`, `stop_proxy()`, `save_slot()`, `recall_slot()`

### `webapp/metrics.py` (Metrics)
- **Purpose**: Performance metrics scraping and parsing
- **Key Responsibilities**:
  - Scrape `/metrics` endpoint from llama-server
  - Parse GPU/CPU/memory metrics
  - Format for export
- **Key Symbols**: `scrape_metrics()`, `parse_metrics()`

### `webapp/scheduler.py` (Scheduler)
- **Purpose**: Background periodic tasks
- **Key Responsibilities**:
  - Periodic metrics export (every N seconds)
  - Background cleanup tasks
- **Key Symbols**: `MetricsScheduler`, `start_scheduler()`

### `webapp/utils.py` (Utilities)
- **Purpose**: Helper functions
- **Key Responsibilities**:
  - String formatting
  - Path utilities
  - Common helper operations
- **Key Symbols**: `format_bytes()`, `sanitize_filename()`

## Route Blueprints

### `webapp/routes/models.py`
- **Endpoints**: `/api/models/*`
- **Purpose**: Model CRUD operations

### `webapp/routes/lifecycle.py`
- **Endpoints**: `/api/lifecycle/*`, `/api/chat`
- **Purpose**: Model start/stop/status, chat proxy

### `webapp/routes/system.py`
- **Endpoints**: `/api/system/*`
- **Purpose**: System info, logs, shutdown

### `webapp/routes/archive.py`
- **Endpoints**: `/api/archive/*`
- **Purpose**: Archive browser and journal endpoints

### `webapp/routes/benchmarks.py`
- **Endpoints**: `/api/benchmarks/*`
- **Purpose**: Benchmark management

### `webapp/routes/huggingface.py`
- **Endpoints**: `/api/hf/*`
- **Purpose**: Hugging Face Hub integration

## Frontend Modules

### `templates/index.html`
- **Purpose**: Main dashboard HTML template
- **Key Elements**: Tile grid, toolbar, properties panel, modals

### `static/js/model-manager.js`
- **Purpose**: Core UI logic for model management
- **Key Functions**: `renderTiles()`, `updateTileSelection()`, `startModel()`, `stopModel()`

### `static/js/ui-controls.js`
- **Purpose**: UI control handlers
- **Key Functions**: `setupHideModelListeners()`, properties panel toggle

### `static/js/init.js`
- **Purpose**: Initialization and event binding
- **Key Functions**: `init()`, event listener registration

### `static/css/` (Stylesheets)
- `variables.css` - CSS custom properties (colors, spacing)
- `layout.css` - Grid/flexbox layout styles
- `components/tiles.css` - Model tile styling
- `components/buttons.css` - Button styles
