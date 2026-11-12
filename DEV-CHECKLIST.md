# Development checklist

The [README](README.md) describes the supported Docker deployment and the [changelog](CHANGELOG.md) tracks shipped behavior. The [Test workflow](.github/workflows/test.yml) runs `python -m pytest -q tests` and verifies the built image.

To run the container test on a Linux Docker host with port 6500 free, build with `docker build -t debrid-fetch:ci .` and run `python3 tests/docker_download_resume.py`.

## 1.0 release readiness

Keep the existing qBittorrent API and persisted settings compatible throughout the release.

- [ ] Verify download and resume in Docker CI using a fake Real-Debrid service. A magnet submitted through the qBittorrent API should survive a container restart during download, resume from the saved offset, and report the completed file under the expected category path.
- [ ] Make the Compose quick start usable on a clean Docker host. Document which paths and ownership settings users should configure and how to reach the web UI.
- [ ] Add setup checks for Real-Debrid credentials, download folder access, and configured Sonarr/Radarr connections. A failed check should identify the affected setting and avoid exposing credentials.
- [ ] Verify a Sonarr import and a Radarr import on a NAS using authorized test downloads. Confirm both imports and hardlinks on a shared filesystem.
- [ ] Exercise upgrade and rollback using persistent config and queue state, then tag 1.0 after the preceding checks pass.
