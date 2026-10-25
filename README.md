# debrid-fetch

A minimal Real-Debrid download client in a Docker image. It exposes a subset of the qBittorrent Web API, so Sonarr and Radarr can use it as a "qBittorrent" download client with no changes. Added torrents and magnets go to Real-Debrid, are polled until cached/ready, then downloaded to a local path with retries and resume.

## Quick start

```sh
export RD_API_TOKEN=your_token  # or put it in .env   # https://real-debrid.com/apitoken
cp docker-compose.example.yml docker-compose.yml
docker compose pull && docker compose up -d
```

## Image

A GitHub Actions workflow builds the image (linux/amd64, e.g. Synology DS220+) on every push to `main` and on `v*` tags, and publishes it to `ghcr.io/stephenvidal/debrid-fetch` (tags: `latest`, `sha-<commit>`, and `x.y.z` / `x.y` for releases). The NAS only pulls; nothing is built locally.

The first publish creates the package as private. Either make it public (GitHub > your profile > Packages > debrid-fetch > Package settings > Change visibility), or log in on the NAS with a personal access token that has `read:packages`:

```sh
echo $GHCR_TOKEN | docker login ghcr.io -u stephenvidal --password-stdin
```

To update: `docker compose pull && docker compose up -d`. To build locally instead, replace `image:` with `build: .` in your compose file.

In Sonarr/Radarr: Settings > Download Clients > add **qBittorrent**, host `rdt-client` (the compose example keeps that name so an existing rdt-client entry works unchanged), port `6500`, any username/password unless you set `WEBUI_USER`/`WEBUI_PASS`. Set a category (e.g. `tv`, `movies`, `tv-anime`, `movies-anime`); files land in `/data/downloads/<category>/<release name>/`.

Mount the same `/data` volume as Sonarr/Radarr (`/volume1/docker/data:/data`) so `content_path` matches what they see and imports hardlink instead of copy; no Remote Path Mapping is needed.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `RD_API_TOKEN` | required | Real-Debrid API token |
| `DOWNLOAD_PATH` | `/data/downloads` | Where files are saved |
| `CONFIG_PATH` | `/config` | Holds `state.json` (survives restarts) |
| `PUID` / `PGID` | 1000 / 1000 | Owner of the process and downloaded files |
| `POLL_INTERVAL` | 15 | Seconds between Real-Debrid status polls |
| `DOWNLOAD_RETRIES` | 5 | Attempts per file (exponential backoff, resumes partial files) |
| `MAX_PARALLEL_DOWNLOADS` | 2 | Torrents downloaded at once |
| `WEBUI_USER` / `WEBUI_PASS` | empty | Optional login; empty accepts any login |
| `UMASK` | 002 | Umask for created files |
| `PORT` | 6500 | Listen port |

## How it works

1. `torrents/add` (magnet URLs or .torrent uploads) adds to Real-Debrid and selects all files.
2. A background loop polls until Real-Debrid reports `downloaded`.
3. Files are unrestricted and downloaded to `.part` files, renamed on success.
4. The torrent then reports state `pausedUP` at 100%, which Sonarr/Radarr treat as complete. Removing it from the client also deletes it from Real-Debrid.

## Limitations (MVP)

Downloads all files in a torrent, no upload/seeding (ratio 0), no web UI, no speed reporting, a single instance only. Torrents not cached on Real-Debrid still work but wait for RD to fetch them.
