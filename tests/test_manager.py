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
