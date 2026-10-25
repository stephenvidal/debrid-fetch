"""Shared auth for the web UI and the qBittorrent-style API.

Auth is on when WEBUI_USER is set. The session cookie (SID) is an HMAC of the credentials under a random key kept
in /config/secret.key: it survives restarts (so the arrs never need to re-login) and changing the password
invalidates every session.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time

import config

COOKIE = "SID"
MAX_FAILS, WINDOW = 5, 300
_fails = {}
_lock = threading.Lock()


def required():
    return bool(config.WEBUI_USER)


def _key():
    path = os.path.join(config.CONFIG_PATH, "secret.key")
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        key = secrets.token_bytes(32)
        try:
            os.makedirs(config.CONFIG_PATH, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        except OSError:
            pass  # unwritable config dir: sessions simply won't survive a restart
        return key


def token():
    msg = f"{config.WEBUI_USER}\0{config.WEBUI_PASS}".encode()
    return hmac.new(_key(), msg, hashlib.sha256).hexdigest()


def valid_cookie(value):
    return not required() or (value is not None and hmac.compare_digest(value, token()))


def check_credentials(user, password):
    ok_u = hmac.compare_digest(user.encode(), config.WEBUI_USER.encode())
    ok_p = hmac.compare_digest(password.encode(), config.WEBUI_PASS.encode())
    return ok_u and ok_p


def locked_out(addr):
    with _lock:
        recent = [t for t in _fails.get(addr, []) if time.time() - t < WINDOW]
        _fails[addr] = recent
        return len(recent) >= MAX_FAILS


def record_failure(addr):
    with _lock:
        _fails.setdefault(addr, []).append(time.time())


def clear_failures(addr):
    with _lock:
        _fails.pop(addr, None)
