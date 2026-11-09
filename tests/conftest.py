import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k in ("RD_API_TOKEN", "WEBUI_USER", "WEBUI_PASS", "POLL_INTERVAL", "KILLSWITCH_ENABLED", "PROTECTION_ENABLED"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config"))
    monkeypatch.setenv("DOWNLOAD_PATH", str(tmp_path / "dl"))
    return monkeypatch


@pytest.fixture
def client(env):
    """Fresh app modules per test (settings store, manager and protection are module-level)."""
    for m in ("settings", "config", "auth", "protection", "arr", "manager", "rd", "ui", "main", "logbuffer", "version", "eventlog"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    main.app.testing = True
    c = main.app.test_client()
    c.main = main
    return c
