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


class Manager:
    def __init__(self):
        self.rd = RealDebrid(config.RD_TOKEN)
        self.lock = threading.RLock()
        self.state_file = os.path.join(config.CONFIG_PATH, "state.json")
        self.torrents = {}   # hash -> dict
        self.categories = {}
        self.pool = ThreadPoolExecutor(config.MAX_PARALLEL)
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

    # ---- API operations
    def add(self, h, rid_fn, category, name):
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
                self.poll()
            except Exception:
                log.exception("poll failed")
            time.sleep(config.POLL_INTERVAL)

    def poll(self):
        with self.lock:
            items = list(self.torrents.values())
        for t in items:
            st = t["state"]
            if st in ("rd_pending", "rd_wait_select", "rd_downloading"):
                try:
                    self._poll_one(t)
                except RDError as e:
                    log.warning("%s: %s", t["name"], e)
            if t["state"] == "rd_ready":
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
            self.rd.select_all(t["rd_id"])
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
        except Exception as e:
            log.exception("download failed: %s", t["name"])
            t["state"], t["error"] = "error", str(e)
        self._save()

    def _retry(self, fn):
        delay = 2
        for i in range(config.DOWNLOAD_RETRIES):
            try:
                return fn()
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
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    t["dl_bytes"] += len(chunk)
        os.replace(part, dest)

    @staticmethod
    def _chown(path):
        try:
            os.chown(path, config.PUID, config.PGID)
        except OSError:
            pass
