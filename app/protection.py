"""Optional network protection check with a killswitch.

Never blocks startup and never raises: a failed check is "unknown", which only matters if the user opted into
KILLSWITCH_STRICT. The killswitch is consulted by the Manager before polling and while downloading.
"""
import json
import logging
import threading
import time

import requests

import config

log = logging.getLogger("protection")
STRICT_AFTER = 3  # consecutive unknown results before strict mode trips


class Protection:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = dict(status="disabled", detail="Protection checks are off", public_ip="", checked_at=0)
        self._unknown_streak = 0
        self._wake = threading.Event()

    # ---- checks
    def _gluetun(self):
        base = config.GLUETUN_URL.rstrip("/")
        h = {"X-API-Key": config.GLUETUN_API_KEY} if config.GLUETUN_API_KEY else {}
        status, ip = None, ""
        for path in ("/v1/vpn/status", "/v1/openvpn/status"):
            r = requests.get(base + path, headers=h, timeout=8)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            status = r.json().get("status")
            break
        if status is None:
            raise RuntimeError("gluetun status endpoint not found")
        try:
            ip = requests.get(base + "/v1/publicip/ip", headers=h, timeout=8).json().get("public_ip", "")
        except (requests.RequestException, ValueError):
            pass
        if status == "running":
            return "protected", f"gluetun VPN is running", ip
        return "unprotected", f"gluetun VPN status: {status}", ip

    def _ip(self):
        homes = {x.strip() for x in config.HOME_IPS.split(",") if x.strip()}
        if not homes:
            raise RuntimeError("no home IP configured")
        r = requests.get(config.IP_CHECK_URL, timeout=8)
        r.raise_for_status()
        txt = r.text.strip()
        if txt.startswith("{"):
            j = json.loads(txt)
            txt = next((str(j[k]) for k in ("ip", "public_ip", "query", "origin") if k in j), "")
        if not txt:
            raise RuntimeError("could not read an IP from the lookup response")
        if txt in homes:
            return "unprotected", f"public IP {txt} is a home IP", txt
        return "protected", f"public IP {txt} is not a home IP", txt

    def check(self):
        if not config.PROTECTION_ENABLED:
            self._set("disabled", "Protection checks are off", "")
            self._unknown_streak = 0
            return
        try:
            status, detail, ip = self._gluetun() if config.PROTECTION_MODE == "gluetun" else self._ip()
        except Exception as e:
            status, detail, ip = "unknown", f"check failed: {e}", ""
        self._unknown_streak = self._unknown_streak + 1 if status == "unknown" else 0
        prev = self.state["status"]
        self._set(status, detail, ip)
        if prev != status:
            log.warning("protection %s -> %s (%s)", prev, status, detail)

    def _set(self, status, detail, ip):
        with self.lock:
            self.state = dict(status=status, detail=detail, public_ip=ip, checked_at=int(time.time()))

    # ---- killswitch
    def tripped(self):
        """Reason string when downloads must stop, else None."""
        if not (config.PROTECTION_ENABLED and config.KILLSWITCH_ENABLED):
            return None
        st = self.state["status"]
        if st == "unprotected":
            return "Killswitch: " + self.state["detail"]
        if st == "unknown" and config.KILLSWITCH_STRICT and self._unknown_streak >= STRICT_AFTER:
            return "Killswitch (strict): " + self.state["detail"]
        return None

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
        s.update(enabled=config.PROTECTION_ENABLED, killswitch=config.KILLSWITCH_ENABLED, mode=config.PROTECTION_MODE,
                 tripped=self.tripped())
        return s

    def recheck(self):
        self._wake.set()

    def run(self):
        while True:
            try:
                self.check()
            except Exception:
                log.exception("protection loop error")
            self._wake.wait(config.PROTECTION_INTERVAL)
            self._wake.clear()
