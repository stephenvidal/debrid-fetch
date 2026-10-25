"""Contract tests: the qBittorrent Web API calls Sonarr/Radarr make, in the shape they send them."""
import base64
import os

import pytest

H = "a" * 40
MAGNET = f"magnet:?xt=urn:btih:{H}&dn=Show.S01E01"


class RDStub:
    def __init__(self):
        self.added, self.deleted, self.fail = [], [], None

    def add_magnet(self, m):
        if self.fail:
            raise self.fail
        self.added.append(m)
        return "RID"

    def add_torrent(self, raw):
        if self.fail:
            raise self.fail
        self.added.append(raw)
        return "RID"

    def delete(self, rid): self.deleted.append(rid)
    def list_torrents(self): return []


@pytest.fixture
def api(client):
    client.main.mgr.rd = RDStub()
    import settings
    settings.store.update({"RD_API_TOKEN": "t"})
    return client


def test_arr_connection_test_and_setup_calls(api):
    assert api.post("/api/v2/auth/login", data={"username": "u", "password": "p"}).data == b"Ok."
    assert api.get("/api/v2/app/webapiVersion").data.decode().startswith("2.")
    assert api.get("/api/v2/app/version").data.startswith(b"v")
    assert "save_path" in api.get("/api/v2/app/preferences").get_json()
    assert api.post("/api/v2/torrents/createCategory", data={"category": "tv", "savePath": ""}).status_code == 200
    assert "tv" in api.get("/api/v2/torrents/categories").get_json()
    assert api.get("/api/v2/torrents/info?category=tv").get_json() == []
    assert api.get("/api/v2/torrents/tags").get_json() == []
    assert api.get("/api/v2/transfer/info").get_json()["connection_status"] == "connected"
    assert api.get("/api/v2/sync/maindata").get_json()["full_update"] is True


def test_add_magnet_then_info_by_category_and_hash(api):
    r = api.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv", "paused": "false"})
    assert r.data == b"Ok."
    for q in ("category=tv", f"hashes={H.upper()}", f"category=tv&hashes={H}", "filter=downloading"):
        rows = api.get("/api/v2/torrents/info?" + q).get_json()
        assert [x["hash"] for x in rows] == [H], q
    row = api.get("/api/v2/torrents/info?category=tv").get_json()[0]
    for key in ("hash", "name", "size", "progress", "state", "category", "save_path", "content_path", "eta"):
        assert key in row
    assert api.get("/api/v2/torrents/info?category=movies").get_json() == []
    assert api.get("/api/v2/torrents/info?filter=errored").get_json() == []
    assert api.get(f"/api/v2/torrents/properties?hash={H}").get_json()["save_path"]
    assert api.get(f"/api/v2/torrents/files?hash={H}").status_code == 200


def test_add_torrent_file_upload(api):
    from bencode import info_hash
    raw = b"d4:infod6:lengthi3e4:name1:x12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee"
    r = api.post("/api/v2/torrents/add", data={"torrents": (__import__("io").BytesIO(raw), "x.torrent"), "category": "tv"},
                 content_type="multipart/form-data")
    assert r.data == b"Ok."
    assert info_hash(raw) in api.get("/api/v2/torrents/info").get_json()[0]["hash"]


def test_delete_removes_from_rd_and_state(api):
    api.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv"})
    assert api.post("/api/v2/torrents/delete", data={"hashes": H, "deleteFiles": "true"}).status_code == 200
    assert api.get("/api/v2/torrents/info").get_json() == [] and api.main.mgr.rd.deleted == ["RID"]


def test_v4_and_v5_noop_endpoints_exist(api):
    api.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv"})
    for ep in ("pause", "resume", "stop", "start", "topPrio", "bottomPrio", "increasePrio", "decreasePrio",
               "setShareLimits", "addTags", "removeTags", "createTags", "deleteTags", "recheck", "reannounce",
               "setForceStart", "setAutoManagement"):
        assert api.post(f"/api/v2/torrents/{ep}", data={"hashes": H}).status_code == 200, ep


def test_valid_add_never_fails_when_rd_unreachable(api):
    import rd
    mgr = api.main.mgr
    mgr.rd.fail = rd.RDError("boom", 503)
    assert api.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv"}).data == b"Ok."
    assert mgr.torrents[H]["state"] == "queued_add"
    assert api.get("/api/v2/torrents/info").get_json()[0]["state"] == "queuedDL"
    # RD recovers: the poll loop sends it
    mgr.rd.fail = None
    mgr.poll()
    assert mgr.torrents[H]["state"] == "rd_pending" and mgr.torrents[H]["rd_id"] == "RID"
    assert mgr.rd.added == [MAGNET] and "source" not in mgr.torrents[H]


def test_rd_rejection_is_listed_as_error_so_the_arr_blacklists(api):
    import rd
    mgr = api.main.mgr
    mgr.rd.fail = rd.RDError("infringing_file", 451)
    assert api.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv"}).data == b"Ok."
    row = api.get("/api/v2/torrents/info").get_json()[0]
    assert row["state"] == "error" and row["hash"] == H and mgr.torrents[H]["error"]


def test_bad_token_queues_instead_of_blacklisting_good_releases(api):
    import rd
    mgr = api.main.mgr
    mgr.rd.fail = rd.RDError("bad_token", 401)
    api.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv"})
    assert mgr.torrents[H]["state"] == "queued_add"
    mgr.poll()                                     # still failing: stays queued, flags the token problem
    assert mgr.torrents[H]["state"] == "queued_add" and "token" in mgr.rd_error


def test_queued_torrent_file_survives_restart(api, tmp_path):
    import rd, importlib, sys
    mgr = api.main.mgr
    mgr.rd.fail = rd.RDError("boom", 503)
    raw = b"d4:infod6:lengthi3e4:name1:x12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee"
    api.post("/api/v2/torrents/add", data={"torrents": (__import__("io").BytesIO(raw), "x.torrent")},
             content_type="multipart/form-data")
    t = next(iter(mgr.torrents.values()))
    mgr2 = type(mgr)()                             # fresh Manager reads state.json
    t2 = mgr2.torrents[t["hash"]]
    assert t2["state"] == "queued_add" and base64.b64decode(t2["source"][1]) == raw


def test_auth_required_when_credentials_set(client, env):
    import settings
    settings.store.update({"WEBUI_USER": "u", "WEBUI_PASS": "p"})
    assert client.get("/api/v2/torrents/info").status_code == 403
    assert client.post("/api/v2/auth/login", data={"username": "u", "password": "bad"}).data == b"Fails."
    assert client.post("/api/v2/auth/login", data={"username": "u", "password": "p"}).data == b"Ok."
    assert client.get("/api/v2/torrents/info").status_code == 200   # cookie from login is kept by the test client
