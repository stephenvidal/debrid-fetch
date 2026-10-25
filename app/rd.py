import requests

import config


class RDError(Exception):
    """A Real-Debrid API failure. `status` is the HTTP status (None for non-HTTP errors)."""

    def __init__(self, msg, status=None, retry_after=None):
        super().__init__(msg)
        self.status = status
        self.retry_after = retry_after

    @property
    def permanent(self):
        """Retrying cannot help: bad token, forbidden, missing or invalid request (anything 4xx but 408/429)."""
        return self.status is not None and 400 <= self.status < 500 and self.status not in (408, 429)

    @property
    def auth(self):
        return self.status in (401, 403)


class RealDebrid:
    def __init__(self):
        self.s = requests.Session()

    def _headers(self):
        return {"Authorization": f"Bearer {config.RD_API_TOKEN}"}

    def user(self, token=None):
        kw = {"headers": {"Authorization": f"Bearer {token}"}} if token else {}
        return self._req("GET", "/user", **kw)

    def _req(self, method, path, **kw):
        r = self.s.request(method, config.RD_API_URL + path, timeout=30, **{"headers": self._headers(), **kw})
        if r.status_code >= 400:
            try:
                wait = float(r.headers.get("Retry-After", ""))
            except ValueError:
                wait = None
            raise RDError(f"{method} {path} -> {r.status_code} {r.text[:200]}", r.status_code, wait)
        return r.json() if r.content else None

    def add_magnet(self, magnet):
        return self._req("POST", "/torrents/addMagnet", data={"magnet": magnet})["id"]

    def add_torrent(self, raw):
        return self._req("PUT", "/torrents/addTorrent", data=raw)["id"]

    def info(self, rid):
        return self._req("GET", f"/torrents/info/{rid}")

    def select_files(self, rid, ids="all"):
        self._req("POST", f"/torrents/selectFiles/{rid}", data={"files": ids})

    def delete(self, rid):
        try:
            self._req("DELETE", f"/torrents/delete/{rid}")
        except RDError:
            pass

    def unrestrict(self, link):
        return self._req("POST", "/unrestrict/link", data={"link": link})
