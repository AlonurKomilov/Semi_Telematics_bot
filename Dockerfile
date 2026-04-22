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

CMD ["python3", "run.py"]
