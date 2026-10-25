# debrid-fetch

A minimal Real-Debrid download client in a Docker image. It exposes a subset of the qBittorrent Web API, so Sonarr and Radarr can use it as a "qBittorrent" download client with no changes. Added torrents and magnets go to Real-Debrid, are polled until cached/ready, then downloaded to a local path with retries and resume.

## Quick start

```sh
export RD_API_TOKEN=your_token   # https://real-debrid.com/apitoken
cp docker-compose.example.yml docker-compose.yml
docker compose up -d --build
```

In Sonarr/Radarr: Settings > Download Clients > add **qBittorrent**, host `debrid-fetch` (or the Docker host), port `8080`, any username/password unless you set `WEBUI_USER`/`WEBUI_PASS`. Set a category (e.g. `tv-sonarr`); files land in `DOWNLOAD_PATH/<category>/<release name>/`.

Mount the downloads volume at the **same path** in debrid-fetch and in Sonarr/Radarr (or add a Remote Path Mapping), because they read the finished files from `content_path`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `RD_API_TOKEN` | required | Real-Debrid API token |
| `DOWNLOAD_PATH` | `/downloads` | Where files are saved |
| `CONFIG_PATH` | `/config` | Holds `state.json` (survives restarts) |
| `PUID` / `PGID` | 1000 / 1000 | Owner of the process and downloaded files |
| `POLL_INTERVAL` | 15 | Seconds between Real-Debrid status polls |
| `DOWNLOAD_RETRIES` | 5 | Attempts per file (exponential backoff, resumes partial files) |
| `MAX_PARALLEL_DOWNLOADS` | 2 | Torrents downloaded at once |
| `WEBUI_USER` / `WEBUI_PASS` | empty | Optional login; empty accepts any login |
| `PORT` | 8080 | Listen port |

## How it works

1. `torrents/add` (magnet URLs or .torrent uploads) adds to Real-Debrid and selects all files.
2. A background loop polls until Real-Debrid reports `downloaded`.
3. Files are unrestricted and downloaded to `.part` files, renamed on success.
4. The torrent then reports state `pausedUP` at 100%, which Sonarr/Radarr treat as complete. Removing it from the client also deletes it from Real-Debrid.

## Limitations (MVP)

Downloads all files in a torrent, no upload/seeding (ratio 0), no web UI, no speed reporting, a single instance only. Torrents not cached on Real-Debrid still work but wait for RD to fetch them.
