# llama.cpp web frontend Dockerfile
# Multi-stage build for security and minimal image size

# Build stage
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


# Production stage
FROM python:3.11-slim AS production

WORKDIR /app

# Create non-root user for security
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# Copy installed packages from builder
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# Copy application code
COPY --chown=appuser:appgroup . /app

# Switch to non-root user
USER appuser

# Expose Flask port
EXPOSE 5000

# Build arguments for optional configuration
ARG REMOTE_MODE=false
ARG LLAMA_CPP_SERVER_URL=http://localhost:8080

# Set environment variables
ENV REMOTE_MODE=${REMOTE_MODE}
ENV LLAMA_CPP_SERVER_URL=${LLAMA_CPP_SERVER_URL}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/status', timeout=5)" || exit 1

# Run the application
CMD ["python", "main.py"]