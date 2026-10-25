# Changelog

Versions follow `app/version.py`. Pushing a `vX.Y.Z` tag publishes `ghcr.io/stephenvidal/debrid-fetch:X.Y.Z` (and `X.Y`); `latest` follows `main`.

## Unreleased

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
