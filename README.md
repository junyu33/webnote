# Webnote

Webnote is a small file transfer API for passing files between scripts, machines, and AI sessions. Upload a file, keep the returned URL, and download the same bytes elsewhere. On a phone, you can pass that URL to another session without finding a local download folder and uploading the file again. (2026-11-18)

It preserves encoding, line endings, trailing spaces, and final newlines. The application uses the Python standard library and runs on Linux with Docker and Compose.

## What to expect

- Uploads and downloads are public. Anyone holding a download URL can read that file.
- Files expire **30 minutes after upload**; downloading does not extend their lifetime.
- The default maximum file size is **100 MiB**. The total storage budget is **1 GiB**, including uploads in progress and an allowance for metadata and filesystem blocks.
- Same-name uploads create separate files rather than overwriting each other.
- Deletion requires the owner's Bearer token.

Webnote is for temporary handoffs. There is no account system, file-list API, web editor, or file browser. Keep files you need later in durable storage.

## Run it locally

Install Linux Docker Engine and the Docker Compose plugin, open this source directory, then run:

```sh
./webnote.sh init http://127.0.0.1:8765
./webnote.sh build
./webnote.sh up
curl --fail http://127.0.0.1:8765/healthz
```

The health response is `{"status": "ok"}`. The launcher creates a private `.env` with a random deletion token. Uploaded files live in the Docker volume `webnote_data`, independently of this source directory.

If your account cannot access the Docker daemon, run Docker-backed launcher commands with `sudo`, for example `sudo ./webnote.sh build` and `sudo ./webnote.sh up`. The `init` command itself does not need Docker privileges. The installation requires Compose v2 (`docker compose`); the older `docker-compose` command is not a substitute.

For access from other devices, initialize with your own HTTPS origin instead, such as `https://webnote.example.com`, and configure an HTTPS reverse proxy to forward to `127.0.0.1:8765`. For an existing installation, edit `WEBNOTE_PUBLIC_URL` in the private `.env` and run `./webnote.sh up`; do not run `init` again. DNS, certificates, and the reverse proxy are configured separately.

## Upload

These examples use the local service. For a remote deployment, substitute your HTTPS origin.

```sh
curl --fail-with-body http://127.0.0.1:8765/api/files \
  -H 'Content-Type: application/octet-stream' \
  -H 'X-Filename: report.md' \
  --data-binary @report.md
```

Send raw file bytes, not a multipart form. Curl supplies the required Content-Length when uploading a regular file this way. Percent-encode non-ASCII UTF-8 filenames.

The JSON response contains `id`, `filename`, `bytes`, `sha256`, `download_url`, and `expires_at`. Pass the download URL, filename, SHA-256, and expiry time to the receiving person or session.

## Download and verify

Use the `download_url` returned by the upload:

```sh
curl --fail --location 'RETURNED_DOWNLOAD_URL' -o downloaded.md
sha256sum downloaded.md
```

Compare the hash with the upload response. If both files are on one machine, use `cmp report.md downloaded.md` to compare them directly. Expired files return HTTP 404; physical cleanup runs every 60 seconds.

## Delete

Set `WEBNOTE_TOKEN` privately in your shell, then use the exact uploaded filename:

```sh
curl --fail-with-body -X DELETE \
  http://127.0.0.1:8765/api/files/by-name \
  -H "Authorization: Bearer $WEBNOTE_TOKEN" \
  -H 'X-Filename: report.md'
```

Deletion is permanent. Duplicate names return HTTP 409; add `-H 'X-File-ID: RETURNED_ID'` to select one. Keep the token out of shell tracing, screenshots, and version control.

## Manage the service

```sh
./webnote.sh status
./webnote.sh logs
./webnote.sh down
./webnote.sh up
```

`down` retains the data volume. After changing `.env`, run `up` to recreate the container with the new configuration. Do not use `docker compose down -v` unless you intend to delete the stored data.

The container runs as UID/GID `10001:10001`, with a read-only root filesystem and loopback-only published port. Uploads over the total storage budget return HTTP 507.

## Further documentation

- [API_USAGE.txt](API_USAGE.txt): all endpoints, deletion options, and error responses. Also served at `/docs` and `/WEBNOTE_USAGE.txt`; replace its example domain with your own origin.
- [PORTABLE.md](PORTABLE.md): configuration, offline images, backup, and migration. Backup/restore preserves expiration deadlines.
- [AGENTS.md](AGENTS.md): installation and validation instructions for coding agents.
- [PORTABILITY_VERIFICATION.md](PORTABILITY_VERIFICATION.md): historical checks and their limitations.

Native release images target `linux/amd64` and `linux/arm64`. Existing local `dist/` artifacts may predate current source; build from source unless you have verified the release version. Generated archives and private deployment files are excluded from Git.

## Development

Host Python 3 is needed only for the optional tests; the deployed application uses container Python.

```sh
python3 -m unittest -v test_server.py
sh -n webnote.sh build-release.sh
```

## License

[MIT](LICENSE). Container base images retain their own licenses.
