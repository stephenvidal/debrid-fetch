import pytest


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
        self.text = data if isinstance(data, str) else ""

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture
def prot(client, env):
    import settings
    settings.store.update({"PROTECTION_ENABLED": True, "KILLSWITCH_ENABLED": True})
    return client.main.prot


def fake(monkeypatch, routes):
    def get(url, **kw):
        for k, v in routes.items():
            if url.endswith(k):
                if isinstance(v, Exception):
                    raise v
                return v
        return Resp({}, 404)
    monkeypatch.setattr("protection.requests.get", get)


def test_disabled_by_default(client):
    client.main.prot.check()
    assert client.main.prot.snapshot()["status"] == "disabled" and not client.main.prot.tripped()


def test_gluetun_running_and_stopped(prot, monkeypatch):
    fake(monkeypatch, {"/v1/vpn/status": Resp({"status": "running"}), "/v1/publicip/ip": Resp({"public_ip": "9.9.9.9"})})
    prot.check()
    assert prot.state["status"] == "protected" and prot.state["public_ip"] == "9.9.9.9" and not prot.tripped()
    fake(monkeypatch, {"/v1/vpn/status": Resp({"status": "stopped"})})
    prot.check()
    assert prot.state["status"] == "unprotected" and "Killswitch" in prot.tripped()


def test_gluetun_falls_back_to_openvpn_endpoint(prot, monkeypatch):
    fake(monkeypatch, {"/v1/openvpn/status": Resp({"status": "running"})})
    prot.check()
    assert prot.state["status"] == "protected"


def test_unreachable_is_unknown_and_does_not_trip_unless_strict(prot, monkeypatch):
    import settings
    fake(monkeypatch, {"/v1/vpn/status": OSError("boom")})
    for _ in range(5):
        prot.check()
    assert prot.state["status"] == "unknown" and prot.tripped() is None
    settings.store.update({"KILLSWITCH_STRICT": True})
    assert "strict" in prot.tripped()


def test_ip_mode(prot, monkeypatch):
    import settings
    settings.store.update({"PROTECTION_MODE": "ip", "HOME_IPS": "1.2.3.4, 5.6.7.8"})
    fake(monkeypatch, {"api.ipify.org": Resp("1.2.3.4")})
    prot.check()
    assert prot.state["status"] == "unprotected" and prot.tripped()
    fake(monkeypatch, {"api.ipify.org": Resp('{"ip": "10.0.0.1"}')})
    prot.check()
    assert prot.state["status"] == "protected" and not prot.tripped()


def test_ip_mode_without_home_ip_is_unknown(prot, monkeypatch):
    import settings
    settings.store.update({"PROTECTION_MODE": "ip"})
    prot.check()
    assert prot.state["status"] == "unknown"


def test_killswitch_pauses_manager_and_refuses_add(client, prot, monkeypatch):
    mgr = client.main.mgr
    prot.state["status"] = "unprotected"
    assert mgr.blocked_reason()
    with pytest.raises(RuntimeError):
        mgr.add("abc", lambda: "rid", "tv", "x")
    r = client.post("/api/v2/torrents/add", data={"urls": "magnet:?xt=urn:btih:" + "a" * 40, "category": "tv"})
    # accepted and queued: a failed add would strand the episode in Sonarr/Radarr
    assert r.data == b"Ok." and mgr.torrents["a" * 40]["state"] == "queued_add"
    prot.state["status"] = "protected"
    assert mgr.blocked_reason() is None
