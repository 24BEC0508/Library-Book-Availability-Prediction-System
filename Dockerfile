# ==============================================================================
# Dockerfile - Library Availability Prediction & Stockout Warning System
# Optimized for DigitalOcean App Platform & DigitalOcean Droplets
# ==============================================================================

FROM python:3.12-slim

# Prevent Python from writing .pyc files and force unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0 \
    CLOUD_PROVIDER=DigitalOcean \
    DO_REGION=blr1 \
    APP_TIMEZONE=Asia/Kolkata \
    ADMIN_API_KEY=library-admin-secret-key-2026

WORKDIR /app

# Install minimal system dependencies (curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code, static web assets, ML model artifact, and catalogs
COPY app/ app/
COPY ml/ ml/
COPY model/ model/
COPY data/ data/

# Create a dedicated non-root user for security and grant permissions
RUN useradd -u 10001 -m -s /bin/bash appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Container healthcheck querying the standard API health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/health || exit 1

# Launch the concurrent multi-threaded HTTP server and student/admin web portal
CMD ["python", "app/api.py"]
