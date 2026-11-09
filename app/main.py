import logging
import os
import threading
import base64

from flask import Flask, request, jsonify, Response, make_response

import auth
import config
import logbuffer
import ui
from bencode import info_hash
from manager import Manager, clean, magnet_hash, magnet_name
from protection import Protection
from version import __version__

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(name)s %(message)s")
logbuffer.install()
logging.getLogger("main").info("debrid-fetch %s starting", __version__)
app = Flask(__name__)
mgr = Manager()
prot = Protection()
mgr.gate = prot.tripped
_started = False
_start_lock = threading.Lock()


def start_worker():
    global _started
    with _start_lock:
        if not _started:
            _started = True
            threading.Thread(target=mgr.run, daemon=True).start()
            threading.Thread(target=prot.run, daemon=True).start()


start_worker()

STATE_MAP = {
    "queued_add": "queuedDL", "rd_pending": "metaDL", "rd_wait_select": "metaDL",
    "rd_downloading": "downloading", "rd_ready": "queuedDL",
    "downloading": "downloading", "completed": "pausedUP", "error": "error",
}


def view(t):
    size = t["size"] or 0
    if t["state"] == "error" and not size:
        size = 1   # harmless cosmetic: show an errored item as a non-empty download (does not trigger the arrs' failure handling)
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


ui.init(mgr, prot, view)
app.register_blueprint(ui.bp)


@app.route("/api/v2/auth/login", methods=["GET", "POST"])
def login():
    addr = request.remote_addr or "?"
    if auth.locked_out(addr):
        return Response("Fails.", 200)
    u, p = request.values.get("username", ""), request.values.get("password", "")
    if auth.required() and not auth.check_credentials(u, p):
        auth.record_failure(addr)
        return Response("Fails.", 200)
    r = make_response("Ok.")
    r.set_cookie(auth.COOKIE, auth.token())
    return r


@app.route("/api/v2/auth/logout", methods=["GET", "POST"])
def logout():
    return ""


@app.before_request
def guard():
    if request.path.startswith("/api/v2/") and request.path != "/api/v2/auth/login" \
            and not auth.valid_cookie(request.cookies.get(auth.COOKIE)):
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


FILTERS = {   # qBittorrent `filter` values -> predicate on the reported state
    "downloading": lambda st: st in ("metaDL", "downloading", "queuedDL"),
    "active": lambda st: st in ("metaDL", "downloading"),
    "inactive": lambda st: st not in ("metaDL", "downloading"),
    "completed": lambda st: st == "pausedUP",
    "paused": lambda st: st == "pausedUP", "stopped": lambda st: st == "pausedUP",
    "resumed": lambda st: st != "pausedUP", "running": lambda st: st != "pausedUP",
    "errored": lambda st: st == "error",
    "seeding": lambda st: False, "stalled": lambda st: False, "stalled_uploading": lambda st: False,
    "stalled_downloading": lambda st: False,
}


@app.route("/api/v2/torrents/info")
def info():
    cat = request.args.get("category")
    hashes = request.args.get("hashes")
    hs = set(hashes.lower().split("|")) if hashes else None
    keep = FILTERS.get(request.args.get("filter", "all"), lambda st: True)
    out = []
    for t in list(mgr.torrents.values()):
        if cat is not None and t["category"] != cat:
            continue
        if hs and t["hash"] not in hs:
            continue
        v = view(t)
        if keep(v["state"]):
            out.append(v)
    off, lim = request.args.get("offset", 0, type=int), request.args.get("limit", 0, type=int)
    out = out[off:]
    return jsonify(out[:lim] if lim > 0 else out)


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
                # Prefer the magnet's display name: the arrs read torrents/info names, and a bare hash there
                # makes the queue unreadable until Real-Debrid reports a filename.
                mgr.add(h, lambda m=m: mgr.rd.add_magnet(m), cat, magnet_name(m) or h, source=("magnet", m))
                added = True
        for f in request.files.getlist("torrents"):
            raw = f.read()
            h = info_hash(raw)
            mgr.add(h, lambda raw=raw: mgr.rd.add_torrent(raw), cat, os.path.splitext(f.filename or h)[0],
                    source=("torrent", base64.b64encode(raw).decode()))
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


@app.route("/api/v2/torrents/tags")
def tags():
    return jsonify([])


@app.route("/api/v2/transfer/info")
def transfer_info():
    return jsonify(connection_status="connected", dht_nodes=0, dl_info_data=mgr.bytes_total, dl_info_speed=0,
                   dl_rate_limit=config.SPEED_LIMIT_MBPS * 1024 * 1024, up_info_data=0, up_info_speed=0, up_rate_limit=0)


@app.route("/api/v2/transfer/speedLimitsMode")
def speed_mode():
    return "0"


@app.route("/api/v2/sync/maindata")
def maindata():
    return jsonify(rid=int(request.args.get("rid", 0)) + 1, full_update=True,
                   torrents={h: view(t) for h, t in list(mgr.torrents.items())},
                   categories={c: dict(name=c, savePath=os.path.join(config.DOWNLOAD_PATH, clean(c)))
                               for c in mgr.categories},
                   tags=[], server_state=dict(connection_status="connected", dl_info_data=mgr.bytes_total,
                                              dl_info_speed=0, up_info_data=0, up_info_speed=0))


# Accepted no-ops so *arr clients don't error (v4 and v5 names). Unknown endpoints would 404 and mark the client unhealthy.
for _p in ("pause", "resume", "stop", "start", "topPrio", "bottomPrio", "increasePrio", "decreasePrio",
           "setForceStart", "setForcedStart", "addTags", "removeTags", "createTags", "deleteTags", "setShareLimits",
           "setDownloadLimit", "setUploadLimit", "setAutoManagement", "setSuperSeeding", "filePrio", "recheck",
           "reannounce", "removeCategories", "editCategory", "setLocation", "rename", "renameFile"):
    app.add_url_rule(f"/api/v2/torrents/{_p}", f"noop_{_p}", lambda: "", methods=["GET", "POST"])
