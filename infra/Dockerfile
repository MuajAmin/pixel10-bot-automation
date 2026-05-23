FROM python:3.12-slim

# Install ADB, curl, bash, and sqlite3
# bash is needed for core/build_props.sh (called by automation.py)
# sqlite3 is needed for GSF ID extraction
RUN apt-get update && apt-get install -y \
    android-tools-adb \
    curl \
    bash \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY infra/requirements-android.txt .
RUN pip install --no-cache-dir -r requirements-android.txt

# ── Copy all required code ──────────────────────────────────────
# Build context is the project root (set in docker-compose.yml)

# Bot / API server
COPY bot/__init__.py /app/bot/__init__.py
COPY bot/android_worker /app/bot/android_worker

# Core automation (runner.py spawns this via subprocess)
COPY core/ /app/core/

# Config directory (build_props.sh reads pif.json etc.)
COPY config/ /app/config/

# Create directories
RUN mkdir -p /app/screenshots /app/logs

# Make shell scripts executable + strip CRLF
RUN find /app -name "*.sh" -exec sed -i "s/\r$//" {} \; \
    && find /app -name "*.sh" -exec chmod +x {} \; \
    && find /app -name "*.py" -exec sed -i "s/\r$//" {} \;

# Expose API port
EXPOSE 8800

# Run the API server. Compose publishes this namespace on host loopback only.
CMD ["sh", "-c", "uvicorn bot.android_worker.api_server:app --host ${WORKER_API_BIND:-127.0.0.1} --port 8800 --log-level info"]
