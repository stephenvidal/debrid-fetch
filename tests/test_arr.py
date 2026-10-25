"""Optional Sonarr/Radarr blocklisting."""
import pytest

H = "a" * 40
MAGNET = f"magnet:?xt=urn:btih:{H}&dn=x"


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


@pytest.fixture
def arr_env(client):
    import settings
    settings.store.update({"RD_API_TOKEN": "t", "ARR_ENABLED": True,
                           "ARR_1_TYPE": "sonarr", "ARR_1_URL": "http://sonarr:8989/", "ARR_1_API_KEY": "k1",
                           "ARR_1_CATEGORIES": "tv, tv-anime",
                           "ARR_2_TYPE": "radarr", "ARR_2_URL": "http://radarr:7878", "ARR_2_API_KEY": "k2",
                           "ARR_2_CATEGORIES": "movies"})
    mgr = client.main.mgr
    calls = []
    history = {"records": [{"id": 411, "eventType": "grabbed", "downloadId": H.upper(), "episodeId": 3273},
                           {"id": 400, "eventType": "downloadFolderImported", "downloadId": H.upper(), "episodeId": 3273}]}

    def request(method, url, **kw):
        calls.append((method, url, kw.get("json"), kw["headers"]["X-Api-Key"]))
        if method == "GET" and url.endswith("/history"):
            return FakeResp(200, mgr._history)
        return FakeResp(200, {})

    mgr._history = history
    mgr.arr.s.request = request
    mgr.calls = calls
    return mgr


def test_slots_need_url_key_and_categories(arr_env):
    arr = arr_env.arr
    assert [s["n"] for s in arr.slots()] == [1, 2]
    assert arr.slot_for("TV-Anime")["type"] == "sonarr" and arr.slot_for("movies")["type"] == "radarr"
    assert arr.slot_for("other") is None and arr.slot_for("") is None


def test_blocklist_marks_failed_then_searches(arr_env):
    mgr = arr_env
    assert mgr.arr.blocklist(mgr.arr.slot_for("tv"), H, mgr.arr_attempts, 3) == "done"
    assert [(m, u.replace("http://sonarr:8989/api/v3", "")) for m, u, *_ in mgr.calls] == [
        ("GET", "/history"), ("POST", "/history/failed/411"), ("POST", "/command")]   # only the *grabbed* record
    assert mgr.calls[-1][2] == {"name": "EpisodeSearch", "episodeIds": [3273]}
    assert all(c[3] == "k1" for c in mgr.calls) and mgr.arr_attempts == {"1:3273": 1}


def test_radarr_uses_movie_ids(arr_env):
    mgr = arr_env
    mgr._history = {"records": [{"id": 7, "eventType": "grabbed", "downloadId": H, "movieId": 55}]}
    mgr.arr.blocklist(mgr.arr.slot_for("movies"), H, mgr.arr_attempts, 3)
    assert mgr.calls[-1][2] == {"name": "MoviesSearch", "movieIds": [55]}


def test_cap_and_missing_history(arr_env):
    mgr = arr_env
    slot = mgr.arr.slot_for("tv")
    mgr.arr_attempts["1:3273"] = 3
    assert mgr.arr.blocklist(slot, H, mgr.arr_attempts, 3) == "capped"
    assert not any(m == "POST" for m, *_ in mgr.calls)           # nothing blocklisted or searched past the cap
    mgr._history = {"records": []}
    assert mgr.arr.blocklist(slot, H, {}, 3) == "retry"


def test_bad_key_is_reported(arr_env):
    import arr
    mgr = arr_env
    mgr.arr.s.request = lambda *a, **k: FakeResp(401)
    with pytest.raises(arr.ArrError) as e:
        mgr.arr.blocklist(mgr.arr.slot_for("tv"), H, {}, 3)
    assert e.value.auth


def test_rd_rejection_triggers_blocklist_from_poll(arr_env, client):
    import rd
    mgr = arr_env

    class RD:
        def add_magnet(self, m): raise rd.RDError("infringing_file", 451)
        def list_torrents(self): return []

    mgr.rd = RD()
    client.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv"})
    t = mgr.torrents[H]
    assert t["state"] == "error" and t["arr"]["next"] == 0
    mgr.poll()
    assert mgr.arr_attempts == {"1:3273": 1}
    assert any(c[1].endswith("/history/failed/411") for c in mgr.calls)
    assert H not in mgr.torrents                                   # removed once the arr has taken over


def test_retries_until_arr_has_the_grab(arr_env, client, monkeypatch):
    import manager, rd
    mgr = arr_env

    class RD:
        def add_magnet(self, m): raise rd.RDError("infringing_file", 451)
        def list_torrents(self): return []

    mgr.rd = RD()
    full, mgr._history = mgr._history, {"records": []}
    client.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv"})
    t = mgr.torrents[H]
    mgr.poll()
    assert "arr" in t and t["arr"]["next"] > 0                    # grab not recorded yet: try again later
    mgr._history, t["arr"]["next"] = full, 0
    mgr.poll()
    assert H not in mgr.torrents and mgr.arr_attempts


def test_off_by_default_and_uncategorised_never_flagged(client):
    import settings
    mgr = client.main.mgr
    settings.store.update({"RD_API_TOKEN": "t"})

    class RD:
        def add_magnet(self, m):
            import rd
            raise rd.RDError("infringing_file", 451)

    mgr.rd = RD()
    client.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv"})
    assert "arr" not in mgr.torrents[H]                            # ARR_ENABLED is false


def test_arr_secrets_never_leave_the_server(client):
    import settings
    settings.store.update({"ARR_1_API_KEY": "supersecret"})
    d = {x["key"]: x for x in settings.store.describe()}
    assert d["ARR_1_API_KEY"]["is_set"] and "value" not in d["ARR_1_API_KEY"]
    assert "supersecret" not in client.get("/api/ui/settings").get_data(as_text=True)


def test_capped_item_is_kept_for_the_user(arr_env, client):
    import rd
    mgr = arr_env
    mgr.arr_attempts["1:3273"] = 3

    class RD:
        def add_magnet(self, m): raise rd.RDError("infringing_file", 451)

    mgr.rd = RD()
    client.post("/api/v2/torrents/add", data={"urls": MAGNET, "category": "tv"})
    mgr.poll()
    assert H in mgr.torrents and "arr" not in mgr.torrents[H]      # nothing done, so nothing removed
