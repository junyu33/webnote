#!/usr/bin/env python3
"""Public, byte-preserving file transfer API with authenticated deletion."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit


FILE_ID = re.compile(r"[0-9a-f]{32}")
STALE_PART = re.compile(r"\.upload-[a-z0-9_]{8}|\.delete-[0-9a-f]{32}")


def utc_now():
    return datetime.now(timezone.utc)


def charge(length):
    # Account for small/empty uploads, metadata and directory blocks as well.
    return ((length + 4095) // 4096) * 4096 + 16384


def storage_file(folder, name):
    fd = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    source = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        source.close()
        raise ValueError("unexpected storage file")
    return source


class TransferServer(ThreadingHTTPServer):
    def release(self, file_id):
        self.used_bytes -= self.charges.pop(file_id, 0)

    def expiry(self, metadata, source):
        if "expires_at" not in metadata:
            # Pre-TTL uploads recorded their completion in the metadata mtime.
            timestamp = os.fstat(source.fileno()).st_mtime
            metadata["expires_at"] = self.deadline(datetime.fromtimestamp(timestamp, timezone.utc))
        return datetime.fromisoformat(metadata["expires_at"])

    def deadline(self, timestamp):
        return (timestamp + self.ttl).isoformat(timespec="milliseconds")

    def service_actions(self):
        if time.monotonic() < self.next_cleanup:
            return
        self.next_cleanup = time.monotonic() + self.cleanup_seconds
        with self.mutation_lock:
            due = [key for key, expiry in self.deadlines.items()
                   if expiry <= time.time() and not self.readers.get(key)]
        for file_id in due:
            with self.mutation_lock:
                if file_id not in self.deadlines or self.readers.get(file_id):
                    continue
                try:
                    folder = self.storage / file_id
                    # Only remove ordinary application-owned upload directories.
                    if (not folder.is_symlink() and
                            {item.name for item in folder.iterdir()} == {"content", "metadata.json"} and
                            all(stat.S_ISREG((folder / name).lstat().st_mode)
                                for name in ("content", "metadata.json"))):
                        shutil.rmtree(folder)
                    else:
                        continue
                except FileNotFoundError:
                    if folder.exists():
                        continue
                except OSError:
                    print("Expired upload cleanup failed", flush=True)
                    continue
                self.deadlines.pop(file_id, None)
                self.release(file_id)


def decode_filename(value):
    if value is None or re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError("invalid filename encoding")
    name = unquote(value, encoding="utf-8", errors="strict")
    if (not name or name in {".", ".."} or len(name.encode("utf-8")) > 1024
            or any(c in name for c in "/\\:")
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError("expected a filename without directory components")
    return name


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def authorized(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1 or not hmac.compare_digest(
            values[0].encode(), ("Bearer " + self.server.token).encode()
        ):
            self.reply(401, {"error": "Bearer token required"})
            return False
        return True

    def do_POST(self):
        if urlsplit(self.path).path != "/api/files":
            self.reply(404, {"error": "not found"})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding"):
            self.reply(400, {"error": "Transfer-Encoding unsupported; use Content-Length"})
            return
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,16}", lengths[0]):
            self.reply(411, {"error": "one valid Content-Length required"})
            return
        length = int(lengths[0])
        if length > self.server.max_bytes:
            self.reply(413, {"error": "file too large", "max_bytes": self.server.max_bytes})
            return
        try:
            filename = decode_filename(self.headers.get("X-Filename", "file"))
        except (ValueError, UnicodeError):
            self.reply(400, {"error": "invalid X-Filename"})
            return
        file_id = secrets.token_hex(16)
        cost = charge(length)
        with self.server.mutation_lock:
            admitted = self.server.used_bytes + cost <= self.server.max_storage
            if admitted:
                self.server.used_bytes += cost
                self.server.charges[file_id] = cost
        if not admitted:
            self.reply(507, {"error": "storage capacity reached",
                             "max_storage_bytes": self.server.max_storage})
            return
        temporary = None
        committed = False
        digest = hashlib.sha256()
        try:
            temporary = Path(tempfile.mkdtemp(prefix=".upload-", dir=self.server.storage))
            remaining = length
            with (temporary / "content").open("wb") as output:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        self.reply(400, {"error": "incomplete upload"})
                        return
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            metadata = {
                "id": file_id, "filename": filename, "bytes": length,
                "sha256": digest.hexdigest(),
                "download_url": self.server.public_url + "/api/files/" + file_id,
                "expires_at": self.server.deadline(utc_now()),
            }
            with (temporary / "metadata.json").open("w", encoding="utf-8") as output:
                json.dump(metadata, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            with self.server.mutation_lock:
                os.rename(temporary, self.server.storage / file_id)
                committed = True
                self.server.deadlines[file_id] = datetime.fromisoformat(metadata["expires_at"]).timestamp()
            descriptor = os.open(self.server.storage, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.reply(201, metadata)
        except TimeoutError:
            self.reply(408, {"error": "upload timeout"})
        except OSError:
            self.reply(500, {"error": "storage failure"})
        finally:
            if not committed:
                try:
                    if temporary is not None:
                        shutil.rmtree(temporary)
                except OSError:
                    print("Incomplete upload cleanup failed", flush=True)
                else:
                    with self.server.mutation_lock:
                        self.server.release(file_id)

    def do_GET(self):
        self.download(False)

    def do_HEAD(self):
        self.download(True)

    def do_DELETE(self):
        if not self.authorized():
            return
        if self.path != "/api/files/by-name":
            self.reply(404, {"error": "not found"})
            return
        names = self.headers.get_all("X-Filename", [])
        try:
            if len(names) != 1:
                raise ValueError("one X-Filename required")
            filename = decode_filename(names[0])
        except (ValueError, UnicodeError):
            self.reply(400, {"error": "one valid X-Filename required; paths are forbidden"})
            return
        ids = self.headers.get_all("X-File-ID", [])
        if ids and (len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{32}", ids[0])):
            self.reply(400, {"error": "invalid X-File-ID"})
            return
        try:
            with self.server.mutation_lock:
                # User-supplied names are compared as metadata only. Directory
                # names come from the storage root, never from request values.
                root = os.open(self.server.storage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    matches = []
                    for file_id in os.listdir(root):
                        if not re.fullmatch(r"[0-9a-f]{32}", file_id):
                            continue
                        if ids and file_id != ids[0]:
                            continue
                        if self.server.deadlines.get(file_id, float("inf")) <= time.time():
                            continue
                        try:
                            folder = os.open(file_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                        except OSError:
                            continue
                        try:
                            fd = os.open("metadata.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
                            with os.fdopen(fd, "r", encoding="utf-8") as source:
                                info = os.fstat(source.fileno())
                                if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                                    continue
                                metadata = json.load(source)
                            if (isinstance(metadata, dict) and metadata.get("filename") == filename
                                    and metadata.get("id") == file_id):
                                matches.append(file_id)
                        except (OSError, ValueError, UnicodeError):
                            continue
                        finally:
                            os.close(folder)
                    if not matches:
                        self.reply(404, {"error": "filename not found"})
                        return
                    if len(matches) != 1:
                        self.reply(409, {"error": "multiple files have this filename; specify X-File-ID", "ids": matches})
                        return
                    file_id = matches[0]
                    folder = os.open(file_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                    try:
                        if set(os.listdir(folder)) != {"content", "metadata.json"} or not all(
                            stat.S_ISREG(os.stat(name, dir_fd=folder, follow_symlinks=False).st_mode)
                            for name in ("content", "metadata.json")
                        ):
                            self.reply(409, {"error": "unexpected storage entry; deletion refused"})
                            return
                        tombstone = ".delete-" + secrets.token_hex(16)
                        os.rename(file_id, tombstone, src_dir_fd=root, dst_dir_fd=root)
                    finally:
                        os.close(folder)
                    # rmtree uses fd-based symlink protection on supported Linux.
                    shutil.rmtree(tombstone, dir_fd=root)
                    self.server.deadlines.pop(file_id, None)
                    if not self.server.readers.get(file_id):
                        self.server.release(file_id)
                    os.fsync(root)
                finally:
                    os.close(root)
            self.reply(200, {"deleted": True, "id": file_id, "filename": filename})
        except OSError:
            self.reply(500, {"error": "storage failure"})

    def download(self, head):
        path = urlsplit(self.path).path
        if path in {"/", "/docs", "/docs/", "/WEBNOTE_USAGE.txt"}:
            body = Path(__file__).with_name("API_USAGE.txt").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if path == "/healthz" and not head:
            self.reply(200, {"status": "ok"})
            return
        match = re.fullmatch(r"/api/files/([0-9a-f]{32})(/meta)?", path)
        if not match:
            self.reply(404, {"error": "not found"})
            return
        folder = self.server.storage / match[1]
        file_id = match[1]
        try:
            with self.server.mutation_lock:
                if folder.is_symlink():
                    raise FileNotFoundError
                with storage_file(folder, "metadata.json") as source:
                    metadata = json.load(source)
                    if self.server.expiry(metadata, source) <= utc_now():
                        raise FileNotFoundError
                content = storage_file(folder, "content")
                self.server.readers[file_id] = self.server.readers.get(file_id, 0) + 1
        except (OSError, ValueError, KeyError, TypeError):
            self.reply(404, {"error": "not found"})
            return
        try:
            if match[2]:
                self.reply(200, metadata)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(metadata["bytes"]))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(metadata["filename"], safe=""))
            self.send_header("ETag", '"' + metadata["sha256"] + '"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head:
                shutil.copyfileobj(content, self.wfile, 1024 * 1024)
        finally:
            content.close()
            with self.server.mutation_lock:
                self.server.readers[file_id] -= 1
                if not self.server.readers[file_id]:
                    del self.server.readers[file_id]
                    if not folder.exists() and file_id not in self.server.deadlines:
                        self.server.release(file_id)

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, fmt, *args):
        # Do not log file IDs, names, query strings, or authorization headers.
        if len(args) >= 2:
            print(f"{self.command} {args[1]}", flush=True)


def create_server(host, port, storage, token, public_url, max_bytes=100 * 1024 * 1024,
                  *, ttl_seconds=1800, cleanup_seconds=60, max_storage=1024 ** 3):
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError("fd-based symlink-safe directory removal is required")
    if len(token) < 32 or not token.isascii():
        raise ValueError("WEBNOTE_TOKEN must contain at least 32 ASCII characters")
    storage = Path(storage)
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    server = TransferServer((host, port), Handler, bind_and_activate=False)
    server.storage = storage
    server.token = token
    server.public_url = public_url.rstrip("/")
    server.max_bytes = max_bytes
    server.mutation_lock = threading.Lock()
    server.ttl = timedelta(seconds=ttl_seconds)
    server.cleanup_seconds = cleanup_seconds
    server.next_cleanup = time.monotonic()
    server.max_storage = max_storage
    server.used_bytes = 0
    server.charges = {}
    server.deadlines = {}
    server.readers = {}
    try:
        for folder in storage.iterdir():
            if folder.is_symlink() or not folder.is_dir():
                continue
            if STALE_PART.fullmatch(folder.name):
                # One server owns this volume; interrupted uploads cannot resume.
                shutil.rmtree(folder)
            elif FILE_ID.fullmatch(folder.name):
                try:
                    with storage_file(folder, "content") as content:
                        cost = charge(os.fstat(content.fileno()).st_size)
                    server.charges[folder.name] = cost
                    server.used_bytes += cost
                    with storage_file(folder, "metadata.json") as source:
                        metadata = json.load(source)
                        server.deadlines[folder.name] = server.expiry(metadata, source).timestamp()
                except (OSError, ValueError, KeyError, TypeError):
                    print("Unexpected upload entry retained", flush=True)
        server.service_actions()
        server.server_bind()
        server.server_activate()
    except BaseException:
        server.server_close()
        raise
    return server


if __name__ == "__main__":
    os.umask(0o077)
    server = create_server(
        os.environ.get("WEBNOTE_HOST", "127.0.0.1"),
        int(os.environ.get("WEBNOTE_PORT", "8765")),
        os.environ.get("WEBNOTE_STORAGE", "./data"),
        os.environ["WEBNOTE_TOKEN"],
        os.environ.get("WEBNOTE_PUBLIC_URL", "http://127.0.0.1:8765"),
        int(os.environ.get("WEBNOTE_MAX_BYTES", str(100 * 1024 * 1024))),
    )
    server.serve_forever()
