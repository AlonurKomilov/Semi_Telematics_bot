"""Gunicorn's normal Uvicorn worker with bounded browser socket input buffers."""
from uvicorn.workers import UvicornWorker


class APIWorker(UvicornWorker):
    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, 'ws_max_size': 4096, 'ws_max_queue': 4,
                     'ws_ping_interval': 20, 'ws_ping_timeout': 20}
