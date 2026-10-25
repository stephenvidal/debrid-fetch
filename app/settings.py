"""Settings schema and store.

Precedence per key: value saved from the web UI (/config/settings.json) > environment variable > default.
Only keys the user actually changed are written to settings.json, so everything else keeps following the env.
"""
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field

log = logging.getLogger("settings")

GROUPS = ["Real-Debrid", "Downloads", "File selection", "Access", "Protection", "Runtime (environment only)"]


@dataclass
class Field:
    key: str
    type: str                 # str | int | bool | enum | secret | regex
    default: object
    label: str
    group: str
    help: str = ""
    min: int = None
    max: int = None
    choices: list = field(default_factory=list)
    env_only: bool = False    # read-only in the UI (needed before the app starts)
    placeholder: str = ""


SCHEMA = [
    Field("RD_API_TOKEN", "secret", "", "API token", "Real-Debrid",
          "From real-debrid.com/apitoken. Stored in /config and never shown again after saving."),
    Field("RD_API_URL", "str", "https://api.real-debrid.com/rest/1.0", "API URL", "Real-Debrid",
          "Only change this to point at a mock or proxy."),
    Field("POLL_INTERVAL", "int", 15, "Poll interval (s)", "Real-Debrid",
          "How often Real-Debrid is asked for torrent status.", min=5, max=3600),

    Field("DOWNLOAD_PATH", "str", "/data/downloads", "Download path", "Downloads",
          "Must be the same path Sonarr/Radarr see. Applies to torrents added from now on."),
    Field("MAX_PARALLEL_DOWNLOADS", "int", 2, "Parallel downloads", "Downloads",
          "Torrents downloaded at the same time.", min=1, max=16),
    Field("DOWNLOAD_RETRIES", "int", 5, "Retries per file", "Downloads",
          "Attempts with exponential backoff; partial files resume.", min=1, max=50),
    Field("CACHED_ONLY", "bool", False, "Only accept torrents cached on Real-Debrid", "Real-Debrid",
          "After adding, wait a few seconds; if Real-Debrid has not got it instantly, remove it again and "
          "report the add as failed, so Sonarr/Radarr move on to another release."),
    Field("CACHED_PROBE_SECONDS", "int", 10, "Cache check wait (s)", "Real-Debrid",
          "How long to wait for Real-Debrid to report a torrent as ready in cached-only mode.", min=3, max=60),
    Field("STALL_TIMEOUT_HOURS", "int", 24, "Give up on stalled torrents after (h)", "Real-Debrid",
          "Mark a torrent as failed when Real-Debrid shows no progress for this long (dead magnets), so the "
          "arrs can pick another release. 0 = never.", min=0, max=720),
    Field("SPEED_LIMIT_MBPS", "int", 0, "Speed limit (MB/s)", "Downloads",
          "Total across all downloads. 0 = unlimited.", min=0, max=100000),
    Field("DELETE_FROM_RD_ON_COMPLETE", "bool", False, "Remove from Real-Debrid when finished", "Downloads",
          "Deletes the torrent from your RD account once its files are on disk."),

    Field("MIN_FILE_SIZE_MB", "int", 0, "Minimum file size (MB)", "File selection",
          "Skip files smaller than this (samples, nfo). 0 = keep everything.", min=0, max=1000000),
    Field("EXCLUDE_REGEX", "regex", "", "Exclude files matching", "File selection",
          "Case-insensitive regex tested against each file path, e.g. (sample|\\.nfo$). If every file would be "
          "excluded, all files are selected instead.", placeholder="(sample|\\.nfo$)"),

    Field("WEBUI_USER", "str", "", "Username", "Access",
          "Protects this UI and the qBittorrent API. Leave empty for no authentication (anyone on the network "
          "gets in). Sonarr/Radarr then need the same credentials."),
    Field("WEBUI_PASS", "secret", "", "Password", "Access", "Required when a username is set."),

    Field("PROTECTION_ENABLED", "bool", False, "Check network protection", "Protection",
          "Periodically verifies you are behind your VPN. Informational on its own, never blocks startup."),
    Field("PROTECTION_MODE", "enum", "gluetun", "Method", "Protection",
          "gluetun: ask the gluetun control server if the VPN is running. ip: look up this container's public "
          "IP and compare it with your home IP.", choices=["gluetun", "ip"]),
    Field("GLUETUN_URL", "str", "http://gluetun:8000", "Gluetun control URL", "Protection",
          "Gluetun's control server (port 8000). Needs a route from this container."),
    Field("GLUETUN_API_KEY", "secret", "", "Gluetun API key", "Protection",
          "Only if your gluetun control server requires one."),
    Field("HOME_IPS", "str", "", "Home IP(s)", "Protection",
          "ip mode: comma-separated public IPs that mean you are unprotected.", placeholder="203.0.113.7"),
    Field("IP_CHECK_URL", "str", "https://api.ipify.org", "IP lookup URL", "Protection",
          "ip mode: returns your public IP as plain text or JSON."),
    Field("PROTECTION_INTERVAL", "int", 20, "Check interval (s)", "Protection", min=5, max=3600),
    Field("KILLSWITCH_ENABLED", "bool", False, "Killswitch", "Protection",
          "When protection is lost, pause polling and downloads (partial files resume) until it is restored. "
          "Adding torrents from Sonarr/Radarr is refused meanwhile."),
    Field("KILLSWITCH_STRICT", "bool", False, "Treat \"unknown\" as unprotected", "Protection",
          "Also trip the killswitch after 3 consecutive failed checks (e.g. gluetun unreachable). "
          "Off by default so a flaky check never stalls downloads."),

    Field("LOG_LEVEL", "enum", "INFO", "Log level", "Runtime (environment only)", "Applies immediately.",
          choices=["DEBUG", "INFO", "WARNING", "ERROR"]),
    Field("CONFIG_PATH", "str", "/config", "Config path", "Runtime (environment only)",
          "Holds settings.json and state.json.", env_only=True),
    Field("PORT", "int", 6500, "Port", "Runtime (environment only)", "Needs a container restart.", env_only=True),
    Field("PUID", "int", 1000, "PUID", "Runtime (environment only)", "Owner of the process and downloaded files.",
          env_only=True),
    Field("PGID", "int", 1000, "PGID", "Runtime (environment only)", "", env_only=True),
    Field("UMASK", "str", "002", "UMASK", "Runtime (environment only)", "", env_only=True),
]
BY_KEY = {f.key: f for f in SCHEMA}
TRUE = {"1", "true", "yes", "on"}


def coerce(f, raw):
    """Parse/validate a value for field f; raises ValueError with a user-facing message."""
    if f.type == "bool":
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in TRUE:
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        raise ValueError("must be true or false")
    if f.type == "int":
        if isinstance(raw, bool):
            raise ValueError("must be a number")
        try:
            v = int(str(raw).strip())
        except ValueError:
            raise ValueError("must be a whole number")
        if f.min is not None and v < f.min or f.max is not None and v > f.max:
            raise ValueError(f"must be between {f.min} and {f.max}")
        return v
    s = "" if raw is None else str(raw).strip()
    if f.type == "enum":
        s = s.upper() if f.key == "LOG_LEVEL" else s.lower()
        if s not in f.choices:
            raise ValueError("must be one of " + ", ".join(f.choices))
    elif f.type == "regex" and s:
        try:
            re.compile(s)
        except re.error as e:
            raise ValueError(f"invalid regex: {e}")
    elif f.key in ("RD_API_URL", "GLUETUN_URL", "IP_CHECK_URL") and s and not re.match(r"https?://", s):
        raise ValueError("must start with http:// or https://")
    elif f.key == "DOWNLOAD_PATH" and not s.startswith("/"):
        raise ValueError("must be an absolute path")
    return s


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.overrides = {}
        self.load()

    @property
    def path(self):
        return os.path.join(os.environ.get("CONFIG_PATH", "/config"), "settings.json")

    def load(self):
        try:
            with open(self.path) as fh:
                data = json.load(fh)
        except OSError:
            data = {}
        except ValueError:
            log.error("%s is not valid JSON; ignoring it", self.path)
            data = {}
        ov = {}
        for k, v in data.items():
            f = BY_KEY.get(k)
            if f and not f.env_only:
                try:
                    ov[k] = coerce(f, v)
                except ValueError as e:
                    log.error("ignoring saved %s: %s", k, e)
        with self.lock:
            self.overrides = ov

    def _env(self, f):
        raw = os.environ.get(f.key)
        if raw is None or (raw == "" and f.type != "str"):
            return None
        try:
            return coerce(f, raw)
        except ValueError as e:
            log.error("ignoring env %s=%r: %s", f.key, raw if f.type != "secret" else "***", e)
            return None

    def source(self, key):
        f = BY_KEY[key]
        if key in self.overrides and not f.env_only:
            return "ui"
        return "env" if self._env(f) is not None else "default"

    def get(self, key):
        f = BY_KEY[key]
        with self.lock:
            if key in self.overrides and not f.env_only:
                return self.overrides[key]
        v = self._env(f)
        return f.default if v is None else v

    def update(self, values, reset=()):
        """values: {key: raw}. Secrets: key omitted/None keeps, "" clears. reset: keys to drop back to env/default."""
        errors, new = {}, {}
        for k, raw in (values or {}).items():
            f = BY_KEY.get(k)
            if not f or f.env_only:
                errors[k] = "not editable"
                continue
            if f.type == "secret" and raw is None:
                continue
            try:
                new[k] = coerce(f, raw)
            except ValueError as e:
                errors[k] = str(e)
        with self.lock:
            merged = dict(self.overrides)
            merged.update(new)
            for k in reset or ():
                merged.pop(k, None)
        eff = lambda k: merged[k] if k in merged else self._effective_without(k)
        if eff("WEBUI_USER") and not eff("WEBUI_PASS"):
            errors["WEBUI_PASS"] = "required when a username is set"
        if errors:
            raise ValueError(errors)
        with self.lock:
            self.overrides = merged
            self._save()

    def _effective_without(self, key):
        v = self._env(BY_KEY[key])
        return BY_KEY[key].default if v is None else v

    def _save(self):
        d = os.path.dirname(self.path)
        os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds secrets
        with os.fdopen(fd, "w") as fh:
            json.dump(self.overrides, fh, indent=2)
        os.replace(tmp, self.path)

    def describe(self):
        out = []
        for f in SCHEMA:
            d = dict(key=f.key, type=f.type, label=f.label, group=f.group, help=f.help, min=f.min, max=f.max,
                     choices=f.choices, readonly=f.env_only, placeholder=f.placeholder,
                     source=self.source(f.key), default=None if f.type == "secret" else f.default)
            if f.type == "secret":
                d["is_set"] = bool(self.get(f.key))  # the value itself never leaves the server
            else:
                d["value"] = self.get(f.key)
            d["env_set"] = self._env(f) is not None
            out.append(d)
        return out


store = Store()
