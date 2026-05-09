import os

bind = "0.0.0.0:8090"
workers = int(os.environ.get("REBOOTER_GUNICORN_WORKERS", "2"))
worker_class = "sync"
threads = int(os.environ.get("REBOOTER_GUNICORN_THREADS", "4"))
timeout = 60
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("REBOOTER_LOG_LEVEL", "info")
