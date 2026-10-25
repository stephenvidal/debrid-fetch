import requests

import config


class RDError(Exception):
    pass


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
            raise RDError(f"{method} {path} -> {r.status_code} {r.text[:200]}")
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
