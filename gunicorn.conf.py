"""Gunicorn configuration for the 4truck API.

Production entry point: gunicorn fans out N uvicorn workers in front of
the FastAPI app. Each worker is its own process — they share Redis +
Postgres but each runs an independent asyncio event loop.

Usage:
    gunicorn -c gunicorn.conf.py interfaces.api.app:create_api

Tuning knobs (all overridable via environment):
    GUNICORN_WORKERS    integer; default = 2 × CPU cores + 1 (Gunicorn doc rec)
    API_PORT            integer; default 8000
    GUNICORN_BIND       full bind string; overrides API_PORT if set
    GUNICORN_TIMEOUT    seconds; default 90 (matches nginx + apiJSONSlow client timeout)
    GUNICORN_KEEPALIVE  seconds; default 75 (HTTP keepalive between nginx and gunicorn)
    GUNICORN_LOG_LEVEL  default "info"

Why these defaults
------------------
* ``--worker-class uvicorn.workers.UvicornWorker``: each worker hosts its own
  uvicorn loop so async endpoints stay async. Sync gunicorn workers would
  defeat the purpose.
* ``preload_app = False``: per-worker imports cost a few hundred ms but avoid
  the asyncio-after-fork pitfalls (event loop, aiohttp sessions, asyncpg
  pools cannot be safely shared across forks).
* ``workers = 2 × CPU + 1``: Gunicorn's recommendation; with mostly I/O-bound
  endpoints this comfortably saturates available CPU without context-switch
  thrash.
* ``timeout = 90s``: matches both the nginx ``proxy_read_timeout`` and the
  dashboard's ``apiJSONSlow`` client. Cold scorecard requests can legitimately
  take 30-60s.
* ``graceful_timeout = 30s``: SIGTERM grace period so in-flight requests
  finish during deploys.
"""

from __future__ import annotations

import multiprocessing
import os


# ── Bind & process model ──────────────────────────────────────
_DEFAULT_PORT = int(os.getenv("API_PORT", "8000"))
bind = os.getenv("GUNICORN_BIND", f"0.0.0.0:{_DEFAULT_PORT}")

_DEFAULT_WORKERS = max(2, multiprocessing.cpu_count() * 2 + 1)
workers = int(os.getenv("GUNICORN_WORKERS", str(_DEFAULT_WORKERS)))

worker_class = "uvicorn.workers.UvicornWorker"

# Keep async loops independent across forks — see module docstring.
preload_app = False

# ── Timeouts ──────────────────────────────────────────────────
timeout = int(os.getenv("GUNICORN_TIMEOUT", "90"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "75"))

# ── Logging ───────────────────────────────────────────────────
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")  # stdout
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")    # stderr
# %h client | %l ident | %u user | %t time | %r request line |
# %s status | %b bytes | %{X-Request-ID}i — useful for correlating
# with the API's RequestIDMiddleware.
access_log_format = (
    '%(h)s "%(r)s" %(s)s %(b)s %(L)s "%({X-Request-ID}i)s"'
)

# ── Worker recycling (defends against memory leaks) ───────────
# Each worker handles up to N requests then gracefully exits and is
# replaced. With 1k req/worker the recycle happens roughly every few
# minutes under steady load; the +/- 100 jitter staggers restarts so
# workers don't all recycle at the same instant.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "100"))


# ── Lifecycle hooks (observability) ───────────────────────────

def on_starting(server):
    server.log.info(
        "gunicorn starting: bind=%s workers=%d worker_class=%s timeout=%ds",
        bind, workers, worker_class, timeout,
    )


def post_fork(server, worker):
    # Each worker logs its PID so we can correlate per-worker logs with
    # the gunicorn arbiter's view.
    server.log.info("gunicorn worker forked: pid=%d", worker.pid)


def worker_exit(server, worker):
    server.log.info("gunicorn worker exited: pid=%d", worker.pid)
