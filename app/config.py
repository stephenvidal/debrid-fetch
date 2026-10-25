import os

RD_TOKEN = os.environ.get("RD_API_TOKEN", "")
DOWNLOAD_PATH = os.environ.get("DOWNLOAD_PATH", "/data/downloads")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))
DOWNLOAD_RETRIES = int(os.environ.get("DOWNLOAD_RETRIES", "5"))
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL_DOWNLOADS", "2"))
PUID = int(os.environ.get("PUID", "1000"))
PGID = int(os.environ.get("PGID", "1000"))
# Optional credentials; if empty any login succeeds (like qBittorrent with auth bypass)
WEBUI_USER = os.environ.get("WEBUI_USER", "")
WEBUI_PASS = os.environ.get("WEBUI_PASS", "")
PORT = int(os.environ.get("PORT", "6500"))
