"""Tracks torrents: adds to Real-Debrid, polls, downloads files, persists state."""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import config
from rd import RealDebrid, RDError

log = logging.getLogger("manager")
SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def clean(name):
    return SAFE.sub("_", name).strip(". ") or "unnamed"


class Paused(Exception):
    """Raised inside a download when the killswitch or a manual pause stops it; the .part file is kept."""


class RateLimiter:
    """Shared limiter: every chunk reserves a time slot, so the total stays under the limit across threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.next = 0.0

    def throttle(self, nbytes):
        bps = config.SPEED_LIMIT_MBPS * 1024 * 1024
        if not bps:
            return
        with self.lock:
            now = time.monotonic()
            self.next = max(self.next, now) + nbytes / bps
            wait = self.next - now
        if wait > 0:
            time.sleep(wait)


class Manager:
    def __init__(self):
        self.rd = RealDebrid()
        self.gate = lambda: None      # returns a reason string while downloads must stop (killswitch)
        self.paused = False           # manual pause from the UI
        self.bytes_total = 0
        self.limiter = RateLimiter()
        self.lock = threading.RLock()
        self.state_file = os.path.join(config.CONFIG_PATH, "state.json")
        self.torrents = {}   # hash -> dict
        self.categories = {}
        self.pool = ThreadPoolExecutor(16)  # MAX_PARALLEL_DOWNLOADS (<=16) is enforced in poll()
        self._load()

    # ---- persistence
    def _load(self):
        try:
            with open(self.state_file) as f:
                d = json.load(f)
            self.torrents, self.categories = d.get("torrents", {}), d.get("categories", {})
        except (OSError, ValueError):
            pass
        for t in self.torrents.values():
            if t["state"] == "downloading":  # interrupted by restart; resume
                t["state"] = "rd_ready"

    def _save(self):
        with self.lock:
            os.makedirs(config.CONFIG_PATH, exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"torrents": self.torrents, "categories": self.categories}, f)
            os.replace(tmp, self.state_file)

    def blocked_reason(self):
        if self.paused:
            return "Paused manually"
        return self.gate()

    def retry(self, h):
        t = self.torrents.get(h)
        if t and t["state"] == "error":
            t["error"] = ""
            t["state"] = "rd_ready" if t.get("links") else "rd_pending"
            self._save()
            return True
        return False

    # ---- API operations
    def add(self, h, rid_fn, category, name):
        why = self.gate()
        if why:
            raise RuntimeError(why)
        with self.lock:
            if h in self.torrents:
                return
            rid = rid_fn()
            self.torrents[h] = dict(
                hash=h, rd_id=rid, name=name or h, category=category or "",
                state="rd_pending", rd_progress=0, size=0, dl_bytes=0,
                added=int(time.time()), completed=0, files=[], error="",
                save_path=os.path.join(config.DOWNLOAD_PATH, clean(category)) if category else config.DOWNLOAD_PATH,
            )
            self._save()

    def delete(self, h, delete_files):
        with self.lock:
            t = self.torrents.pop(h, None)
            self._save()
        if t:
            self.rd.delete(t["rd_id"])
            if delete_files:
                import shutil
                shutil.rmtree(os.path.join(t["save_path"], clean(t["name"])), ignore_errors=True)

    # ---- background work
    def run(self):
        while True:
            try:
                if not config.RD_API_TOKEN:
                    log.warning("no Real-Debrid token configured; set one in the web UI or RD_API_TOKEN")
                else:
                    self.poll()
            except Exception:
                log.exception("poll failed")
            time.sleep(config.POLL_INTERVAL)

    def poll(self):
        why = self.blocked_reason()
        if why:
            log.info("not polling: %s", why)
            return
        with self.lock:
            items = list(self.torrents.values())
        for t in items:
            st = t["state"]
            if st in ("rd_pending", "rd_wait_select", "rd_downloading"):
                try:
                    self._poll_one(t)
                except RDError as e:
                    log.warning("%s: %s", t["name"], e)
            if t["state"] == "rd_ready" and not self.blocked_reason() \
                    and sum(x["state"] == "downloading" for x in items) < config.MAX_PARALLEL_DOWNLOADS:
                t["state"] = "downloading"
                self.pool.submit(self._download, t)
        self._save()

    def _poll_one(self, t):
        info = self.rd.info(t["rd_id"])
        s = info["status"]
        t["name"] = info.get("filename") or t["name"]
        t["size"] = info.get("bytes", t["size"])
        t["rd_progress"] = info.get("progress", 0)
        if s == "waiting_files_selection":
            self.rd.select_files(t["rd_id"], self._select(info))
            t["state"] = "rd_wait_select"
        elif s == "downloaded":
            t["links"] = info["links"]
            t["state"] = "rd_ready"
        elif s in ("magnet_error", "error", "virus", "dead", "magnet_conversion"):
            if s == "magnet_conversion":
                return
            t["state"], t["error"] = "error", s
        else:
            t["state"] = "rd_downloading"

    @staticmethod
    def _select(info):
        """File ids to select on RD, honouring MIN_FILE_SIZE_MB / EXCLUDE_REGEX; falls back to everything."""
        files = info.get("files") or []
        min_bytes = config.MIN_FILE_SIZE_MB * 1024 * 1024
        rx = re.compile(config.EXCLUDE_REGEX, re.I) if config.EXCLUDE_REGEX else None
        keep = [str(f["id"]) for f in files
                if f.get("bytes", 0) >= min_bytes and not (rx and rx.search(f.get("path", "")))]
        if not files or not keep:
            if files:
                log.warning("%s: filters excluded every file; selecting all", info.get("filename"))
            return "all"
        return ",".join(keep)

    def _download(self, t):
        root = os.path.join(t["save_path"], clean(t["name"]))
        try:
            os.makedirs(root, exist_ok=True)
            self._chown(t["save_path"])
            self._chown(root)
            links = t.get("links", [])
            if len(links) == 1 and not t.get("files"):
                pass
            t["files"] = t.get("files") or []
            done = {f["name"] for f in t["files"] if f.get("done")}
            for link in links:
                if self.blocked_reason():
                    raise Paused(self.blocked_reason())
                u = self._retry(lambda: self.rd.unrestrict(link))
                fname = clean(u["filename"])
                if fname in done:
                    continue
                dest = os.path.join(root, fname)
                entry = dict(name=fname, size=u.get("filesize", 0), done=False)
                t["files"].append(entry)
                self._retry(lambda: self._fetch(u["download"], dest, t))
                entry["done"] = True
                self._chown(dest)
                self._save()
            t["state"], t["completed"] = "completed", int(time.time())
            if config.DELETE_FROM_RD_ON_COMPLETE:
                self.rd.delete(t["rd_id"])
        except Paused as e:
            log.warning("%s: download paused (%s); will resume", t["name"], e)
            t["state"] = "rd_ready"
        except Exception as e:
            log.exception("download failed: %s", t["name"])
            t["state"], t["error"] = "error", str(e)
        self._save()

    def _retry(self, fn):
        delay = 2
        for i in range(config.DOWNLOAD_RETRIES):
            try:
                return fn()
            except Paused:
                raise
            except (requests.RequestException, RDError, OSError) as e:
                if i == config.DOWNLOAD_RETRIES - 1:
                    raise
                log.warning("retry %d after error: %s", i + 1, e)
                time.sleep(delay)
                delay = min(delay * 2, 60)

    def _fetch(self, url, dest, t):
        part = dest + ".part"
        pos = os.path.getsize(part) if os.path.exists(part) else 0
        hdr = {"Range": f"bytes={pos}-"} if pos else {}
        with requests.get(url, headers=hdr, stream=True, timeout=60) as r:
            if r.status_code == 416:
                pos = 0  # part file already complete or invalid
                os.replace(part, dest)
                return
            r.raise_for_status()
            if pos and r.status_code != 206:
                pos = 0
            with open(part, "ab" if pos else "wb") as f:
                for chunk in r.iter_content(1 << 18):
                    why = self.blocked_reason()
                    if why:
                        raise Paused(why)
                    f.write(chunk)
                    t["dl_bytes"] += len(chunk)
                    self.bytes_total += len(chunk)
                    self.limiter.throttle(len(chunk))
        os.replace(part, dest)

    @staticmethod
    def _chown(path):
        try:
            os.chown(path, config.PUID, config.PGID)
        except OSError:
            pass
