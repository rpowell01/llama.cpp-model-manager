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

# Install runtime dependencies (curl for healthcheck, ca-certificates for HTTPS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# Copy application code (respects .dockerignore)
COPY --chown=appuser:appgroup . /app

# Ensure /app is writable by appuser (WORKDIR creates it as root)
RUN chown -R appuser:appgroup /app

# Create required directories
RUN mkdir -p /app/models /app/logs /app/webapp/data && \
    chown -R appuser:appgroup /app/models /app/logs /app/webapp/data

# Switch to non-root user
USER appuser

# Expose Flask port
EXPOSE 5000

# Health check - use /api/status endpoint which returns JSON
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/api/status > /dev/null 2>&1 || exit 1

# Run the application
CMD ["python", "webapp/main.py"]