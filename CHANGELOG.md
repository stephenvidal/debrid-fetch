# Changelog

Versions follow `app/version.py`. Pushing a `vX.Y.Z` tag publishes `ghcr.io/stephenvidal/debrid-fetch:X.Y.Z` (and `X.Y`); `latest` follows `main`.

## Unreleased

- **Check a release** (new page): paste magnets or bare info hashes and debrid-fetch asks Real-Debrid whether it will serve them, then deletes the probe torrent. The verdict is the same one a grab would get, so a 451 is a lookup instead of a failed Sonarr/Radarr cycle. `POST /api/ui/rd-check`.
- **Activity history** (new page): every add, refusal, RD-side removal, stall, completion and arr blocklisting is recorded with its reason and kept across restarts in `/config/events.json` (last 1000). Answers "where did that download go?" without reading container logs. `GET/DELETE /api/ui/events`.
- **Dead tokens can no longer hide**: the token is validated every five minutes even when nothing is downloading, so `rd_error` is populated while the queue is empty (the old poll loop only noticed a bad token when a torrent failed).
- **Configuration drift is flagged**: when a value saved in the UI differs from the same variable in the environment, the UI says so in a banner. The UI value still wins, but silently no longer.
- **Packed releases are labelled**: a finished download that contains only archives is marked "needs extracting" (Sonarr/Radarr never extract; Unpackerr does) and recorded in the activity history.
- **Readable names for magnets**: when the arr sends a magnet, its `dn=` display name is used instead of the bare info hash, so the queue reads like a release from the first poll.
- MIT license.
- The web UI links its CSS/JS with `?v=<version>`, so upgrades can't be served stale by a CDN that rewrites cache headers.
- With `ARR_ENABLED`, a torrent is removed from debrid-fetch (and Real-Debrid) once the arr has blocklisted it; the compose example shows the `ARR_*` variables.
- Optional Sonarr/Radarr integration (`ARR_ENABLED`, off by default): for releases Real-Debrid permanently refuses (or, in cached-only mode, doesn't have), mark the grab failed in the arr so it is blocklisted and trigger a normal search, capped at `ARR_MAX_ATTEMPTS` per episode/movie. The arrs ignore a qBittorrent client's `error` state, so this is the only way to get them to move on.
- Docs corrected: errored items are only a warning in the arrs; they are not blocklisted without the integration.

## 0.9.0

First release candidate for 1.0.

- Downloads are checked against the size Real-Debrid reported; a bad partial file is restarted instead of renamed.
- Real-Debrid errors are handled by kind: 429 honours `Retry-After`, 5xx and network errors retry, a rejected token stops with a banner, and torrents Real-Debrid no longer has are marked failed.
- Expired download links are replaced with a fresh one. Disk-full pauses downloads instead of failing them. `STALL_TIMEOUT_HOURS` fails torrents Real-Debrid never progresses.
- On start, vanished torrents are reconciled and stale orphaned `.part` files are removed. **Clean up Real-Debrid** on the Torrents page removes untracked torrents from your RD account.
- Optional `CACHED_ONLY` mode removes torrents Real-Debrid doesn't already have and reports them as `error`. **Experimental:** Sonarr/Radarr map qBittorrent's `error` state to a warning, so they do not blocklist the release or search again; the item stays in their queue until you mark it failed there.
- A valid `torrents/add` always answers `Ok.`: unreachable Real-Debrid or an active killswitch queues the add, a permanent rejection is listed as `error`. A failed add would strand the episode in Sonarr/Radarr. (A permanently rejected add is listed as `error`, which the arrs only show as a warning; see the caveat above.)
- More of the qBittorrent API (filters, `transfer/info`, `sync/maindata`, v4/v5 no-ops) with contract tests.
- CI builds the Docker image on every pull request and checks that it starts.

## 0.1.0 (MVP)

qBittorrent-compatible API backed by Real-Debrid, web UI with UI-editable settings, optional VPN protection with killswitch, GHCR image.
