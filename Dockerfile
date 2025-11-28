# PostgreSQL Backup Manager - Docker Image
# Provides full access to host filesystem for backup/restore operations

FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gnupg2 \
    lsb-release \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (for Docker-in-Docker operations)
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y docker-ce-cli docker-compose-plugin

# Add PostgreSQL official apt repository (modern approach without apt-key)
RUN wget --quiet -O /usr/share/keyrings/postgresql-keyring.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.asc] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list

# Install PostgreSQL client tools for versions 14, 15, 16, 17, and 18
# This provides compatibility with most PostgreSQL server versions
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client-14 \
    postgresql-client-15 \
    postgresql-client-16 \
    postgresql-client-17 \
    postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Verify PostgreSQL tools are installed
RUN echo "=== Installed PostgreSQL client versions ===" \
    && ls -1 /usr/lib/postgresql/ \
    && echo "=== pg_dump version ===" \
    && /usr/lib/postgresql/18/bin/pg_dump --version \
    && echo "=== psql version ===" \
    && /usr/lib/postgresql/18/bin/psql --version

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt ./
COPY webapp/requirements.txt ./webapp/
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r webapp/requirements.txt

# Copy application code
COPY backup_script.py ./
COPY webapp/ ./webapp/

# Create necessary directories
RUN mkdir -p /host /backups /app/data

# Set environment variables for the app
ENV BACKUP_SCRIPT_PATH=/app/backup_script.py \
    HOST_ROOT=/host \
    DEFAULT_BACKUP_DIR=/host/tmp/postgres-backups \
    CONNECTIONS_FILE=/app/data/connections.json

# Expose port
EXPOSE 6536

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:6536/api/status || exit 1

# Run the application (always start the web UI)
CMD ["python", "-m", "uvicorn", "webapp.app:app", "--host", "0.0.0.0", "--port", "6536"]
