"""The release check: "will Real-Debrid serve this?" answered as a lookup instead of a failed grab."""
import pytest

H = "b" * 40
MAGNET = f"magnet:?xt=urn:btih:{H}&dn=Some.Release.2023.1080p.WEB-DL"


class ProbeRD:
    """Records what the check does with Real-Debrid, and answers with a canned verdict."""

    def __init__(self, reject=None, status="downloaded"):
        self.reject, self.status = reject, status
        self.added, self.infos, self.deleted = [], [], []

    def add_magnet(self, m):
        self.added.append(m)
        if self.reject:
            raise self.reject
        return "RID"

    def info(self, rid):
        self.infos.append(rid)
        return {"status": self.status, "filename": "Some.Release.2023.1080p.WEB-DL.mkv", "bytes": 1234 * 1024}

    def delete(self, rid):
        self.deleted.append(rid)


@pytest.fixture
def mgr(client):
    import settings
    settings.store.update({"RD_API_TOKEN": "t"})
    return client.main.mgr


def test_cached_release_is_accepted_and_cleaned_up(mgr):
    mgr.rd = ProbeRD()
    (r,) = mgr.probe([MAGNET])
    assert r["ok"] is True and r["verdict"] == "cached" and r["hash"] == H
    assert r["filename"].endswith(".mkv") and r["bytes"] == 1234 * 1024
    assert mgr.rd.added == [MAGNET]
    assert mgr.rd.deleted == ["RID"]          # a check never leaves a torrent in the account


def test_uncached_release_is_still_accepted(mgr):
    mgr.rd = ProbeRD(status="magnet_conversion")
    (r,) = mgr.probe([MAGNET])
    assert r["ok"] is True and r["verdict"] == "accepted"
    assert mgr.rd.deleted == ["RID"]


def test_infringing_release_is_reported_as_refused(mgr):
    import rd
    mgr.rd = ProbeRD(reject=rd.RDError('POST /torrents/addMagnet -> 451 {"error": "infringing_file", "error_code": 35}', 451))
    (r,) = mgr.probe([MAGNET])
    assert r["ok"] is False and r["verdict"] == "refused" and r["status"] == 451
    assert "infringing_file" in r["detail"]
    assert mgr.rd.deleted == []


def test_a_bare_info_hash_is_enough(mgr):
    mgr.rd = ProbeRD()
    (r,) = mgr.probe([H.upper()])
    assert r["ok"] is True
    assert mgr.rd.added == [f"magnet:?xt=urn:btih:{H.upper()}"]
    assert mgr.rd.added[0].lower() != ""     # RD accepts a magnet without a display name


def test_junk_is_rejected_without_calling_real_debrid(mgr):
    mgr.rd = ProbeRD()
    (r,) = mgr.probe(["not a magnet at all"])
    assert r["ok"] is False and r["verdict"] == "invalid"
    assert mgr.rd.added == []


def test_the_probe_is_capped(mgr):
    mgr.rd = ProbeRD()
    results = mgr.probe([MAGNET] * 40)
    assert len(results) == 25
    assert len(mgr.rd.added) == 25


def test_endpoint(client):
    import settings
    settings.store.update({"RD_API_TOKEN": "t"})
    client.main.mgr.rd = ProbeRD()
    r = client.post("/api/ui/rd-check", json={"items": [MAGNET]}, headers={"X-Requested-With": "debrid-fetch"})
    assert r.status_code == 200
    body = r.get_json()["results"]
    assert len(body) == 1 and body[0]["verdict"] == "cached"


def test_endpoint_needs_the_csrf_header(client):
    import settings
    settings.store.update({"RD_API_TOKEN": "t"})
    client.main.mgr.rd = ProbeRD()
    assert client.post("/api/ui/rd-check", json={"items": [MAGNET]}).status_code == 400


def test_endpoint_rejects_an_empty_request(client):
    import settings
    settings.store.update({"RD_API_TOKEN": "t"})
    client.main.mgr.rd = ProbeRD()
    r = client.post("/api/ui/rd-check", json={"items": []}, headers={"X-Requested-With": "debrid-fetch"})
    assert r.status_code == 400 and "magnet" in r.get_json()["error"]


def test_endpoint_without_a_token_says_so(client):
    import settings
    settings.store.update({"RD_API_TOKEN": ""})
    client.main.mgr.rd = ProbeRD()
    r = client.post("/api/ui/rd-check", json={"items": [MAGNET]}, headers={"X-Requested-With": "debrid-fetch"})
    body = r.get_json()["results"][0]
    assert body["verdict"] == "no token" and client.main.mgr.rd.added == []
