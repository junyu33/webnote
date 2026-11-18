# Portable Webnote deployment

Last updated: 2026-11-18.

Requires Linux Docker Engine and the Docker Compose plugin. Arch Linux is not required. The application uses only the Python standard library. Native images are supplied for `linux/amd64` and `linux/arm64`; an ARM64 image cannot run natively on x86-64.

## Start from the release bundle

```sh
tar -xzf webnote-portable.tar.gz
cd webnote-portable
sha256sum -c SHA256SUMS
./webnote.sh init https://webnote.example.com
./webnote.sh load
./webnote.sh up
./webnote.sh status
curl --fail http://127.0.0.1:8765/healthz
```

`load` chooses the native image archive from the host CPU architecture. An image loaded from the bundle needs no registry access. To build from source instead, use `./webnote.sh build` before `up`.

Configure your existing HTTPS reverse proxy to forward to `127.0.0.1:8765`; point the domain to the new host and issue its certificate. `WEBNOTE_PUBLIC_URL` controls generated download URLs; it does not create DNS records or provide TLS. Uploads and downloads are public; deletion requires the token stored in `.env`. API documentation is served at `/docs` and `/WEBNOTE_USAGE.txt`.

## Configuration and persistent storage

- `.env`: private runtime configuration and deletion token; generated with mode `0600`. Set `WEBNOTE_BIND_PORT` to change the loopback host port, `WEBNOTE_PROJECT` for a separate instance, and `WEBNOTE_PUBLIC_URL` for its public origin. Re-run `up` after changes so Compose recreates the container.
- Compose: `compose.portable.yaml`; no `/etc/webnote` or `/var/lib/webnote` dependency. `webnote.sh` always selects this file. The portable Compose file is the supported deployment layout.
- Retention: files expire 30 minutes after upload, without extension on download. Cleanup runs every 60 seconds; expired downloads and metadata return 404. The 1 GiB total storage budget includes uploads in progress and per-file overhead; uploads over budget return 507.
- Data: Docker named volume `<project>_data` (default `webnote_data`) mounted at `/data`. Image initialization gives UID/GID `10001:10001` ownership; no host service account is required.
- `down` removes containers and the network, retaining data. Do not use `docker compose down -v` unless permanent data deletion is intended.
- Logs: `./webnote.sh logs`; status: `./webnote.sh status`.
- For a parallel instance, use a different `WEBNOTE_PROJECT` and bind port. The launcher refuses to operate on an existing same-name project belonging to another Compose file.

## Backup and move to another host

```sh
./webnote.sh backup /secure/path/webnote-data.tar.gz
```

Backup briefly stops an existing running container for a consistent snapshot, then restarts it. The archive contains file bytes and metadata; it does not contain `.env` or the token. Save `.env` separately in a private backup. The archive is private data, not part of the public release bundle.

On the destination, extract the same release, securely copy `.env` (keep mode `0600`), adjust the public URL if necessary, then run:

```sh
./webnote.sh load
./webnote.sh restore /secure/path/webnote-data.tar.gz
./webnote.sh up
```

Restore requires an empty data volume and leaves the service stopped until `up`. Use trusted backups only; inspect errors before starting after a failed restore. Existing file IDs and metadata, including expiration deadlines, remain unchanged. Backup/restore does not reset the 30-minute lifetime; files can expire during migration. For pre-TTL metadata, expiry is derived from its preserved modification time. Changing the domain does not rewrite URLs embedded in older metadata. Copy the private configuration to retain the deletion token.

When importing an existing host-directory deployment, stop its writer during the final snapshot. Create a compatible archive from its storage root with `tar -C /path/to/existing/storage -czf /secure/path/webnote-data.tar.gz .`, then restore it as above. Preserve the existing token through a separate private configuration transfer unless token rotation is intended.

Verify the health endpoint and download known file IDs byte-for-byte before changing DNS. Both hosts must not accept writes independently during cutover. HTTPS proxy configuration and certificates belong to the host ingress and must be configured separately; Docker restart policy starts the application after Docker starts, so a custom systemd unit is optional.

## Release contents

The source, Dockerfile, Compose configuration, launcher, API guide, image archives, MIT license, and SHA-256 manifest form the release bundle. Runtime `.env`, tokens, and uploaded files are excluded. Keep previous release bundles to roll back the application image; keep separate data backups.

## Build a release

Run `./build-release.sh image` natively on an amd64 host and on an arm64 host using identical source files. Collect both `dist/images/webnote-linux-*.tar.gz` archives and their `*.tag` files in one builder's `dist/images/`, then run `./build-release.sh bundle`. The bundle step refuses mismatched source tags and includes only an explicit public-file allowlist. No runtime `.env`, uploaded data, or host certificates are bundled.

The API guide uses `https://webnote.example.com` in its examples. Replace that origin when following the commands; setting `WEBNOTE_PUBLIC_URL` does not rewrite the static guide. Source or guide changes require rebuilding both architecture images before assembling a matching bundle.
