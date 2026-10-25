import os

import pytest


class FakeRD:
    def __init__(self):
        self.selected = None

    def select_files(self, rid, ids="all"):
        self.selected = ids


def test_file_selection_filters(client, env):
    import settings
    M = client.main.Manager
    info = {"filename": "x", "files": [{"id": 1, "path": "/a/movie.mkv", "bytes": 5 << 30},
                                        {"id": 2, "path": "/a/sample.mkv", "bytes": 10 << 20},
                                        {"id": 3, "path": "/a/info.nfo", "bytes": 100}]}
    assert M._select(info) == "1,2,3"
    settings.store.update({"MIN_FILE_SIZE_MB": 50})
    assert M._select(info) == "1"
    settings.store.update({"MIN_FILE_SIZE_MB": 0, "EXCLUDE_REGEX": "(sample|\\.nfo$)"})
    assert M._select(info) == "1"
    settings.store.update({"EXCLUDE_REGEX": "."})           # would exclude everything -> select all
    assert M._select(info) == "all"


def test_retry_and_manual_pause(client):
    mgr = client.main.mgr
    mgr.torrents["h"] = dict(hash="h", state="error", error="x", links=["l"])
    assert mgr.retry("h") and mgr.torrents["h"]["state"] == "rd_ready"
    mgr.paused = True
    assert mgr.blocked_reason() == "Paused manually"


def test_fetch_stops_when_blocked_and_keeps_part_file(client, env, tmp_path, monkeypatch):
    mgr = client.main.mgr
    import manager

    class R:
        status_code = 200
        def raise_for_status(self): pass
        def iter_content(self, n): yield b"x" * 10; yield b"y" * 10
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(manager.requests, "get", lambda *a, **k: R())
    calls = iter([None, "Killswitch"])
    mgr.gate = lambda: next(calls, "Killswitch")
    dest = str(tmp_path / "f.bin")
    t = {"dl_bytes": 0}
    with pytest.raises(manager.Paused):
        mgr._fetch("http://x", dest, t)
    assert open(dest + ".part", "rb").read() == b"x" * 10


def _resp(status, headers=None, text=""):
    class R:
        status_code = status
        content = b"x"
        def __init__(s): s.headers, s.text = headers or {}, text
        def json(s): return {}
    return R()


def test_rd_error_classification(client, monkeypatch):
    import rd
    r = rd.RealDebrid()
    for status, permanent, auth in ((401, True, True), (403, True, True), (404, True, False),
                                    (429, False, False), (503, False, False)):
        monkeypatch.setattr(r.s, "request", lambda *a, **k: _resp(status, {"Retry-After": "7"}))
        with pytest.raises(rd.RDError) as e:
            r.info("x")
        assert (e.value.permanent, e.value.auth) == (permanent, auth)
    assert e.value.retry_after == 7


def test_retry_gives_up_at_once_on_permanent_errors(client, monkeypatch):
    import manager, rd
    mgr = client.main.mgr
    monkeypatch.setattr(manager.time, "sleep", lambda s: None)
    calls = []

    def bad():
        calls.append(1)
        raise rd.RDError("nope", 401)

    with pytest.raises(rd.RDError):
        mgr._retry(bad)
    assert len(calls) == 1 and "token" in mgr.rd_error

    calls.clear()

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise rd.RDError("busy", 429, 1)
        return "ok"

    assert mgr._retry(flaky) == "ok" and len(calls) == 3


def _fake_get(monkeypatch, manager, status, body=b"", headers=None):
    class R:
        status_code = status
        def __init__(s): s.headers = headers or {}
        def raise_for_status(s): pass
        def iter_content(s, n): yield body
        def __enter__(s): return s
        def __exit__(s, *a): pass
    seen = []
    monkeypatch.setattr(manager.requests, "get", lambda url, **k: seen.append(k.get("headers")) or R())
    return seen


def test_fetch_checks_size(client, tmp_path, monkeypatch):
    import manager
    mgr = client.main.mgr
    dest = str(tmp_path / "f.bin")
    _fake_get(monkeypatch, manager, 200, b"abc")
    with pytest.raises(OSError, match="got 3 bytes"):
        mgr._fetch("http://x", dest, {"dl_bytes": 0}, expected=10)
    assert not os.path.exists(dest + ".part") and not os.path.exists(dest)
    mgr._fetch("http://x", dest, {"dl_bytes": 0}, expected=3)
    assert open(dest, "rb").read() == b"abc"


def test_fetch_416_only_trusts_complete_part(client, tmp_path, monkeypatch):
    import manager
    mgr = client.main.mgr
    dest = str(tmp_path / "f.bin")
    _fake_get(monkeypatch, manager, 416)
    open(dest + ".part", "wb").write(b"abc")
    mgr._fetch("http://x", dest, {"dl_bytes": 0}, expected=3)
    assert open(dest, "rb").read() == b"abc"
    open(dest + ".part", "wb").write(b"abcdef")
    os.remove(dest)
    with pytest.raises(OSError, match="unusable partial"):
        mgr._fetch("http://x", dest, {"dl_bytes": 0}, expected=3)
    assert not os.path.exists(dest + ".part")


def test_stall_timeout(client):
    import settings, time
    M = client.main.Manager
    t = dict(name="x", state="rd_pending", added=time.time() - 25 * 3600)
    M._check_stall(t)
    assert t["state"] == "error" and "stalled" in t["error"]
    t = dict(name="x", state="rd_downloading", added=0, progress_at=time.time() - 3600)
    M._check_stall(t)
    assert t["state"] == "rd_downloading"
    settings.store.update({"STALL_TIMEOUT_HOURS": 0})
    t = dict(name="x", state="rd_pending", added=0)
    M._check_stall(t)
    assert t["state"] == "rd_pending"


def test_download_refreshes_expired_link_and_pauses_when_disk_full(client, tmp_path, monkeypatch):
    import manager, requests
    mgr = client.main.mgr
    monkeypatch.setattr(manager.time, "sleep", lambda s: None)
    unrestricts = []
    mgr.rd.unrestrict = lambda link: unrestricts.append(link) or {"filename": "a.mkv", "filesize": 3,
                                                                  "download": f"http://dl/{len(unrestricts)}"}

    def fetch(url, dest, t, expected=0):
        if url.endswith("/1"):
            e = requests.HTTPError()
            e.response = type("R", (), {"status_code": 404})()
            raise e
        open(dest, "wb").write(b"abc")

    mgr._fetch = fetch
    t = dict(hash="h", name="N", state="downloading", links=["L"], files=[], size=3, dl_bytes=0, error="",
             save_path=str(tmp_path / "dl"))
    mgr.torrents["h"] = t
    mgr._download(t)
    assert t["state"] == "completed" and len(unrestricts) == 2  # once for the name, once more for a fresh link
    assert open(tmp_path / "dl" / "N" / "a.mkv", "rb").read() == b"abc"

    t.update(state="downloading", files=[], size=10 ** 18)
    mgr._download(t)
    assert t["state"] == "rd_ready"   # paused for space, not failed
