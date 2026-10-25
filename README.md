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

To update, name the service so the rest of a shared media stack isn't recreated: `docker compose pull rdt-client && docker compose up -d rdt-client` (use your service name; the example's is `rdt-client`). A private package needs `docker login ghcr.io` on the host first. To build locally instead, replace `image:` with `build: .` in your compose file.

In Sonarr/Radarr: Settings > Download Clients > add **qBittorrent**, host `rdt-client` (the compose example keeps that name so an existing rdt-client entry works unchanged), port `6500`, any username/password unless you set `WEBUI_USER`/`WEBUI_PASS`. Set a category (e.g. `tv`, `movies`, `tv-anime`, `movies-anime`); files land in `/data/downloads/<category>/<release name>/`.

Bind the downloads directory at the same container path Sonarr/Radarr use (`/volume1/docker/data/downloads:/data/downloads`; or mount all of `/volume1/docker/data:/data`) so `content_path` matches what they see and imports hardlink instead of copy; no Remote Path Mapping is needed. Pre-create the category directories (`tv`, `movies`, `tv-anime`, `movies-anime`) on the host, owned by `PUID:PGID`, to avoid arr path warnings. The image declares no volume for `/data/downloads`, so always bind it explicitly; otherwise files land in an anonymous Docker volume the arrs cannot see.

## Web UI

Open `http://<host>:6500/`. It has five pages:

- **Dashboard**: live speed, counts, protection status, active downloads, and a pause/resume switch.
- **Torrents**: everything tracked, with state, progress, retry for errors and remove (files stay on disk).
- **Settings**: every setting in the table below. Each field shows where its value comes from (*default*, *from env*, *set in UI*); **Reset** drops a UI value so the env/default applies again. The Real-Debrid token has a **Test** button.
- **Protection**: VPN check and killswitch (below).
- **Logs**: the last 500 log lines, live.

Settings saved in the UI go to `/config/settings.json` (mode 600) and override the same environment variable; only changed keys are written, so everything else keeps following your compose file. They apply without a restart, except the *Runtime* group (`PORT`, `PUID`, `PGID`, `UMASK`, `CONFIG_PATH`), which is env-only and shown read-only. Secrets (`RD_API_TOKEN`, `WEBUI_PASS`, `GLUETUN_API_KEY`) are never sent back to the browser after saving; the UI only shows whether one is set.

**Authentication.** Set `WEBUI_USER`/`WEBUI_PASS` (env or UI) to require a login for both the UI and the qBittorrent API; Sonarr/Radarr then need the same credentials. Without them everything is open and the UI shows a warning. Five failed logins from one address lock it out for five minutes. Locked out? Delete `WEBUI_USER`/`WEBUI_PASS` from `/config/settings.json` and restart.

## Protection and killswitch

Optional and off by default. When enabled, debrid-fetch checks every `PROTECTION_INTERVAL` seconds that traffic is protected:

- `gluetun` mode asks gluetun's control server (`GLUETUN_URL`, port 8000) whether the VPN is running, and shows gluetun's public IP. It tells you the VPN container is up; it does not prove debrid-fetch itself routes through it.
- `ip` mode looks up the public IP this container actually uses (`IP_CHECK_URL`) and reports *unprotected* if it is one of `HOME_IPS`. This is the stronger check when debrid-fetch is not behind gluetun.

On its own the check is informational. With `KILLSWITCH_ENABLED`, an *unprotected* result immediately stops Real-Debrid polling and all downloads (partial `.part` files are kept and resume) and holds new adds: `torrents/add` still answers `Ok.` and the torrent waits as *Waiting to send to RD* (state `queuedDL`) until protection is back. Everything resumes on the next *protected* result. A check that merely fails (gluetun unreachable, lookup timeout) is *unknown* and never trips the killswitch unless you also enable `KILLSWITCH_STRICT`, which trips after 3 consecutive unknowns.

## Configuration

All of these can be set as environment variables or in the web UI.

| Variable | Default | Description |
|---|---|---|
| `RD_API_TOKEN` | empty | Real-Debrid API token (env or UI; nothing downloads until one is set) |
| `RD_API_URL` | RD REST API | Only for mocks/proxies |
| `POLL_INTERVAL` | 15 | Seconds between Real-Debrid status polls |
| `DOWNLOAD_PATH` | `/data/downloads` | Where files are saved |
| `MAX_PARALLEL_DOWNLOADS` | 2 | Torrents downloaded at once (1-16) |
| `DOWNLOAD_RETRIES` | 5 | Attempts per file (exponential backoff, resumes partial files) |
| `CACHED_ONLY` | false | Only accept torrents Real-Debrid already has cached; others are removed again and show as failed |
| `CACHED_PROBE_SECONDS` | 10 | How long to wait for the cache check |
| `ARR_ENABLED` | false | Blocklist permanently rejected releases in Sonarr/Radarr (see above) |
| `ARR_MAX_ATTEMPTS` | 3 | Blocklist-and-search rounds per episode/movie |
| `ARR_1_TYPE` ... `ARR_4_CATEGORIES` | empty | Up to four instances: type, URL, API key (secret), categories |
| `STALL_TIMEOUT_HOURS` | 24 | Mark a torrent failed when Real-Debrid shows no progress for this long, 0 = never |
| `SPEED_LIMIT_MBPS` | 0 | Total download limit in MB/s, 0 = unlimited |
| `DELETE_FROM_RD_ON_COMPLETE` | false | Remove the torrent from your RD account once downloaded |
| `MIN_FILE_SIZE_MB` | 0 | Don't select files smaller than this on RD |
| `EXCLUDE_REGEX` | empty | Don't select files whose path matches (case-insensitive) |
| `WEBUI_USER` / `WEBUI_PASS` | empty | Optional login for the UI and qBittorrent API |
| `PROTECTION_ENABLED` | false | Turn on the VPN check |
| `PROTECTION_MODE` | `gluetun` | `gluetun` or `ip` |
| `GLUETUN_URL` / `GLUETUN_API_KEY` | `http://gluetun:8000` / empty | gluetun control server |
| `HOME_IPS` / `IP_CHECK_URL` | empty / `https://api.ipify.org` | ip mode |
| `PROTECTION_INTERVAL` | 20 | Seconds between checks |
| `KILLSWITCH_ENABLED` | false | Stop downloads while unprotected |
| `KILLSWITCH_STRICT` | false | Also stop after 3 failed checks in a row |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CONFIG_PATH` | `/config` | Holds `settings.json`, `state.json`, `secret.key` (env only) |
| `PUID` / `PGID` | 1000 / 1000 | Owner of the process and downloaded files (env only) |
| `UMASK` | 002 | Umask for created files (env only) |
| `PORT` | 6500 | Listen port (env only) |

## How it works

1. `torrents/add` (magnet URLs or .torrent uploads) adds to Real-Debrid and selects its files (all of them, minus any `MIN_FILE_SIZE_MB` / `EXCLUDE_REGEX` filters).
2. A background loop polls until Real-Debrid reports `downloaded`.
3. Files are unrestricted and downloaded to `.part` files, checked against the size Real-Debrid reported, then renamed. An expired download link is replaced with a fresh one; 429/5xx/network errors back off and retry, while a rejected token stops with a banner in the UI. If the disk is too full, downloads pause and resume once space is free.
4. The torrent then reports state `pausedUP` at 100%, which Sonarr/Radarr treat as complete. Removing it from the client also deletes it from Real-Debrid.

## Adds never fail

A failed `torrents/add` leaves the episode stuck as grabbed in Sonarr/Radarr (they record the release as grabbed and stop searching), and a non-2xx reply marks the whole client unhealthy. So a valid add is always answered `Ok.`: if Real-Debrid can't be reached right now (killswitch, no token, a rejected token, 429/5xx, network) it is saved with its magnet/.torrent and sent when possible, and if RD permanently refuses it (e.g. an invalid or blocked torrent) it is listed with state `error`. Sonarr/Radarr show that as a warning in their queue but do **not** blocklist the release or search again: they map qBittorrent's `error` state to a warning, so no qBittorrent-style client can trigger that. Only a malformed magnet or .torrent returns `Fails.`.

## Sonarr/Radarr blocklisting (optional, off by default)

Sonarr and Radarr map every qBittorrent state to a warning at worst, so no torrent client can make them blocklist a release; only their own API can. With `ARR_ENABLED`, debrid-fetch uses it in one narrow case: when Real-Debrid *permanently* refuses a release (blocked/infringing, dead, virus, or never progresses for `STALL_TIMEOUT_HOURS`) or, in cached-only mode, doesn't have it, debrid-fetch looks up the grab by its info hash in the arr's history, marks it failed (which blocklists that release) and triggers a normal `EpisodeSearch`/`MoviesSearch`. The search goes through your quality profiles; debrid-fetch never grabs a release itself. Network errors, rate limits, a rejected token and the killswitch never trigger it.

Configure up to four instances (Settings > Sonarr / Radarr, or env): `ARR_<n>_TYPE` (`sonarr`/`radarr`), `ARR_<n>_URL`, `ARR_<n>_API_KEY` (Settings > General in the arr; stored write-only like the RD token) and `ARR_<n>_CATEGORIES` (comma-separated download categories that instance uses, e.g. `tv, tv-anime`). A torrent whose category matches no instance is left alone. `ARR_MAX_ATTEMPTS` (default 3) caps blocklist-and-search rounds per episode or movie so a run of bad releases can't loop forever. A wrong API key or unreachable arr shows a banner and is retried for 30 minutes. Needs Sonarr/Radarr v3 or later (`/api/v3`).

## Housekeeping

On each start debrid-fetch compares its state with your Real-Debrid account: unfinished torrents RD no longer has are marked failed, and `.part` files older than 7 days that no tracked torrent owns are deleted. The **Torrents** page has a **Clean up Real-Debrid** button that lists torrents in your RD account which debrid-fetch is not tracking (leftovers, things added from other apps) and deletes them after you confirm. Nothing is ever deleted from your account automatically.

**Cached-only mode.** Real-Debrid has no public cache search, so with `CACHED_ONLY` debrid-fetch adds the torrent, waits up to `CACHED_PROBE_SECONDS` for RD to report it ready, and removes it again if it is not. The add is still accepted and the torrent is listed with state `error`, which the arrs show as a warning. They do not blocklist the release or search again on their own, so you have to mark it failed in the arr yourself; the episode is otherwise left waiting on that queue item. (Returning a failed add instead leaves the episode stuck as grabbed, and a non-2xx response marks the whole client unhealthy.) With the optional Sonarr/Radarr integration below, debrid-fetch can do that for you.

## Limitations (MVP)

No upload/seeding (ratio 0), no per-torrent speed in the qBittorrent API, a single instance only. Torrents not cached on Real-Debrid still work but wait for RD to fetch them.

## Troubleshooting

- **Sonarr/Radarr say the download client is unavailable.** Check host `rdt-client` (or your container name), port `6500`, and the credentials if you set `WEBUI_USER`/`WEBUI_PASS`. If the container name changed, update the host in the arr, not the port.
- **Import fails with "no files found" or a path warning.** The downloads folder must be mounted at the same container path in debrid-fetch and the arrs (default `/data/downloads`). No Remote Path Mapping should be needed.
- **Files are owned by the wrong user / can't be imported.** Set `PUID`/`PGID` to your NAS user and make sure the host folders (including the category folders) are owned by it.
- **Nothing downloads.** The UI banners say why: no token, a rejected token, the killswitch, a manual pause, or a full disk. The Logs page has the details.
- **Torrents sit at "Queued at RD".** The torrent isn't cached; Real-Debrid is fetching it. It fails after `STALL_TIMEOUT_HOURS` without progress. Turn on `CACHED_ONLY` if you'd rather skip those releases.
- **The UI looks stale after an upgrade.** The page links its CSS/JS with the release version, so a new version loads fresh files even behind a CDN or reverse proxy that caches `/static` (Cloudflare, for example, overrides `Cache-Control` with its own 4-hour TTL). If it still looks old, hard-refresh once, or check the proxy isn't also caching `/`.
- **The port is only reachable from the NAS itself.** The example compose publishes `127.0.0.1:6500` because the arrs reach it over the Docker network. Change the `ports:` line to publish on the LAN, and set a UI login first.

## Upgrading and rolling back

`docker compose pull rdt-client && docker compose up -d rdt-client`, naming the service so the other containers in your stack are left alone (and `docker login ghcr.io` first if the package is private). Settings and state live in `/config` and carry over. To pin a version, use `image: ghcr.io/stephenvidal/debrid-fetch:0.9.0` instead of `latest`; to roll back, set the previous tag and run `docker compose up -d rdt-client`. See [CHANGELOG.md](CHANGELOG.md). Keep the token out of version control: put it in `.env` or the web UI.

## Development

```sh
pip install -r app/requirements.txt pytest
python -m pytest -q tests
```

## License

[MIT](LICENSE).
