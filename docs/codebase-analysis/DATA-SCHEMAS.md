# Data Schemas

## Model Registry Schema

Models are stored in memory (and persisted via config). Key fields:

```python
class ModelConfig:
    name: str                    # Unique model identifier
    path: str                    # Path to GGUF model file
    port: int                    # llama-server port (auto-assigned if not set)
    args: list[str]              # llama-server command-line arguments
    running: bool                # Whether the model is currently running
    pid: int | None              # Process ID of llama-server
    memory_usage: dict           # GPU/CPU memory usage stats
    kv_cache_slots: int          # Number of KV cache slots (for mnemonic-proxy)
```

## Model List Response (`GET /api/models`)

```json
{
  "models": [
    {
      "name": "model-name",
      "path": "/path/to/model.gguf",
      "port": 9931,
      "running": true,
      "pid": 12345,
      "memory_usage": {"gpu": "2.1GB", "cpu": "512MB"},
      "kv_cache_slots": 100
    }
  ]
}
```

## Chat Completion Request (`POST /api/chat`)

```json
{
  "model": "model-name",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "stream": false,
  "temperature": 0.7
}
```

## Chat Completion Response

```json
{
  "choices": [
    {
      "message": {"role": "assistant", "content": "Response text..."},
      "finish_reason": "stop"
    }
  ]
}
```

## System Info Response (`GET /api/system/info`)

```json
{
  "cpu": {"cores": 8, "model": "Intel Core i7"},
  "gpu": [
    {"name": "NVIDIA RTX 3080", "memory": "10GB"}
  ],
  "memory": {"total": "32GB", "available": "16GB"},
  "os": "Linux 5.15.0"
}
```

## State/SSE Log Entry

Real-time logs streamed via SSE from `state.py`:

```json
{
  "timestamp": "2026-11-26T04:00:00Z",
  "level": "info",
  "model": "model-name",
  "message": "Model started successfully"
}
```
