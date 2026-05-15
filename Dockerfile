FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# `snmp` = net-snmp CLI tools (snmpbulkwalk/snmpget) — used by the
# v0.5.58 SNMP external-sensor poller via subprocess shell-out, the
# same pattern as the iputils-ping-backed watchdog ping probe.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates iputils-ping snmp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
COPY templates ./templates
COPY static ./static
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY gunicorn.conf.py ./gunicorn.conf.py

ENV REBOOTER_DATA_DIR=/data \
    REBOOTER_FIRMWARE_DIR=/data/firmware \
    REBOOTER_UPLOADS_DIR=/data/uploads

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8090/api/v1/version || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:create_app()"]
