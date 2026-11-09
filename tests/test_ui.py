import json
import os

H = {"X-Requested-With": "debrid-fetch"}


def put(c, values=None, reset=None):
    return c.put("/api/ui/settings", json={"values": values or {}, "reset": reset or []}, headers=H)


def field(c, key):
    return next(f for f in c.get("/api/ui/settings").get_json()["fields"] if f["key"] == key)


def test_precedence_ui_over_env_over_default(client, env):
    assert field(client, "POLL_INTERVAL")["source"] == "default"
    env.setenv("POLL_INTERVAL", "30")
    assert (field(client, "POLL_INTERVAL")["value"], field(client, "POLL_INTERVAL")["source"]) == (30, "env")
    assert put(client, {"POLL_INTERVAL": "45"}).status_code == 200
    assert (field(client, "POLL_INTERVAL")["value"], field(client, "POLL_INTERVAL")["source"]) == (45, "ui")
    assert client.main.config.POLL_INTERVAL == 45          # live, no restart
    assert put(client, reset=["POLL_INTERVAL"]).status_code == 200
    assert field(client, "POLL_INTERVAL")["value"] == 30


def test_only_changed_keys_persisted(client):
    put(client, {"MAX_PARALLEL_DOWNLOADS": 4})
    path = os.path.join(os.environ["CONFIG_PATH"], "settings.json")
    assert json.load(open(path)) == {"MAX_PARALLEL_DOWNLOADS": 4}
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_secret_never_returned_and_keep_semantics(client):
    put(client, {"RD_API_TOKEN": "supersecrettoken"})
    r = client.get("/api/ui/settings")
    assert b"supersecrettoken" not in r.data
    f = field(client, "RD_API_TOKEN")
    assert f["is_set"] and "value" not in f
    put(client, {"RD_API_TOKEN": None})                    # omitted -> kept
    assert client.main.config.RD_API_TOKEN == "supersecrettoken"
    put(client, {"RD_API_TOKEN": ""})                      # empty -> cleared
    assert client.main.config.RD_API_TOKEN == ""


def test_validation(client):
    r = put(client, {"POLL_INTERVAL": "abc", "EXCLUDE_REGEX": "(", "DOWNLOAD_PATH": "rel", "PORT": 1})
    errs = r.get_json()["errors"]
    assert r.status_code == 422 and set(errs) == {"POLL_INTERVAL", "EXCLUDE_REGEX", "DOWNLOAD_PATH", "PORT"}
    assert field(client, "POLL_INTERVAL")["source"] == "default"      # nothing saved on error


def test_username_requires_password(client):
    assert put(client, {"WEBUI_USER": "a"}).status_code == 422


def test_csrf_header_required(client):
    assert client.put("/api/ui/settings", json={"values": {}}).status_code == 400


def test_auth_flow_and_arr_api_shares_it(client):
    assert client.get("/api/v2/app/version").status_code == 200          # open by default
    r = put(client, {"WEBUI_USER": "me", "WEBUI_PASS": "pw"})
    assert r.status_code == 200                                          # saver stays logged in
    assert client.get("/api/ui/status").status_code == 200
    client.delete_cookie("SID")
    assert client.get("/api/ui/status").status_code == 403
    assert client.get("/api/v2/app/version").status_code == 403
    assert client.get("/api/ui/session").get_json() == {"auth_required": True, "authed": False}
    assert client.post("/api/ui/login", json={"username": "me", "password": "bad"}).status_code == 401
    assert client.get("/api/v2/auth/login?username=me&password=bad").data == b"Fails."
    assert client.post("/api/ui/login", json={"username": "me", "password": "pw"}).status_code == 200
    assert client.get("/api/ui/status").status_code == 200
    assert client.get("/api/v2/app/version").status_code == 200


def test_login_lockout(client):
    put(client, {"WEBUI_USER": "me", "WEBUI_PASS": "pw"})
    client.delete_cookie("SID")
    for _ in range(5):
        client.post("/api/ui/login", json={"username": "me", "password": "x"})
    assert client.post("/api/ui/login", json={"username": "me", "password": "pw"}).status_code == 429


def test_arr_login_cookie_is_stable_across_restart(client):
    put(client, {"WEBUI_USER": "me", "WEBUI_PASS": "pw"})
    a = client.main.auth.token()
    assert a == client.main.auth.token() and "pw" not in a


def test_index_served(client):
    assert b"debrid-fetch" in client.get("/").data
    assert client.get("/static/app.js").status_code == 200


def test_index_and_assets_load_when_logged_out(client):
    put(client, {"WEBUI_USER": "me", "WEBUI_PASS": "pw"})
    client.delete_cookie("SID")
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/ui/settings").status_code == 403


def test_index_versions_asset_urls(client):
    from version import __version__
    html = client.get("/").get_data(as_text=True)
    assert f"/static/app.css?v={__version__}" in html and f"/static/app.js?v={__version__}" in html
    assert client.get("/").headers["Cache-Control"] == "no-cache"
    assert client.get(f"/static/app.css?v={__version__}").status_code == 200


def test_a_ui_value_that_shadows_a_different_env_value_is_reported(client, env):
    """The silent override that once kept the app on a dead token: .env looked right, the UI won, nothing said so."""
    import settings
    env.setenv("RD_API_TOKEN", "from-env")
    settings.store.update({"RD_API_TOKEN": "from-ui"})
    assert [d["key"] for d in settings.store.drift()] == ["RD_API_TOKEN"]
    st = client.get("/api/ui/status").get_json()
    assert any("Real-Debrid token" in w for w in st["config_warnings"])
    assert "from-ui" not in json.dumps(st["config_warnings"])      # never echo the secret


def test_no_drift_warning_when_the_ui_and_env_agree(client, env):
    import settings
    env.setenv("RD_API_TOKEN", "same")
    settings.store.update({"RD_API_TOKEN": "same"})
    assert settings.store.drift() == []
    assert client.get("/api/ui/status").get_json()["config_warnings"] == []
