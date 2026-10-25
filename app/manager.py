"""Tracks torrents: adds to Real-Debrid, polls, downloads files, persists state."""
import base64
import json
import logging
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import config
from rd import RealDebrid, RDError

log = logging.getLogger("manager")
NOT_CACHED = "not cached on Real-Debrid (cached-only mode is on)"
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
        self.rd_error = ""           # last Real-Debrid problem that needs attention (bad token, ...); cleared on success
        self.limiter = RateLimiter()
        self.lock = threading.RLock()
        self.pending_ids = set()      # RD ids added but not yet tracked (cache probe in progress)
        self.reconciled = False
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
        if t and t["state"] == "error" and t.get("rd_id"):
            t["error"] = ""
            t["state"] = "rd_ready" if t.get("links") else "rd_pending"
            self._save()
            return True
        return False

    # ---- API operations
    def _track(self, h, category, name, state, rd_id="", error="", source=None):
        """Record a torrent unless the hash is already known; returns whether it was recorded."""
        with self.lock:
            if h in self.torrents:
                return False
            t = dict(
                hash=h, rd_id=rd_id, name=name or h, category=category or "",
                state=state, error=error, rd_progress=0, size=0, dl_bytes=0,
                added=int(time.time()), completed=0, files=[],
                save_path=os.path.join(config.DOWNLOAD_PATH, clean(category)) if category else config.DOWNLOAD_PATH,
            )
            if source:
                t["source"] = source
            self.torrents[h] = t
            self._save()
            return True

    def add(self, h, rid_fn, category, name, source=None):
        """Accept a torrent. With `source` (("magnet", str) or ("torrent", base64)), a valid add never fails:
        if Real-Debrid can't be reached right now (killswitch, no token, outage) it is queued and sent later, and
        if RD permanently rejects it it is listed as an error. Sonarr/Radarr only blacklist and re-search on a failed
        *tracked download*; a failed add leaves the episode stuck as grabbed."""
        with self.lock:
            if h in self.torrents:
                return
        why = self.gate() or ("no Real-Debrid token configured" if not config.RD_API_TOKEN else "")
        rid = None
        if not why:
            try:
                rid = rid_fn()
            except RDError as e:
                if e.permanent and not e.auth:
                    log.warning("%s: Real-Debrid rejected the add: %s", name or h, e)
                    self._track(h, category, name, "error", error=f"Real-Debrid rejected it: {e}")
                    return
                why = str(e)
            except requests.RequestException as e:
                why = str(e)
        if rid is None:
            if source is None:
                raise RuntimeError(why)
            log.warning("%s: can't reach Real-Debrid now (%s); queued, will be sent when possible", name or h, why)
            self._track(h, category, name, "queued_add", source=source)
            return
        self.pending_ids.add(rid)
        try:
            uncached = config.CACHED_ONLY and not self._probe_cached(rid)
            if uncached:
                self.rd.delete(rid)
            # An uncached torrent is accepted and reported as an error for the same reason (see above).
            ok = self._track(h, category, name, "error" if uncached else "rd_pending",
                             rd_id="" if uncached else rid, error=NOT_CACHED if uncached else "")
            if not ok and not uncached:   # added twice at once
                self.rd.delete(rid)
        finally:
            self.pending_ids.discard(rid)

    def _send_queued(self, t):
        """Send a locally queued add to Real-Debrid; stays queued while RD is unreachable."""
        kind, data = t["source"]
        try:
            rid = self.rd.add_magnet(data) if kind == "magnet" else self.rd.add_torrent(base64.b64decode(data))
        except RDError as e:
            if e.permanent and not e.auth:
                t["state"], t["error"] = "error", f"Real-Debrid rejected it: {e}"
                t.pop("source", None)
            elif e.auth:
                self.rd_error = f"Real-Debrid rejected the API token ({e.status}); check it in Settings"
            raise
        self.pending_ids.add(rid)
        try:
            if config.CACHED_ONLY and not self._probe_cached(rid):
                self.rd.delete(rid)
                t["state"], t["error"] = "error", NOT_CACHED
            else:
                t["rd_id"], t["state"] = rid, "rd_pending"
            t.pop("source", None)
        finally:
            self.pending_ids.discard(rid)

    def _probe_cached(self, rid):
        """True if Real-Debrid has the torrent ready within CACHED_PROBE_SECONDS (selecting its files if asked)."""
        deadline = time.monotonic() + config.CACHED_PROBE_SECONDS
        selected = False
        while True:
            info = self.rd.info(rid)
            s = info["status"]
            if s == "downloaded":
                return True
            if s == "waiting_files_selection" and not selected:
                self.rd.select_files(rid, self._select(info))
                selected = True
            elif s in ("magnet_error", "error", "virus", "dead"):
                return False
            if time.monotonic() >= deadline:
                log.info("%s: not cached on Real-Debrid (status %s)", info.get("filename") or rid, s)
                return False
            time.sleep(1)

    # ---- Real-Debrid account housekeeping
    def untracked(self):
        """Torrents in the RD account that debrid-fetch does not know about."""
        with self.lock:
            known = {t["rd_id"] for t in self.torrents.values()} | self.pending_ids
        return [x for x in self.rd.list_torrents() if x["id"] not in known]

    def purge_untracked(self, ids):
        """Delete the given RD torrents, skipping any that became tracked since they were listed."""
        with self.lock:
            known = {t["rd_id"] for t in self.torrents.values()} | self.pending_ids
            ids = [i for i in ids if i not in known]
        for i in ids:
            self.rd.delete(i)
        log.info("removed %d untracked torrent(s) from Real-Debrid", len(ids))
        return len(ids)

    def reconcile(self):
        """Once per start: fail torrents Real-Debrid no longer has, and remove stale orphaned .part files."""
        in_rd = {x["id"] for x in self.rd.list_torrents()}
        with self.lock:
            for t in self.torrents.values():
                if t["state"] != "completed" and t["rd_id"] and t["rd_id"] not in in_rd:
                    log.warning("%s: no longer on Real-Debrid", t["name"])
                    t["state"], t["error"] = "error", "removed from Real-Debrid"
            keep = {os.path.join(t["save_path"], clean(t["name"])) for t in self.torrents.values()}
            self._save()
        self._clean_orphans(keep)
        self.reconciled = True

    @staticmethod
    def _clean_orphans(keep, max_age=7 * 86400):
        """Delete .part files older than max_age in release folders that no tracked torrent owns."""
        base = config.DOWNLOAD_PATH
        for dirpath, dirs, files in os.walk(base):
            if dirpath[len(base):].count(os.sep) >= 3:
                dirs[:] = []
            if dirpath in keep:
                dirs[:] = []
                continue
            for f in files:
                p = os.path.join(dirpath, f)
                try:
                    if f.endswith(".part") and time.time() - os.path.getmtime(p) > max_age:
                        os.remove(p)
                        log.info("removed orphaned partial file %s", p)
                except OSError:
                    pass

    def delete(self, h, delete_files):
        with self.lock:
            t = self.torrents.pop(h, None)
            self._save()
        if t:
            if t["rd_id"]:
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
                    if not self.reconciled:
                        self.reconcile()
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
            if st == "queued_add" and config.RD_API_TOKEN and not self.blocked_reason():
                try:
                    self._send_queued(t)
                except (RDError, requests.RequestException) as e:
                    log.warning("%s: queued add not sent yet: %s", t["name"], e)
                continue
            if st in ("rd_pending", "rd_wait_select", "rd_downloading"):
                try:
                    self._poll_one(t)
                    self.rd_error = ""
                except RDError as e:
                    if e.auth:
                        self.rd_error = f"Real-Debrid rejected the API token ({e.status}); check it in Settings"
                        log.error("%s", self.rd_error)
                        break   # every other torrent would fail the same way
                    if e.status == 404:  # RD no longer has it (deleted on their side)
                        t["state"], t["error"] = "error", "not found on Real-Debrid"
                    log.warning("%s: %s", t["name"], e)
                    if e.status == 429:
                        time.sleep(min(e.retry_after or 10, 120))
                        break
                self._check_stall(t)
            if t["state"] == "rd_ready" and not self.blocked_reason() \
                    and sum(x["state"] == "downloading" for x in items) < config.MAX_PARALLEL_DOWNLOADS:
                t["state"] = "downloading"
                self.pool.submit(self._download, t)
        self._save()

    @staticmethod
    def _check_stall(t):
        """Fail torrents Real-Debrid has not advanced for STALL_TIMEOUT_HOURS (dead magnets would wait forever)."""
        limit = config.STALL_TIMEOUT_HOURS * 3600
        if limit and t["state"] in ("rd_pending", "rd_wait_select", "rd_downloading") \
                and time.time() - t.get("progress_at", t["added"]) > limit:
            log.warning("%s: no progress on Real-Debrid for %dh; giving up", t["name"], config.STALL_TIMEOUT_HOURS)
            t["state"], t["error"] = "error", f"stalled on Real-Debrid for {config.STALL_TIMEOUT_HOURS}h"

    def _poll_one(self, t):
        info = self.rd.info(t["rd_id"])
        s = info["status"]
        t["name"] = info.get("filename") or t["name"]
        t["size"] = info.get("bytes", t["size"])
        progress = info.get("progress", 0)
        if progress != t.get("rd_progress") or s != t.get("rd_status"):
            t["progress_at"] = int(time.time())
        t["rd_progress"], t["rd_status"] = progress, s
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
            t["files"] = t.get("files") or []
            done = {f["name"] for f in t["files"] if f.get("done")}
            self._check_space(root, t)
            for link in links:
                if self.blocked_reason():
                    raise Paused(self.blocked_reason())
                u = self._retry(lambda: self.rd.unrestrict(link))
                fname = clean(u["filename"])
                if fname in done:
                    continue
                dest = os.path.join(root, fname)
                entry = next((f for f in t["files"] if f["name"] == fname), None)
                if entry is None:
                    entry = dict(name=fname, size=u.get("filesize", 0), done=False)
                    t["files"].append(entry)
                cur = {"u": u}

                def attempt():
                    try:
                        self._fetch(cur["u"]["download"], dest, t, entry["size"])
                    except requests.HTTPError as e:
                        if e.response is not None and e.response.status_code in (403, 404, 410):
                            log.warning("%s: download link expired; asking Real-Debrid for a new one", fname)
                            cur["u"] = self.rd.unrestrict(link)
                        raise

                self._retry(attempt)
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

    def _check_space(self, root, t):
        """Pause (not fail) while the disk is too full, so freeing space lets the download carry on."""
        if not os.access(root, os.W_OK):
            raise OSError(f"{root} is not writable by uid {os.getuid()}; check PUID/PGID and the host folder owner")
        have = sum(os.path.getsize(os.path.join(root, f)) for f in os.listdir(root) if f.endswith(".part")) \
            if os.path.isdir(root) else 0
        need = max(0, t.get("size", 0) - have) + (100 << 20)
        free = shutil.disk_usage(root).free
        if free < need:
            raise Paused(f"not enough disk space in {root}: need {need >> 20} MB, {free >> 20} MB free")

    def _retry(self, fn):
        delay = 2
        for i in range(config.DOWNLOAD_RETRIES):
            try:
                return fn()
            except Paused:
                raise
            except RDError as e:
                if e.auth:
                    self.rd_error = f"Real-Debrid rejected the API token ({e.status}); check it in Settings"
                if e.permanent or i == config.DOWNLOAD_RETRIES - 1:
                    raise
                wait = min(e.retry_after or delay, 120)
                log.warning("retry %d after Real-Debrid error: %s (waiting %ds)", i + 1, e, wait)
                time.sleep(wait)
                delay = min(delay * 2, 60)
            except (requests.RequestException, OSError) as e:
                if i == config.DOWNLOAD_RETRIES - 1:
                    raise
                log.warning("retry %d after error: %s", i + 1, e)
                time.sleep(delay)
                delay = min(delay * 2, 60)

    def _fetch(self, url, dest, t, expected=0):
        """Download to dest + '.part' (resuming), check the size when known, then rename."""
        part = dest + ".part"
        pos = os.path.getsize(part) if os.path.exists(part) else 0
        hdr = {"Range": f"bytes={pos}-"} if pos else {}
        with requests.get(url, headers=hdr, stream=True, timeout=60) as r:
            if r.status_code == 416:  # range past the end: the part file is either complete or garbage
                if expected and pos == expected:
                    os.replace(part, dest)
                    return
                os.remove(part)
                raise OSError(f"{os.path.basename(dest)}: unusable partial file ({pos} bytes); restarting from zero")
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
        size = os.path.getsize(part)
        if expected and size != expected:
            os.remove(part)
            raise OSError(f"{os.path.basename(dest)}: got {size} bytes, Real-Debrid reported {expected}")
        os.replace(part, dest)

    @staticmethod
    def _chown(path):
        try:
            os.chown(path, config.PUID, config.PGID)
        except OSError:
            pass
