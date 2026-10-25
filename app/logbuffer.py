"""In-memory ring buffer of recent log records for the web UI."""
import collections
import logging
import threading

_buf = collections.deque(maxlen=500)
_lock = threading.Lock()
_seq = 0


class Handler(logging.Handler):
    def emit(self, record):
        global _seq
        try:
            line = dict(t=record.created, level=record.levelname, name=record.name, msg=record.getMessage())
            with _lock:
                _seq += 1
                line["id"] = _seq
                _buf.append(line)
        except Exception:
            pass


def install():
    logging.getLogger().addHandler(Handler())


def tail(after=0):
    with _lock:
        return [x for x in _buf if x["id"] > after]
