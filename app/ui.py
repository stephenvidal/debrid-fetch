"""JSON API behind the web UI (/api/ui/*) and the static single-page app."""
import logging
import os
import time

import requests
from flask import Blueprint, Response, jsonify, make_response, request, send_from_directory

import auth
import config
import logbuffer
from manager import clean
from rd import RDError
from version import __version__
from settings import GROUPS, store

bp = Blueprint("ui", __name__)
log = logging.getLogger("ui")
_ctx = {}
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def init(mgr, prot, view):
    _ctx.update(mgr=mgr, prot=prot, view=view)


def apply_runtime():
    """Push settings that need an explicit hook (everything else is read live from config)."""
    logging.getLogger().setLevel(config.LOG_LEVEL)
    _ctx["prot"].recheck()


def set_session(resp):
    resp.set_cookie(auth.COOKIE, auth.token(), httponly=True, samesite="Strict",
                    secure=request.is_secure or request.headers.get("X-Forwarded-Proto") == "https",
                    max_age=60 * 60 * 24 * 30)
    return resp


@bp.route("/")
def index():
    # Version the asset URLs so each release is a new URL: a CDN or browser that overrides Cache-Control for /static
    # (Cloudflare rewrites it to 4 hours) can't serve a stale app.css/app.js after an upgrade.
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read().replace("/static/app.css", f"/static/app.css?v={__version__}") \
                       .replace("/static/app.js", f"/static/app.js?v={__version__}")
    r = Response(html, mimetype="text/html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@bp.route("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC, name, max_age=0)


@bp.before_request
def guard():
    if request.path in ("/", "/api/ui/session", "/api/ui/login") or request.path.startswith("/static/"):
        return  # the page and its assets carry no data; the login form lives in them
    if not auth.valid_cookie(request.cookies.get(auth.COOKIE)):
        return Response("Forbidden", 403)
    if request.method != "GET" and request.headers.get("X-Requested-With") != "debrid-fetch":
        return Response("Missing X-Requested-With", 400)  # blocks cross-site form posts


@bp.route("/api/ui/session")
def session():
    return jsonify(auth_required=auth.required(), authed=auth.valid_cookie(request.cookies.get(auth.COOKIE)))


@bp.route("/api/ui/login", methods=["POST"])
def login():
    addr = request.remote_addr or "?"
    if auth.locked_out(addr):
        return jsonify(error="Too many failed attempts. Try again in a few minutes."), 429
    d = request.get_json(silent=True) or {}
    if auth.required() and not auth.check_credentials(str(d.get("username", "")), str(d.get("password", ""))):
        auth.record_failure(addr)
        log.warning("failed UI login from %s", addr)
        return jsonify(error="Wrong username or password."), 401
    auth.clear_failures(addr)
    return set_session(make_response(jsonify(ok=True)))


@bp.route("/api/ui/logout", methods=["POST"])
def logout():
    r = make_response(jsonify(ok=True))
    r.delete_cookie(auth.COOKIE)
    return r


@bp.route("/api/ui/settings")
def get_settings():
    return jsonify(groups=GROUPS, fields=store.describe(), config_file=store.path)


@bp.route("/api/ui/settings", methods=["PUT"])
def put_settings():
    d = request.get_json(silent=True) or {}
    before = auth.token() if auth.required() else None
    try:
        store.update(d.get("values"), d.get("reset"))
    except ValueError as e:
        return jsonify(errors=e.args[0]), 422
    except OSError as e:
        return jsonify(errors={"_": f"could not write {store.path}: {e}"}), 500
    apply_runtime()
    log.info("settings saved: %s", ", ".join(sorted((d.get("values") or {}).keys() | set(d.get("reset") or []))))
    resp = make_response(jsonify(ok=True, fields=store.describe()))
    # a changed password rotates the session token; keep the person who changed it logged in
    if auth.required() and auth.token() != before:
        set_session(resp)
    elif not auth.required():
        resp.delete_cookie(auth.COOKIE)
    return resp


@bp.route("/api/ui/test-rd", methods=["POST"])
def test_rd():
    d = request.get_json(silent=True) or {}
    token = d.get("token") or None  # test an unsaved token, else the saved one
    if not (token or config.RD_API_TOKEN):
        return jsonify(ok=False, error="No token set"), 200
    try:
        u = _ctx["mgr"].rd.user(token)
    except (RDError, requests.RequestException) as e:
        return jsonify(ok=False, error=str(e).replace(token or "\0", "***")), 200
    return jsonify(ok=True, username=u.get("username"), type=u.get("type"), premium=u.get("premium", 0),
                   expiration=u.get("expiration"))


def _counts(mgr):
    c = dict(total=0, active=0, downloading=0, completed=0, error=0)
    for t in list(mgr.torrents.values()):
        c["total"] += 1
        s = t["state"]
        c["completed" if s == "completed" else "error" if s == "error"
          else "downloading" if s == "downloading" else "active"] += 1
    return c


@bp.route("/api/ui/status")
def status():
    mgr = _ctx["mgr"]
    return jsonify(version=__version__, now=time.time(), counts=_counts(mgr), bytes_total=mgr.bytes_total,
                   blocked=mgr.blocked_reason(), rd_error=mgr.rd_error, arr_error=mgr.arr_error, paused=mgr.paused, has_token=bool(config.RD_API_TOKEN),
                   auth_required=auth.required(), protection=_ctx["prot"].snapshot(),
                   download_path=config.DOWNLOAD_PATH, speed_limit_mbps=config.SPEED_LIMIT_MBPS)


@bp.route("/api/ui/torrents")
def torrents():
    mgr = _ctx["mgr"]
    out = []
    for t in list(mgr.torrents.values()):
        v = _ctx["view"](t)
        v.update(raw_state=t["state"], error=t.get("error", ""), rd_progress=t["rd_progress"],
                 files=len(t.get("files") or []), folder=os.path.join(t["save_path"], clean(t["name"])))
        out.append(v)
    out.sort(key=lambda x: x["added_on"], reverse=True)
    return jsonify(out)


@bp.route("/api/ui/torrents/<h>/retry", methods=["POST"])
def retry(h):
    return jsonify(ok=_ctx["mgr"].retry(h.lower()))


@bp.route("/api/ui/torrents/<h>/delete", methods=["POST"])
def delete(h):
    d = request.get_json(silent=True) or {}
    _ctx["mgr"].delete(h.lower(), bool(d.get("delete_files")))
    return jsonify(ok=True)


@bp.route("/api/ui/pause", methods=["POST"])
def pause():
    _ctx["mgr"].paused = bool((request.get_json(silent=True) or {}).get("paused"))
    log.info("downloads %s from the UI", "paused" if _ctx["mgr"].paused else "resumed")
    return jsonify(ok=True)


@bp.route("/api/ui/rd/untracked")
def rd_untracked():
    try:
        items = _ctx["mgr"].untracked()
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(items=[dict(id=x["id"], name=x.get("filename", ""), bytes=x.get("bytes", 0),
                               status=x.get("status", ""), added=x.get("added", "")) for x in items])


@bp.route("/api/ui/rd/purge-untracked", methods=["POST"])
def rd_purge():
    ids = [str(i) for i in (request.get_json(silent=True) or {}).get("ids", [])]
    return jsonify(removed=_ctx["mgr"].purge_untracked(ids))


@bp.route("/api/ui/protection/check", methods=["POST"])
def protection_check():
    _ctx["prot"].check()
    return jsonify(_ctx["prot"].snapshot())


@bp.route("/api/ui/logs")
def logs():
    return jsonify(logbuffer.tail(request.args.get("after", 0, type=int)))
