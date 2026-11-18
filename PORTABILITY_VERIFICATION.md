# Portability verification

Historical checks on 2026-11-14 used native Arch Linux x86-64 and Ubuntu ARM64 Docker hosts. The images used the pinned Python 3.13 slim base, with Debian 13 inside the container. (Documentation clarified 2026-11-18.)

These checks apply to image tag `webnote:portable-43adf876bf69`, not to a newly rebuilt image from the current source. Changes made while preparing the source for publication have not yet received a new dual-architecture container verification.

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
