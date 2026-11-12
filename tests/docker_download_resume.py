"""Exercise download recovery through the built image on a Linux Docker host."""

import json
import os
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
from uuid import uuid4


TORRENT_HASH = "a" * 40
RELEASE = "Download.Resume.Test"
FILENAME = "sample.mkv"
MAGNET = f"magnet:?xt=urn:btih:{TORRENT_HASH}&dn={RELEASE}"
PREFIX = b"a" * (512 * 1024)
SUFFIX = b"b" * (512 * 1024)
SIZE = len(PREFIX) + len(SUFFIX)
APP_URL = "http://127.0.0.1:6500"
IMAGE = "debrid-fetch:ci"


class FakeRealDebrid(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), RealDebridHandler)
        self.added = False
        self.selected = False
        self.release_first_request = threading.Event()
        self.resumed = threading.Event()


class RealDebridHandler(BaseHTTPRequestHandler):
    def send_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        if self.path == "/torrents/addMagnet" and form == {"magnet": [MAGNET]} and not self.server.added:
            self.server.added = True
            self.send_json({"id": "test-torrent"})
        elif self.path == "/torrents/selectFiles/test-torrent" and self.server.added and form == {"files": ["1"]}:
            self.server.selected = True
            self.send_json({})
        elif self.path == "/unrestrict/link" and self.server.selected and form == {"link": ["test-link"]}:
            self.send_json({"filename": FILENAME, "filesize": SIZE,
                            "download": f"http://127.0.0.1:{self.server.server_port}/file"})
        else:
            self.send_error(400, "Unexpected Real-Debrid request")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/user":
            self.send_json({"username": "ci"})
        elif path == "/torrents":
            self.send_json([{"id": "test-torrent"}] if self.server.added else [])
        elif path == "/torrents/info/test-torrent" and self.server.added:
            self.send_json({
                "status": "downloaded" if self.server.selected else "waiting_files_selection",
                "filename": RELEASE, "bytes": SIZE, "progress": 100,
                "files": [{"id": 1, "path": f"/{FILENAME}", "bytes": SIZE}],
                "links": ["test-link"],
            })
        elif path == "/file":
            self.serve_file()
        else:
            self.send_error(404)

    def serve_file(self):
        byte_range = self.headers.get("Range")
        if byte_range is None:
            self.send_response(200)
            self.send_header("Content-Length", str(SIZE))
            self.end_headers()
            self.wfile.write(PREFIX)
            self.wfile.flush()
            # Hold the rest until the test has killed the client with a partial file on disk.
            self.server.release_first_request.wait(timeout=60)
        elif byte_range == f"bytes={len(PREFIX)}-":
            self.send_response(206)
            self.send_header("Content-Length", str(len(SUFFIX)))
            self.send_header("Content-Range", f"bytes {len(PREFIX)}-{SIZE - 1}/{SIZE}")
            self.end_headers()
            self.server.resumed.set()
            self.wfile.write(SUFFIX)
        else:
            self.send_error(400, f"Unexpected range: {byte_range}")


def docker(*args, check=True):
    subprocess.run(["docker", *args], check=check, timeout=30)


def app_request(path, form=None):
    data = urlencode(form).encode() if form is not None else None
    with urlopen(APP_URL + path, data, timeout=3) as response:
        return response.read()


def wait_until(description, condition, timeout=30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = condition()
            if last:
                return last
        except URLError as error:
            last = error
        time.sleep(0.2)
    raise AssertionError(f"Timed out waiting for {description} (last result: {last!r})")


def completed_torrent():
    rows = json.loads(app_request(f"/api/v2/torrents/info?category=tv&hashes={TORRENT_HASH}"))
    if rows and rows[0]["state"] == "error":
        raise AssertionError(f"The app reported a failed torrent: {rows[0]}")
    if rows and rows[0]["state"] == "pausedUP":
        return rows[0]


def check_download_resume(workdir, upstream):
    config = workdir / "config"
    downloads = workdir / "downloads"
    config.mkdir()
    downloads.mkdir()
    result = downloads / "tv" / RELEASE / FILENAME
    partial = result.with_name(result.name + ".part")
    container = f"debrid-fetch-resume-{uuid4().hex[:8]}"
    try:
        docker("run", "--detach", "--name", container, "--network", "host",
               "--mount", f"type=bind,source={config},target=/config",
               "--mount", f"type=bind,source={downloads},target=/data/downloads",
               "--env", f"PUID={os.getuid()}", "--env", f"PGID={os.getgid()}",
               "--env", "RD_API_TOKEN=ci", "--env", "POLL_INTERVAL=5",
               "--env", f"RD_API_URL=http://127.0.0.1:{upstream.server_port}", IMAGE)
        wait_until("container health", lambda: app_request("/health") == b"ok")
        assert app_request("/api/v2/torrents/add", {"urls": MAGNET, "category": "tv"}) == b"Ok."
        wait_until("the first bytes to reach the download bind mount",
                   lambda: partial.exists() and partial.stat().st_size == len(PREFIX))
        assert partial.read_bytes() == PREFIX
        assert not result.exists()

        docker("kill", container)
        upstream.release_first_request.set()
        docker("start", container)
        wait_until("container health after restart", lambda: app_request("/health") == b"ok")
        row = wait_until("the qBittorrent API to report completion", completed_torrent)

        assert upstream.resumed.is_set(), "The download did not request the saved byte offset"
        assert result.read_bytes() == PREFIX + SUFFIX
        assert not partial.exists()
        assert row["hash"] == TORRENT_HASH and row["category"] == "tv"
        assert row["content_path"] == f"/data/downloads/tv/{RELEASE}"
        assert row["progress"] == 1 and row["amount_left"] == 0 and row["size"] == SIZE
        print("The download resumed after container restart and the API reported the expected completed path.")
    except Exception:
        docker("logs", container, check=False)
        raise
    finally:
        docker("rm", "--force", container, check=False)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="debrid-fetch-ci-") as tmp, FakeRealDebrid() as upstream:
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        try:
            check_download_resume(Path(tmp), upstream)
        finally:
            upstream.release_first_request.set()
            upstream.shutdown()
            thread.join()
