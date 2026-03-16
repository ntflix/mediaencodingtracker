# syntax=docker/dockerfile:1

# ── Build stage: install Python deps ─────────────────────────────────────────
FROM python:3.13-slim-bookworm AS builder

WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    "sqlalchemy[asyncio]" \
    aiosqlite \
    apscheduler \
    pydantic \
    pydantic-settings \
    python-multipart \
    anyio


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm

# Install ffmpeg (includes ffprobe).
# On RPi4b (ARM64) there is no stable Docker-accessible H.264 hardware encoder,
# so software encoding (libx264) is used.  For hardware-accelerated encoding,
# see README § Hardware Acceleration.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder.
COPY --from=builder /usr/local/lib/python3.13 /usr/local/lib/python3.13
COPY --from=builder /usr/local/bin/uvicorn     /usr/local/bin/uvicorn

WORKDIR /app
COPY app/ ./app/
COPY docker-compose.yml ./docker-compose.yml

# Non-root user for safety.
RUN useradd -m -u 1000 tracker && \
    mkdir -p /data && chown tracker:tracker /data
USER tracker

EXPOSE 8000

# DB lives in /data (named volume); media is bind-mounted at /media.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
