# ── Stage 1: Build dashboard ─────────────────────────────────────
FROM node:20-slim AS dashboard-build
WORKDIR /build
COPY interfaces/dashboard/package.json interfaces/dashboard/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install --ignore-scripts
COPY interfaces/dashboard/ .
RUN npm run build

# ── Stage 2: Python application ──────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# System deps for reportlab (fonts), staticmap (PIL), and cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev \
    libfreetype6 \
    libjpeg62-turbo \
    libpng16-16 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Copy built dashboard from stage 1
COPY --from=dashboard-build /build/dist interfaces/dashboard/dist/

# Create data directory for SQLite
RUN mkdir -p /app/data

EXPOSE 8000 8001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Default entrypoint runs the legacy single-process model (API + bot +
# scheduler in one Python process). For multi-worker production deploys,
# split into THREE containers and override CMD:
#
#   API container (multiple workers, horizontally scalable):
#     CMD ["python3", "-m", "gunicorn", "-c", "gunicorn.conf.py",
#          "interfaces.api.app:app"]
#     ENV ENABLE_API=1 ENABLE_BOT=0 ENABLE_SCHEDULER=0
#
#   Bot/scheduler container (single instance — owns Telegram polling
#   + APScheduler, holds the global scheduler lock in Redis):
#     CMD ["python3", "run.py"]
#     ENV ENABLE_API=0 ENABLE_BOT=1 ENABLE_SCHEDULER=1
#
#   ARQ worker container (Phase 3 — horizontally scalable, runs
#   background jobs enqueued by the API or by ARQ cron):
#     CMD ["python3", "-m", "arq", "capabilities.jobs.worker.WorkerSettings"]
#     ENV ENABLE_API=0 ENABLE_BOT=0 ENABLE_SCHEDULER=0
#
# All three containers share the same Redis + DB so the API can enqueue
# jobs the worker picks up.
CMD ["python3", "run.py"]
