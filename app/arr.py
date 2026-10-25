"""Optional Sonarr/Radarr integration: blocklist a release the client can never fetch, then search again.

Sonarr/Radarr map every qBittorrent state to "warning" at worst, so a torrent client cannot make them blocklist a
release. Their own API can: mark the grab failed (adds it to the blocklist) and run a search, which goes through the
usual quality profiles. This module never grabs a release itself.
"""
import logging

import requests

import config

log = logging.getLogger("arr")
SLOTS = 4


class ArrError(Exception):
    def __init__(self, msg, auth=False):
        super().__init__(msg)
        self.auth = auth


class Arr:
    def __init__(self):
        self.s = requests.Session()

    @staticmethod
    def slots():
        out = []
        for i in range(1, SLOTS + 1):
            url, key = getattr(config, f"ARR_{i}_URL"), getattr(config, f"ARR_{i}_API_KEY")
            cats = {c.strip().lower() for c in getattr(config, f"ARR_{i}_CATEGORIES").split(",") if c.strip()}
            if url and key and cats:
                out.append(dict(n=i, type=getattr(config, f"ARR_{i}_TYPE"), url=url.rstrip("/"), key=key, cats=cats))
        return out

    def slot_for(self, category):
        for s in self.slots():
            if (category or "").lower() in s["cats"]:
                return s

    def _call(self, slot, method, path, **kw):
        try:
            r = self.s.request(method, slot["url"] + "/api/v3" + path, headers={"X-Api-Key": slot["key"]},
                               timeout=20, **kw)
        except requests.RequestException as e:
            raise ArrError(f"{slot['type']} #{slot['n']} unreachable: {type(e).__name__}")
        if r.status_code in (401, 403):
            raise ArrError(f"{slot['type']} #{slot['n']} rejected the API key ({r.status_code})", auth=True)
        if r.status_code >= 400:
            raise ArrError(f"{slot['type']} #{slot['n']} {method} {path} -> {r.status_code}")
        try:
            return r.json()
        except ValueError:
            return None

    def blocklist(self, slot, hash_, attempts, max_attempts):
        """Mark the grab(s) for this torrent failed and search again.

        Returns "retry" (the arr hasn't recorded the grab yet), "capped" (every item already hit the attempt cap) or
        "done". `attempts` maps "<slot>:<item id>" to the number of times this was done; it is updated in place.
        """
        sonarr = slot["type"] == "sonarr"
        data = self._call(slot, "GET", "/history", params=dict(page=1, pageSize=100, downloadId=hash_.upper())) or {}
        grabs = [r for r in data.get("records", [])
                 if r.get("eventType") == "grabbed" and (r.get("downloadId") or "").lower() == hash_.lower()]
        if not grabs:
            return "retry"
        item_key = "episodeId" if sonarr else "movieId"
        todo = [g for g in grabs if attempts.get(f"{slot['n']}:{g.get(item_key)}", 0) < max_attempts]
        if not todo:
            return "capped"
        for g in todo:
            self._call(slot, "POST", f"/history/failed/{g['id']}")
        ids = sorted({g[item_key] for g in todo if g.get(item_key) is not None})
        for i in ids:
            attempts[f"{slot['n']}:{i}"] = attempts.get(f"{slot['n']}:{i}", 0) + 1
        if ids:
            self._call(slot, "POST", "/command", json=(
                {"name": "EpisodeSearch", "episodeIds": ids} if sonarr else {"name": "MoviesSearch", "movieIds": ids}))
        return "done"
