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
docker run -p 5000:5000 \
  -e REMOTE_MODE=true \
  -e LLAMA_CPP_SERVER_URL=http://localhost:8080 \
  -v ./models:/app/models \
  llamaweb
```

Or using docker-compose with an external server:

```bash
# Create a .env file
echo "REMOTE_MODE=true" > .env
echo "LLAMA_CPP_SERVER_URL=http://localhost:8080" >> .env

# Run
docker-compose up -d
```

### Running with Remote llama.cpp Server

You can run the llama.cpp server alongside the web frontend:

```bash
# Start both services
docker-compose -f docker-compose.yml up -d

# Or with a custom model
docker run -d --name llamacpp-server \
  -p 8080:8080 \
  -v ./models:/models \
  ghcr.io/ggerganov/llama.cpp:server \
  -m /models/your-model.gguf --host 0.0.0.0 --port 8080

# Then run web in remote mode
docker run -p 5000:5000 \
  -e REMOTE_MODE=true \
  -e LLAMA_CPP_SERVER_URL=http://localhost:8080 \
  llamaweb
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `REMOTE_MODE` | `false` | Set to `true` to use remote llama.cpp server |
| `LLAMA_CPP_SERVER_URL` | `http://localhost:8080` | URL of the remote llama.cpp server |

## Development

```bash
# Run tests
python -m pytest tests/

# Run the development server
python main.py --host 0.0.0.0 --port 5000
```

## License

MIT