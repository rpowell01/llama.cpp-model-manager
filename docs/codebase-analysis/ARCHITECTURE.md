# System Architecture

## High-Level Architecture

```mermaid
graph TB
    subgraph "Frontend (Browser)"
        HTML[index.html]
        JS[model-manager.js, ui-controls.js, init.js]
        CSS[tiles.css, layout.css, variables.css]
    end

    subgraph "Backend (Flask App)"
        MAIN[main.py - App Factory]
        CONFIG[config.py - Configuration]
        
        subgraph "Routes"
            MODELS[models.py - Model CRUD]
            LIFECYCLE[lifecycle.py - Start/Stop/Chat]
            SYSTEM[system.py - System Info/Logs]
            ARCHIVE[archive.py - Archive Browser]
            BENCHMARKS[benchmarks.py - Benchmarks]
            HF[huggingface.py - HF Hub]
        end
        
        subgraph "Core Services"
            MODEL_REGISTRY[models.py - Registry]
            PROCESS_MGR[processes.py - Process Mgmt]
            STATE_MGR[state.py - State/SSE]
            MNEMONIC_PROXY[mnemonic_proxy.py - Proxy Mgmt]
            METRICS[metrics.py - Metrics Scraping]
            SCHEDULER[scheduler.py - Background Tasks]
        end
    end

    subgraph "External Services"
        LLAMA_SERVER[llama-server processes]
        MNEMONIC_PROXY_SVC[mnemonic-proxy instances]
        HF_HUB[Hugging Face Hub API]
    end

    HTML --> JS
    JS --> MAIN
    MAIN --> CONFIG
    MAIN --> MODELS
    MAIN --> LIFECYCLE
    MAIN --> SYSTEM
    MAIN --> ARCHIVE
    MAIN --> BENCHMARKS
    MAIN --> HF
    
    MODELS --> MODEL_REGISTRY
    LIFECYCLE --> PROCESS_MGR
    LIFECYCLE --> MNEMONIC_PROXY
    LIFECYCLE --> STATE_MGR
    PROCESS_MGR --> LLAMA_SERVER
    MNEMONIC_PROXY --> MNEMONIC_PROXY_SVC
    METRICS --> LLAMA_SERVER
    SCHEDULER --> METRICS
```

## Request Flow: Model Startup

```mermaid
sequenceDiagram
    participant U as User (UI)
    participant F as Flask Frontend
    participant R as routes/lifecycle.py
    participant P as processes.py
    participant M as models.py
    participant S as state.py
    
    U->>F: Click "Start" on tile
    F->>R: POST /api/lifecycle/start/<model>
    R->>M: register_model() / get_model()
    R->>P: start_model_process(model_config)
    P->>P: Allocate port, build cmd args
    P->>LLAMA: Spawn llama-server process
    P->>S: Log "Model starting..."
    P->>R: Return process info
    R->>F: JSON response {pid, port}
    F->>U: Update tile UI (running state)
```

## Request Flow: Chat Completion

```mermaid
sequenceDiagram
    participant U as User (UI)
    participant F as Flask Frontend
    participant R as routes/lifecycle.py
    participant P as processes.py
    participant L as llama-server
    
    U->>F: Send chat message
    F->>R: POST /api/chat
    R->>P: get_model_process(model_name)
    P->>L: Forward request to llama-server
    L->>P: Streaming response
    P->>R: Stream chunks
    R->>F: Stream chunks (SSE/Streaming)
    F->>U: Display response
```

## Data Flow: Real-time State Logging

```mermaid
sequenceDiagram
    participant P as processes.py
    participant S as state.py
    participant F as Flask Frontend
    participant U as User (Browser)
    
    P->>S: add_log(level, model, message)
    S->>S: Append to log buffer
    S->>F: SSE broadcast to subscribers
    F->>U: Push log event via EventSource
    U->>U: Render log in UI
```

## Component Boundaries

| Component | Responsibility | Dependencies |
|-----------|---------------|--------------|
| `main.py` | App initialization, blueprint registration | Flask, config, routes |
| `config.py` | Configuration defaults & loading | os, dotenv |
| `models.py` (core) | Model registry, lookup, state | - |
| `processes.py` | llama-server process lifecycle | psutil, subprocess |
| `state.py` | Shared state, SSE logging | threading |
| `mnemonic_proxy.py` | Proxy instance management | requests, subprocess |
| `metrics.py` | Metrics scraping/parsing | requests, re |
| `scheduler.py` | Background task scheduling | threading/schedule |
