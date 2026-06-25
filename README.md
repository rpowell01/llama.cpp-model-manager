# llama.cpp Web Frontend

A web-based interface for managing and interacting with llama.cpp models.

## Features

- Model lifecycle management (load/unload)
- Chat interface with loaded models
- Remote mode support for distributed llama.cpp servers
- Model downloads and management
- System status monitoring

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