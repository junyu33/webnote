# Portability verification

Historical checks on 2026-11-14 used native Arch Linux x86-64 and Ubuntu ARM64 Docker hosts. The images used the pinned Python 3.13 slim base, with Debian 13 inside the container. (Documentation clarified 2026-11-18.)

The historical checks apply to image tag `webnote:portable-43adf876bf69`. A fresh amd64 source deployment is recorded below; current-source ARM64 and dual-architecture bundle verification remain outstanding.

| Platform | Historical native image ID |
|---|---|
| linux/amd64 | `sha256:4bf3797b27e7d84bc89bbe994cf6ffa57b3b9c475a1a13a59a5d19aa2f14a918` |
| linux/arm64 | `sha256:0846286d8bf5d32273d8ded148ff799cf370567c7fc28188a737a517a4624162` |

## Historical checks

- Seven API regression tests passed, including binary preservation, restart persistence, public transfers, private deletion, duplicate names, and unsafe filename rejection.
- AMD64 Compose startup used private `.env` mode `0600`, UID/GID `10001:10001`, a read-only container root, a writable named volume, and loopback-only ingress.
- Binary fixtures containing CRLF, trailing spaces, and final newlines survived upload/download and container recreation byte-for-byte.
- Consistent backup and restore retained file IDs, metadata, and the test deletion token. Restore into populated storage was refused.
- AMD64-to-ARM64 and ARM64-to-AMD64 transfers succeeded using native images and separate data backups.
- An extracted release passed its SHA-256 manifest and loaded its native image without a registry pull.
- A separate disposable Arch migration exercise verified empty, text, and binary fixtures, HEAD/metadata, container recreation, backup/restore, and deletion authorization.

These checks used test fixtures. Deployment metadata and recovery locations are maintained in the separate infrastructure documentation; raw historical host logs are excluded from the public source tree. The historical results do not establish that the current tree has been rebuilt or deployed, and do not test installation on a fresh operating system.

## Reproduction

Run `python3 -m unittest -v test_server.py`. Follow `PORTABLE.md` to build each native image, package a release, and exercise startup, recreation, backup, and restore using disposable projects and synthetic files. Record the exact source, image IDs, platforms, and results for each new release.

For an optional disposable Arch/x86-64 bundle migration check, run `python3 scripts/arch-migration-check.py --result /tmp/webnote-migration-result.json`. It requires Docker/Compose and creates and removes temporary containers, networks, and volumes. It tests the supplied bundle, not unbuilt source changes.

## Fresh source deployment, 2026-11-18

Verified the README/AGENTS source-install workflow on Ubuntu 24.04.5 LTS x86-64, with Docker 29.1.3 and Compose 2.40.3. Docker was already installed; only the missing `docker-compose-v2` package was added, with no package upgrades or removals. Docker-backed commands used `sudo`; no account-group or socket-permission changes were needed.

The public GitHub source archive for commit `ebd8a958ec0c73b9fca3e81eeafcf190362e2875` matched all 16 tracked files by SHA-256. A new directory, project, token, loopback port, and empty named volume were used. No previous runtime configuration, uploaded data, or prebuilt Webnote image was imported.

- Native image: `linux/amd64`, `sha256:37214940797842578e53cc8cd7d1dfe83b6daf19e8d9c7fd7a78992ba5f1d3b5`.
- Source build: 39.51 seconds; initial healthy startup: 16.50 seconds. These are single observations, not benchmarks.
- Health and all four guide routes returned HTTP 200.
- A 1,048,604-byte binary/text fixture retained its exact bytes, SHA-256, HEAD length/ETag, metadata, generated origin, and 30-minute expiration.
- Missing and incorrect deletion tokens returned 401. The generated token deleted only the fixture; its URL then returned 404.
- Container removal/recreation retained the fixture, metadata, and private token. Private initialization used mode `0600` and refused overwriting.
- The container used UID/GID `10001:10001`, read-only root, loopback-only ingress, and its own named data volume.
- All nine isolated regression tests passed, including short-TTL expiration and quota/restart tests; shell syntax checks passed.
- Temporary containers, network, volume, image tag, and runtime token were removed. Other running containers retained their IDs, start times, and restart counts.

This was a localhost deployment on an existing Linux host, not a fresh OS installation or a new public HTTPS ingress. It did not wait for the deployed service's real 30-minute TTL or fill its 1 GiB quota; those behaviors were exercised by the isolated regression tests. No DNS, certificate, firewall, or production routing was changed. This result does not validate existing historical `dist/` bundles or current-source ARM64 images.
