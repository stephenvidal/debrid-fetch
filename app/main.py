import logging
import os
import re
import threading
import urllib.parse
from base64 import b32decode

from flask import Flask, request, jsonify, Response, make_response

import config
from bencode import info_hash
from manager import Manager, clean

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
app = Flask(__name__)
mgr = Manager()
_started = False
_start_lock = threading.Lock()


def start_worker():
    global _started
    with _start_lock:
        if not _started:
            _started = True
            threading.Thread(target=mgr.run, daemon=True).start()


start_worker()

STATE_MAP = {
    "rd_pending": "metaDL", "rd_wait_select": "metaDL",
    "rd_downloading": "downloading", "rd_ready": "queuedDL",
    "downloading": "downloading", "completed": "pausedUP", "error": "error",
}


def magnet_hash(m):
    q = urllib.parse.urlparse(m).query
    for xt in urllib.parse.parse_qs(q).get("xt", []):
        mm = re.match(r"urn:btih:(.+)", xt, re.I)
        if mm:
            h = mm.group(1)
            if len(h) == 32:
                h = b32decode(h.upper()).hex()
            return h.lower()
    raise ValueError("bad magnet")


def view(t):
    size = t["size"] or 0
    if t["state"] == "completed":
        prog = 1.0
    elif t["state"] in ("downloading", "rd_ready"):
        prog = 0.5 + 0.5 * (t["dl_bytes"] / size if size else 0)
        prog = min(prog, 0.999)
    else:
        prog = t["rd_progress"] / 200.0
    root = os.path.join(t["save_path"], clean(t["name"]))
    return dict(
        hash=t["hash"], name=t["name"], size=size, total_size=size, progress=prog,
        state=STATE_MAP.get(t["state"], "unknown"), category=t["category"], tags="",
        save_path=t["save_path"], content_path=root, added_on=t["added"],
        completion_on=t["completed"] or -1, dlspeed=0, upspeed=0, eta=8640000,
        ratio=0, amount_left=0 if t["state"] == "completed" else size,
        num_seeds=0, num_leechs=0, priority=0, downloaded=t["dl_bytes"], uploaded=0,
    )


def authed():
    return True


@app.route("/api/v2/auth/login", methods=["GET", "POST"])
def login():
    u, p = request.values.get("username", ""), request.values.get("password", "")
    if config.WEBUI_USER and (u != config.WEBUI_USER or p != config.WEBUI_PASS):
        return Response("Fails.", 200)
    r = make_response("Ok.")
    r.set_cookie("SID", "debrid-fetch")
    return r


@app.route("/api/v2/auth/logout", methods=["GET", "POST"])
def logout():
    return ""


@app.before_request
def guard():
    if config.WEBUI_USER and request.path.startswith("/api/v2/") \
            and request.path != "/api/v2/auth/login" and request.cookies.get("SID") != "debrid-fetch":
        return Response("Forbidden", 403)


@app.route("/health")
def health():
    return "ok"


@app.route("/api/v2/app/version")
def version():
    return "v4.6.0"


@app.route("/api/v2/app/webapiVersion")
def webapi():
    return "2.9.3"


@app.route("/api/v2/app/preferences")
def prefs():
    return jsonify(save_path=config.DOWNLOAD_PATH, temp_path_enabled=False,
                   max_ratio_enabled=False, max_seeding_time_enabled=False,
                   dht=False, pex=False, lsd=False)


@app.route("/api/v2/app/setPreferences", methods=["POST"])
def setprefs():
    return ""


@app.route("/api/v2/app/defaultSavePath")
def defsave():
    return config.DOWNLOAD_PATH


@app.route("/api/v2/torrents/info")
def info():
    cat = request.args.get("category")
    hashes = request.args.get("hashes")
    hs = set(hashes.lower().split("|")) if hashes else None
    out = []
    for t in list(mgr.torrents.values()):
        if cat is not None and t["category"] != cat:
            continue
        if hs and t["hash"] not in hs:
            continue
        out.append(view(t))
    return jsonify(out)


@app.route("/api/v2/torrents/properties")
def props():
    t = mgr.torrents.get(request.args.get("hash", "").lower())
    if not t:
        return Response("Not Found", 404)
    v = view(t)
    return jsonify(save_path=v["save_path"], total_size=v["size"], addition_date=v["added_on"],
                   completion_date=v["completion_on"], pieces_have=0, pieces_num=0, share_ratio=0)


@app.route("/api/v2/torrents/files")
def files():
    t = mgr.torrents.get(request.args.get("hash", "").lower())
    if not t:
        return Response("Not Found", 404)
    done = t["state"] == "completed"
    return jsonify([dict(name=f"{clean(t['name'])}/{f['name']}", size=f["size"],
                         progress=1.0 if f["done"] else 0, priority=1, index=i)
                    for i, f in enumerate(t["files"])] if done or t["files"] else [])


@app.route("/api/v2/torrents/trackers")
def trackers():
    return jsonify([])


@app.route("/api/v2/torrents/add", methods=["POST"])
def add():
    cat = request.form.get("category", "")
    added = False
    try:
        for m in (request.form.get("urls") or "").splitlines():
            m = m.strip()
            if m.startswith("magnet:"):
                h = magnet_hash(m)
                mgr.add(h, lambda m=m: mgr.rd.add_magnet(m), cat, h)
                added = True
        for f in request.files.getlist("torrents"):
            raw = f.read()
            h = info_hash(raw)
            mgr.add(h, lambda raw=raw: mgr.rd.add_torrent(raw), cat, os.path.splitext(f.filename or h)[0])
            added = True
    except Exception as e:
        logging.exception("add failed")
        return Response(f"Fails. {e}", 200)
    return Response("Ok." if added else "Fails.", 200)


@app.route("/api/v2/torrents/delete", methods=["POST"])
def delete():
    for h in request.form.get("hashes", "").lower().split("|"):
        if h == "all":
            for k in list(mgr.torrents):
                mgr.delete(k, request.form.get("deleteFiles") == "true")
        elif h:
            mgr.delete(h, request.form.get("deleteFiles") == "true")
    return ""


@app.route("/api/v2/torrents/categories")
def cats():
    return jsonify({c: dict(name=c, savePath=os.path.join(config.DOWNLOAD_PATH, clean(c)))
                    for c in mgr.categories})


@app.route("/api/v2/torrents/createCategory", methods=["POST"])
def mkcat():
    c = request.form.get("category", "")
    if c:
        mgr.categories[c] = True
        mgr._save()
    return ""


@app.route("/api/v2/torrents/setCategory", methods=["POST"])
def setcat():
    for h in request.form.get("hashes", "").lower().split("|"):
        if h in mgr.torrents:
            mgr.torrents[h]["category"] = request.form.get("category", "")
    return ""


# Accepted no-ops so *arr clients don't error.
for _p in ("pause", "resume", "topPrio", "setForceStart", "addTags", "removeTags", "setShareLimits",
           "recheck", "reannounce", "removeCategories", "editCategory"):
    app.add_url_rule(f"/api/v2/torrents/{_p}", f"noop_{_p}", lambda: "", methods=["GET", "POST"])
