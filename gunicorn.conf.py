import os

bind = "0.0.0.0:8090"
# Single-worker by design — APScheduler's expire_commands job claims a
# Postgres advisory lock that only the FIRST worker acquires, and the
# in-memory rate-limit bucket needs to be shared across all incoming
# traffic. Threads handle concurrency within the worker.
workers = int(os.environ.get("REBOOTER_GUNICORN_WORKERS", "1"))
worker_class = "gthread"
threads = int(os.environ.get("REBOOTER_GUNICORN_THREADS", "8"))
timeout = 60
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("REBOOTER_LOG_LEVEL", "info")
