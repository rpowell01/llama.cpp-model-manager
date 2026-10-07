# llama.cpp Web Frontend

A web-based interface for managing and interacting with llama.cpp models.

## Features

- Model lifecycle management (load/unload)
- Chat interface with loaded models
- Remote mode support for distributed llama.cpp servers
- Model downloads and management
- System status monitoring
- **Empirical Optimization** — Automatic benchmark-based parameter tuning

## Requirements

- Python 3.11+
- Flask 3.0+
- llama.cpp server binary (for local mode)

## Installation

```bash
pip install -r requirements.txt
```

## Running with Docker

The application can be run using Docker for easy deployment and isolation.

### Local Mode (Default)

Local mode runs the llama.cpp server as a local process managed by the web application:

```bash
# Build the image
docker build -t llamaweb .

# Run the container
docker run -p 5000:5000 -v ./models:/app/models llamaweb
```

Or using docker-compose:

```bash
# Run with local model management
docker-compose up -d
```

### Remote Mode

Remote mode connects to an external llama.cpp server via REST API:

```bash
# Build with remote mode enabled
docker build -t llamaweb --build-arg REMOTE_MODE=true --build-arg LLAMA_CPP_SERVER_URL=http://localhost:8080 .

# Run the container
# Note: Replace host.docker.internal with your host's IP if on Linux
docker run -p 5000:5000 \
  -e REMOTE_MODE=true \
  -e LLAMA_CPP_SERVER_URL=http://host.docker.internal:8080 \
  -v ./models:/app/models \
  llamaweb
```

Or using docker-compose with an external server:

```bash
# Create a .env file
echo "REMOTE_MODE=true" > .env
# Use host.docker.internal for macOS/Windows, or host IP for Linux
echo "LLAMA_CPP_SERVER_URL=http://host.docker.internal:8080" >> .env

# Run
docker-compose up -d
```

### Running with Remote llama.cpp Server

You can run the llama.cpp server alongside the web frontend using docker-compose:

```yaml
# docker-compose.remote.yml
version: '3.8'
services:
  llamacpp-server:
    image: ghcr.io/ggerganov/llama.cpp:server
    ports:
      - "8080:8080"
    volumes:
      - ./models:/models
    command: -m /models/your-model.gguf --host 0.0.0.0 --port 8080
    networks:
      - llamanet

  web:
    build:
      context: .
      build-args:
        REMOTE_MODE: "true"
        LLAMA_CPP_SERVER_URL: "http://llamacpp-server:8080"
    ports:
      - "5000:5000"
    volumes:
      - ./models:/app/models
    depends_on:
      - llamacpp-server
    networks:
      - llamanet

networks:
  llamanet:
    driver: bridge
```

Run with:
```bash
docker-compose -f docker-compose.remote.yml up -d
```

For standalone docker run (host networking), use `host.docker.internal` on macOS/Windows or your host IP on Linux:
```bash
docker run -p 5000:5000 \
  -e REMOTE_MODE=true \
  -e LLAMA_CPP_SERVER_URL=http://host.docker.internal:8080 \
  llamaweb
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `REMOTE_MODE` | `false` | Set to `true` to use remote llama.cpp server |
| `LLAMA_CPP_SERVER_URL` | `http://localhost:8080` | URL of the remote llama.cpp server |
| `PORT` | `5000` | Port for the web frontend (local mode only) |
| `HOST` | `0.0.0.0` | Host for the web frontend |
| `BENCHMARK_MAX_DURATION` | `60` | Maximum time (seconds) for empirical benchmark optimization |
| `BENCHMARK_CACHE_DIR` | `~/.cache/llama-web/benchmarks` | Directory to store benchmark cache files |
| `BENCHMARK_ENABLED` | `true` | Enable/disable empirical benchmark optimization |

## Empirical Optimization

The web frontend includes an **Empirical Optimization** feature that automatically finds the best llama.cpp server parameters for your specific hardware and model combination by running actual benchmarks.

### Heuristic vs Empirical Modes

| Aspect | Heuristic Mode (`/api/optimize-settings`) | Empirical Mode (`/api/benchmark-optimize`) |
|--------|------------------------------------------|-------------------------------------------|
| **Method** | Rule-based formulas based on hardware specs | Runs actual `llama-bench` benchmarks |
| **Speed** | Instant (~ms) | Slower (10-300 seconds) |
| **Accuracy** | Good general estimates | Hardware-specific optimal settings |
| **Use Case** | Quick setup, CI/CD, first run | Production tuning, maximum performance |
| **Requirements** | None | `llama-bench` binary, model file |

**Heuristic Mode** (original): Uses `compute_optimal_settings()` which applies general rules based on detected hardware (CPU cores, RAM, GPU VRAM). Fast but may not find the true optimum for your specific hardware/model combination.

**Empirical Mode** (new): Runs a sweep of `llama-bench` benchmarks across multiple parameter combinations (GPU layers, batch sizes, thread counts, cache types, etc.) and selects the configuration that maximizes your chosen target metric (throughput, latency, or balanced). Results are cached for 30 days keyed by model file + hardware fingerprint.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/optimize-settings` | POST | Heuristic optimization (instant) |
| `/api/benchmark-optimize` | POST | Empirical optimization (runs benchmarks) |

### Example Benchmark Optimization Request

```bash
curl -X POST http://localhost:5000/api/benchmark-optimize \
  -H "Content-Type: application/json" \
  -d '{
    "model": "my-model.gguf",
    "max_duration_sec": 60,
    "target": "balanced"
  }'
```

### Example Benchmark Optimization Response

```json
{
  "optimal_settings": {
    "gpu_layers": 50,
    "batch_size": 2048,
    "ubatch_size": 512,
    "threads": 16,
    "threads_batch": 16,
    "cache_type_k": "q8_0",
    "cache_type_v": "q8_0",
    "flash_attn": "auto",
    "ctx_size": 8192,
    "numa": true,
    "numa_value": "distribute",
    "spec_type": "ngram-simple",
    "spec_draft_n_max": 3,
    "spec_ngram_size_n": 12,
    "spec_ngram_size_m": 48
  },
  "benchmark_results": [
    {
      "config": {"n_gpu_layers": 50, "batch_size": 2048, "threads": 16, ...},
      "metrics": {"pp_avg": 1250.5, "tg_avg": 45.2, "latency_p50": 22.1, ...},
      "success": true
    },
    ...
  ],
  "hardware_info": {...},
  "cached": false,
  "duration_sec": 45.3,
  "optimization_target": "balanced",
  "model": "my-model.gguf"
}
```

### Benchmark Cache

Benchmark results are cached to avoid re-running benchmarks for the same model/hardware combination:

- **Location**: `~/.cache/llama-web/benchmarks/` (or `BENCHMARK_CACHE_DIR`)
- **Key**: MD5 hash of model file (path, size, mtime) + hardware fingerprint
- **TTL**: 30 days
- **Invalidation**: Automatic on hardware changes

To clear the cache:
```bash
rm -rf ~/.cache/llama-web/benchmarks/
```

Or set `BENCHMARK_CACHE_DIR` to a different location and delete that directory.

### Configuration Options

Control benchmark behavior via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `BENCHMARK_MAX_DURATION` | `60` | Max seconds to spend benchmarking (10-3600) |
| `BENCHMARK_CACHE_DIR` | `~/.cache/llama-web/benchmarks` | Cache directory path |
| `BENCHMARK_ENABLED` | `true` | Set to `false` to disable empirical optimization |

### Requirements for Empirical Optimization

1. **llama-bench binary** — Must be in the same directory as `llama-server` (or `LLAMA_SERVER_DIR`)
2. **Model file** — Local file path accessible to the benchmark process
3. **Time** — Allow 10-3600 seconds depending on `BENCHMARK_MAX_DURATION`
4. **Disk space** — Temporary files created during benchmarking (cleaned up automatically)

### Troubleshooting

**Benchmark binary not found:**
- Ensure `llama-bench` is built/installed alongside `llama-server`
- Set `LLAMA_SERVER_DIR` to the directory containing both binaries

**Benchmarks timeout:**
- Increase `BENCHMARK_MAX_DURATION` (max 3600 seconds)
- Reduce model context size or batch sizes in the configuration

**Cache not working:**
- Check write permissions on `BENCHMARK_CACHE_DIR`
- Verify hardware detection is consistent (`/api/hardware-info`)

## Architecture

### Mode Selection

The application operates in one of two modes:

1. **Local Mode** (`REMOTE_MODE=false`): The web frontend manages llama.cpp server processes directly. Models are loaded/unloaded by spawning local processes.

2. **Remote Mode** (`REMOTE_MODE=true`): The web frontend acts as a client, delegating all model operations to a remote llama.cpp server via REST API.

### Remote Mode Flow

```
┌─────────────────┐     ┌──────────────────┐
│  Web Frontend   │────▶│  llama.cpp Server │
│  (Port 5000)    │     │  (Port 8080)     │
└─────────────────┘     └──────────────────┘
         │                       │
         ▼                       ▼
    /api/start ───────────────▶ /api/models/load
    /api/stop  ──────────────▶ /api/models/unload
    /api/chat  ──────────────▶ /api/chat
```

## API Endpoints

| Endpoint | Method | Mode | Description |
|----------|--------|------|-------------|
| `/api/status` | GET | Both | Get system status and mode |
| `/api/start` | POST | Both | Load/start a model |
| `/api/stop` | POST | Both | Stop/unload a model |
| `/api/chat` | POST | Both | Send chat message |
| `/api/next-port` | GET | Local | Get available port (local only) |

## Troubleshooting

### Remote Mode Issues

**503 Service Unavailable**: The web frontend cannot reach the remote llama.cpp server.
- Verify `LLAMA_CPP_SERVER_URL` is correct
- Ensure the remote server is running and accessible
- Check network connectivity between containers

**400 Bad Request**: Model not found on remote server.
- Verify the model name matches exactly
- Check the remote server has the model loaded

### Local Mode Issues

**Port already in use**: Another process is using the selected port.
- Use `/api/next-port` to find an available port
- Or set a specific port via environment variable

**Model not found**: The specified model file doesn't exist.
- Ensure models are in the `./models` directory
- Verify file permissions

## Development

```bash
# Run tests
python -m pytest tests/

# Run the development server
python main.py --host 0.0.0.0 --port 5000
```

## License

MIT