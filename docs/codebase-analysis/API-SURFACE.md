# API Surface Reference

## Base URL
All API endpoints are relative to the Flask app root (default: `http://localhost:5000`)

## Model Management (`/api/models`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/models` | List all registered models |
| `POST` | `/api/models` | Register a new model |
| `GET` | `/api/models/{name}` | Get details for a specific model |
| `DELETE` | `/api/models/{name}` | Delete a model registration |

## Lifecycle Management (`/api/lifecycle`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/lifecycle/{name}/start` | Start a model's llama-server process |
| `POST` | `/api/lifecycle/{name}/stop` | Stop a model's llama-server process |
| `GET` | `/api/lifecycle/{name}/status` | Get running status of a model |
| `POST` | `/api/chat` | Chat completion endpoint (OpenAI-compatible) |

## System Information (`/api/system`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/system/info` | System hardware & software info |
| `GET` | `/api/system/logs` | Get application logs |
| `POST` | `/api/system/shutdown` | Graceful shutdown |

## Archive / Context Management (`/api/archive`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/archive/journal/{model}` | Get archive journal for a model |
| `GET` | `/api/archive/browser/{model}` | Archive browser UI for a model |

## Hugging Face Integration (`/api/hf`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/hf/repo-search` | Search repositories on HF Hub |
| `GET` | `/api/hf/models` | List models from HF Hub |

## Benchmarks (`/api/benchmarks`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/benchmarks` | List benchmarks |
| `POST` | `/api/benchmarks/run` | Run a benchmark |

## Health Check

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Basic health check endpoint |
