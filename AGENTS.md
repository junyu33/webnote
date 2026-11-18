# Webnote installation and maintenance

Read `README.md` for the user-facing contract and `PORTABLE.md` for backup, migration, and release details. Before repository work, check for relevant repository-local `skills/` directories or additional `SKILL.md` instructions. (2026-11-18)

## Service contract

- Upload, download, HEAD, and metadata queries are public. Only deletion requires the owner's token.
- Preserve raw bytes, including encoding, line endings, and trailing whitespace.
- Files expire 30 minutes after upload; reads do not extend expiry. Cleanup runs every 60 seconds. Expired downloads, HEAD, and metadata return 404.
- The default per-file limit is 100 MiB. Total storage accounting is capped at 1 GiB, including in-progress uploads and per-file overhead; rejected uploads return 507.
- Filenames are metadata, not filesystem paths. Preserve traversal and symlink protections.
- Do not change these behaviors as an installation side effect. TTL, cleanup interval, and total quota are currently defaults in `create_server`, not supported Compose environment settings.

## 1. Identify the target and inspect the host

Determine whether the user requested a local trial, new deployment, or existing-instance maintenance. Use the stated target; do not assume a personal server or domain.

```sh
uname -s
uname -m
docker version
docker compose version
docker ps --format '{{.Names}}\t{{.Ports}}'
```

The documented deployment uses Linux Docker Engine and Compose. Native release archives support amd64 and arm64. Check the intended port, available disk, and existing Compose projects and volumes. Install missing dependencies using the distribution's method only within the requested scope; do not perform a whole-system upgrade or change global networking.

Before changing an existing instance, identify its actual Compose directory, project, image, configuration, and data volume. Do not print `.env`, use `set -x`, or dump container environment variables. Preserve the deletion token unless rotation was requested. Preparing source alone does not authorize changing a running service.

## 2. Select source or a verified release

For source installation, use the intended checkout and `webnote.sh build`.

For an offline release, verify its external archive checksum before extraction when available, then verify `SHA256SUMS` in the extracted bundle. Confirm its version and feature set. `webnote.sh load` selects the native image archive; it does not rebuild it.

Existing `dist/` packages may predate current source. Historical portability checks do not validate a new build. Never force stale images into a new-source release by editing tag files or checksums.

## 3. Configure a new or existing instance

For a new local trial:

```sh
./webnote.sh init http://127.0.0.1:8765
```

For a public deployment, use the user's HTTPS origin instead. The launcher accepts an HTTP(S) origin without a path or credentials, creates `.env` with mode `0600`, and generates a random 64-character hex deletion token. It refuses to overwrite `.env`. `.env.example` is a field reference, not a usable token source.

| Field | Purpose |
|---|---|
| `WEBNOTE_PUBLIC_URL` | Origin used in newly generated download URLs |
| `WEBNOTE_BIND_PORT` | Loopback host port; default 8765 |
| `WEBNOTE_PROJECT` | Compose project name; default webnote |
| `WEBNOTE_IMAGE` | Image tag to build or run |
| `WEBNOTE_MAX_BYTES` | Per-file byte limit; default 104857600 |
| `WEBNOTE_TOKEN` | Private deletion credential; never include its value in output or Git |

Retain existing configuration and edit only required fields. For a separate trial, use a separate directory, unique project, and free loopback port. Resolve same-name project collisions instead of bypassing the launcher's Compose-file ownership guard.

## 4. Validate configuration and start

Check configuration without printing substituted secrets:

```sh
docker compose --env-file .env -f compose.portable.yaml config --quiet
```

Choose one image preparation path, then start:

```sh
./webnote.sh build  # source installation
# OR: ./webnote.sh load  # verified native offline bundle
./webnote.sh up
./webnote.sh status
```

The supported file is `compose.portable.yaml`. Data lives in `<project>_data` at container `/data`. The container runs as UID/GID `10001:10001`, with read-only root and loopback-only ingress. No host Python environment or custom service account is required. Docker restart policy handles startup after Docker starts; a custom systemd unit is optional.

## 5. Verify the actual instance

Use the configured port rather than assuming 8765. For the default trial:

```sh
curl --fail http://127.0.0.1:8765/healthz
curl --fail http://127.0.0.1:8765/docs
```

Health must return HTTP 200 with `{"status": "ok"}`. The static API guide contains an example domain; `WEBNOTE_PUBLIC_URL` changes generated URLs but does not rewrite the guide.

Upload a uniquely named synthetic fixture containing binary bytes, CRLF, trailing spaces, and a final newline. Verify the returned ID, size, SHA-256, origin, and expiration. Check public GET bytes, HEAD Content-Length and SHA-256 ETag, and metadata. Missing or invalid deletion authorization must return 401. Delete only this fixture using the retained token and exact filename plus ID, then confirm 404. Read credentials privately without printing them or putting their values into test artifacts.

When host Python is available, also run the isolated regression suite:

```sh
python3 -m unittest -v test_server.py
sh -n webnote.sh build-release.sh
```

The tests cover byte preservation and persistence, public transfer/private deletion, duplicates, traversal and symlinks, expiration before cleanup, and concurrent quota admission with restart accounting. They use temporary storage and an independent token; they do not replace testing the deployed instance. Do not fill production storage or wait for its real TTL merely to retest these isolated behaviors.

Configure DNS, TLS, a reverse proxy, or monitoring only within the requested deployment scope. Forward HTTPS ingress to the selected loopback port and accommodate the configured upload size. For public deployment, verify health, docs, generated URLs, and fixture transfer over HTTPS too; do not disable TLS verification to make a check pass.

## 6. Update, migrate, or recover

Keep the token and data volume. Record the prior image and relevant configuration before an authorized update, prepare the intended image, and run `up` to recreate the container. Recheck afterward and report interrupted transfers. Do not restore stale application data as an image rollback.

Follow `PORTABLE.md` for `webnote.sh backup ARCHIVE` and `restore ARCHIVE`. Backup briefly stops the writer; restore refuses a populated volume and stays stopped until `up`. Transfer `.env` separately and privately. Expiration deadlines remain unchanged, so files may expire during migration. Changing the origin does not rewrite URLs in existing metadata.

`down` retains data. Do not use `down -v`, prune shared resources, overwrite populated storage, or delete existing files as routine cleanup. Remove only disposable resources created for an authorized test.

## Releases and completion

Build `build-release.sh image` natively on each architecture using matching source, collect image archives and tag files, then run `build-release.sh bundle`. Packaging uses an explicit allowlist and checksum manifest. Preserve the MIT notice in source, images, and bundles. Exclude credentials, runtime configuration, uploaded data, private backups, certificates, and host logs.

Report the source/version, image and platform, project, origin and port, configuration/data locations without credentials, checks actually performed, and remaining work. Distinguish unit tests, container startup, and HTTPS verification. Do not claim a source-only change was deployed or that historical tests validate a new image.
