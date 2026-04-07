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

# Create data directory for SQLite
RUN mkdir -p /app/data

EXPOSE 8443 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python3", "run.py"]
