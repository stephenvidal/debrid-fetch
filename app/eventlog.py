"""Durable, bounded history of what happened to each torrent - and why.

The app log answers "what is it doing"; this answers "where did it go". A torrent that
Real-Debrid silently drops, a release it refuses with 451, a download that stalls and is
given up on: each leaves one event here, kept across restarts in CONFIG_PATH/events.json.
"""
import json
import logging
import os
import threading
import time

import config

log = logging.getLogger("events")
MAX_EVENTS = 1000


class EventLog:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = []
        self.seq = 0
        self.path = os.path.join(config.CONFIG_PATH, "events.json")
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            items = data.get("events") or []
            self.items = [e for e in items if isinstance(e, dict) and "kind" in e][-MAX_EVENTS:]
            self.seq = int(data.get("seq") or (self.items[-1].get("id", 0) if self.items else 0))
        except (OSError, ValueError, TypeError):
            self.items, self.seq = [], 0

    def _save(self, items, seq):
        try:
            os.makedirs(config.CONFIG_PATH, exist_ok=True)
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # names can contain a title
            with os.fdopen(fd, "w") as f:
                json.dump({"seq": seq, "events": items}, f)
            os.replace(tmp, self.path)
        except OSError as e:                     # never let bookkeeping break a download
            log.warning("could not save the event log: %s", e)

    def record(self, kind, hash="", name="", detail=""):
        with self.lock:
            self.seq += 1
            e = dict(id=self.seq, t=int(time.time()), kind=kind,
                     hash=(hash or "").lower(), name=name or "", detail=detail or "")
            self.items.append(e)
            self.items = self.items[-MAX_EVENTS:]
            snapshot, seq = list(self.items), self.seq
        self._save(snapshot, seq)
        return e

    def tail(self, after=0, hash=None, limit=200):
        """Events newer than `after`, optionally only for one hash, oldest first."""
        h = (hash or "").lower()
        with self.lock:
            out = [e for e in self.items if e["id"] > after and (not h or e["hash"] == h)]
        return out[-limit:] if limit and limit > 0 else out

    def clear(self):
        with self.lock:
            self.items = []
            snapshot, seq = [], self.seq
        self._save(snapshot, seq)


events = EventLog()
