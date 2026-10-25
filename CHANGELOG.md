# Changelog

Versions follow `app/version.py`. Pushing a `vX.Y.Z` tag publishes `ghcr.io/stephenvidal/debrid-fetch:X.Y.Z` (and `X.Y`); `latest` follows `main`.

## 0.9.0

First release candidate for 1.0.

- Downloads are checked against the size Real-Debrid reported; a bad partial file is restarted instead of renamed.
- Real-Debrid errors are handled by kind: 429 honours `Retry-After`, 5xx and network errors retry, a rejected token stops with a banner, and torrents Real-Debrid no longer has are marked failed.
- Expired download links are replaced with a fresh one. Disk-full pauses downloads instead of failing them. `STALL_TIMEOUT_HOURS` fails torrents Real-Debrid never progresses.
- On start, vanished torrents are reconciled and stale orphaned `.part` files are removed. **Clean up Real-Debrid** on the Torrents page removes untracked torrents from your RD account.
- Optional `CACHED_ONLY` mode removes torrents Real-Debrid doesn't already have and reports them as `error`. **Experimental:** on Sonarr 4.0.20 an errored item was seen staying in the queue without being blacklisted, so don't rely on the automatic re-search yet. Errored items now report a non-zero size in case Sonarr was reading zero size as "not started".
- A valid `torrents/add` always answers `Ok.`: unreachable Real-Debrid or an active killswitch queues the add, a permanent rejection is listed as `error`. A failed add would strand the episode in Sonarr/Radarr. (A permanently rejected add is listed as `error`; see the Sonarr caveat above.)
- More of the qBittorrent API (filters, `transfer/info`, `sync/maindata`, v4/v5 no-ops) with contract tests.
- CI builds the Docker image on every pull request and checks that it starts.

## 0.1.0 (MVP)

qBittorrent-compatible API backed by Real-Debrid, web UI with UI-editable settings, optional VPN protection with killswitch, GHCR image.
