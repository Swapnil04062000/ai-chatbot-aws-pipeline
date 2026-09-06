FROM python:3.12-slim AS builder

# Copy the uv binary from the official Astral image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation and disable python downloads by uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0

# Install dependencies using lockfiles for optimal caching
COPY pyproject.toml uv.lock ./

# 'uv sync' automatically creates the virtual environment at /app/.venv
RUN uv sync --locked --no-install-project --no-editable

# Copy application source code, telemetry config, and static assets
COPY main.py otel_metrics.py otel-config.yaml ./
COPY static/ static/

# Stage 2: Final lightweight runtime image
FROM python:3.12-slim

WORKDIR /app

# Copy the synced virtual environment and files from the builder stage
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app

# Set PATH to use the virtual environment binaries
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Expose the port FastAPI runs on
EXPOSE 8000

# Run Uvicorn server bound to all interfaces
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
