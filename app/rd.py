import requests

import os
API = os.environ.get("RD_API_URL", "https://api.real-debrid.com/rest/1.0")


class RDError(Exception):
    pass


class RealDebrid:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _req(self, method, path, **kw):
        r = self.s.request(method, API + path, timeout=30, **kw)
        if r.status_code >= 400:
            raise RDError(f"{method} {path} -> {r.status_code} {r.text[:200]}")
        return r.json() if r.content else None

    def add_magnet(self, magnet):
        return self._req("POST", "/torrents/addMagnet", data={"magnet": magnet})["id"]

    def add_torrent(self, raw):
        return self._req("PUT", "/torrents/addTorrent", data=raw)["id"]

    def info(self, rid):
        return self._req("GET", f"/torrents/info/{rid}")

    def select_all(self, rid):
        self._req("POST", f"/torrents/selectFiles/{rid}", data={"files": "all"})

    def delete(self, rid):
        try:
            self._req("DELETE", f"/torrents/delete/{rid}")
        except RDError:
            pass

    def unrestrict(self, link):
        return self._req("POST", "/unrestrict/link", data={"link": link})
