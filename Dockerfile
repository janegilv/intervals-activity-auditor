# The web interface plus the scheduled monitor in one container.
# See "Running on a server with Docker" in README.md; compose.yaml has the
# settings (data volume, port, time zone).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Time zones for TZ (log timestamps and notification times).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-web.txt ./
RUN pip install -r requirements-web.txt

COPY . .

# Run as an ordinary user. UID/GID should own the data folder on the host
# (compose.yaml passes AUDITOR_UID / AUDITOR_GID, default 1000).
ARG UID=1000
ARG GID=1000
RUN (getent group "$GID" >/dev/null || groupadd --gid "$GID" auditor) \
    && useradd --uid "$UID" --gid "$GID" --no-create-home --home-dir /data auditor \
    && mkdir -p /data \
    && chown "$UID:$GID" /data
USER auditor

# Everything that changes lives in /data: the database, logs, reports and
# your checks.local.toml. Settings and credentials come from the environment.
ENV DATA_DIR=/data \
    OUTPUT_DIR=/data/output \
    CHECKS_LOCAL_FILE=/data/checks.local.toml \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8080
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=1m --timeout=5s --start-period=30s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('WEB_PORT', '8080'), timeout=4)"]

CMD ["python", "main.py", "serve"]
