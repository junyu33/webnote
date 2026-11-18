import hashlib
import http.client
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from server import charge, create_server


class TransferTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.token = "test-token-" + "a" * 32
        self.start()

    def start(self, **settings):
        self.server = create_server("127.0.0.1", 0, self.directory.name, self.token, "https://webnote.example.com", 2000000, **settings)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def tearDown(self):
        self.stop()
        self.directory.cleanup()

    def request(self, method, path, body=None, headers=None, auth=False):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        headers = dict(headers or {})
        if auth:
            headers["Authorization"] = "Bearer " + self.token
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_binary_roundtrip_and_restart(self):
        for body in [b"", b"  text\r\n\nend \n", bytes(range(256)) * 4096]:
            status, _, raw = self.request("POST", "/api/files", body, {"X-Filename": "%E6%96%87%E4%BB%B6.bin"})
            self.assertEqual(status, 201)
            metadata = json.loads(raw)
            self.assertEqual(metadata["bytes"], len(body))
            self.assertEqual(metadata["sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(metadata["filename"], "文件.bin")
            path = "/api/files/" + metadata["id"]
            self.assertEqual(self.request("GET", path, auth=False)[0], 200)
            self.stop()
            self.start()
            self.assertEqual(self.request("GET", path)[2], body)
            status, headers, raw = self.request("HEAD", path)
            self.assertEqual((status, raw), (200, b""))
            self.assertEqual(int(headers["Content-Length"]), len(body))
            self.assertEqual(json.loads(self.request("GET", path + "/meta")[2]), metadata)

    def test_reject_invalid_requests(self):
        self.assertEqual(self.request("POST", "/api/files", headers={"Content-Length": "2000001"})[0], 413)
        self.assertEqual(self.request("POST", "/api/files", b"x", {"X-Filename": "%0A"})[0], 400)
        self.assertEqual(self.request("GET", "/api/files/../../etc/passwd")[0], 404)
        self.assertEqual(self.request("GET", "/api/files/" + "0" * 32)[0], 404)

    def test_public_transfer_and_private_delete(self):
        metadata = self.upload("public.bin")
        path = "/api/files/" + metadata["id"]
        for method, suffix in [("GET", ""), ("HEAD", ""), ("GET", "/meta"), ("HEAD", "/meta")]:
            with self.subTest(method=method, suffix=suffix):
                self.assertEqual(self.request(method, path + suffix)[0], 200)
                self.assertEqual(self.request(method, path + suffix, headers={"Authorization": "Bearer wrong"})[0], 200)
        self.assertEqual(self.delete("public.bin", auth=False)[0], 401)
        self.assertEqual(self.request("DELETE", "/api/files/by-name", headers={
            "X-Filename": "public.bin", "Authorization": "Bearer wrong",
        })[0], 401)
        self.assertEqual(self.request("GET", path)[2], b"preserve these bytes\x00\r\n")
        self.assertEqual(self.delete("public.bin")[0], 200)
        self.assertEqual(self.request("GET", path)[0], 404)

    def upload(self, filename, body=b"preserve these bytes\x00\r\n"):
        status, _, raw = self.request("POST", "/api/files", body, {"X-Filename": filename})
        self.assertEqual(status, 201)
        return json.loads(raw)

    def delete(self, filename=None, file_id=None, auth=True):
        headers = {} if filename is None else {"X-Filename": filename}
        if file_id is not None:
            headers["X-File-ID"] = file_id
        return self.request("DELETE", "/api/files/by-name", headers=headers, auth=auth)

    def test_delete_by_name_and_auth(self):
        metadata = self.upload("%E6%96%87%E4%BB%B6%20%23100%25.bin")
        other = self.upload("keep.bin")
        name = "%E6%96%87%E4%BB%B6%20%23100%25.bin"
        self.assertEqual(self.delete(name, auth=False)[0], 401)
        self.assertEqual(self.delete("missing.bin")[0], 404)
        self.assertEqual(self.delete()[0], 400)
        status, _, raw = self.delete(name)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"deleted": True, "id": metadata["id"], "filename": metadata["filename"]})
        self.assertEqual(self.request("GET", "/api/files/" + metadata["id"])[0], 404)
        self.assertEqual(self.request("GET", "/api/files/" + other["id"])[0], 200)
        self.assertEqual(self.delete(name)[0], 404)

    def test_duplicate_name_requires_matching_id(self):
        first = self.upload("same.bin")
        second = self.upload("same.bin")
        other = self.upload("other.bin")
        self.assertEqual(self.delete("same.bin")[0], 409)
        self.assertEqual(self.delete("same.bin", "../outside")[0], 400)
        self.assertEqual(self.delete("same.bin", other["id"])[0], 404)
        self.assertEqual(self.delete("same.bin", first["id"])[0], 200)
        self.assertEqual(self.request("GET", "/api/files/" + second["id"])[0], 200)
        self.assertEqual(self.delete("same.bin")[0], 200)

    def test_traversal_and_symlink_protection(self):
        with tempfile.TemporaryDirectory() as outside:
            sentinel = Path(outside) / "sentinel"
            sentinel.write_bytes(b"must not be deleted")
            for name in ["..", ".", "../sentinel", "%2e%2e%2fsentinel", "%2Fetc%2Fpasswd",
                         "..%5csentinel", "C%3A%5csentinel", "%00", "%0d%0a", "%FF", "%GG", ""]:
                with self.subTest(name=name):
                    self.assertEqual(self.delete(name)[0], 400)
                    self.assertEqual(self.request("POST", "/api/files", b"x", {"X-Filename": name})[0], 400)
            # Double-encoded input remains a literal metadata name, never a path.
            self.assertEqual(self.delete("%252e%252e%252fsentinel")[0], 404)
            root = Path(self.directory.name)
            (root / ("a" * 32)).symlink_to(outside, target_is_directory=True)
            self.assertEqual(self.delete("sentinel")[0], 404)
            metadata = self.upload("symlink.bin")
            folder = root / metadata["id"]
            (folder / "content").unlink()
            (folder / "content").symlink_to(sentinel)
            self.assertEqual(self.delete("symlink.bin")[0], 409)
            # A forged metadata ID cannot redirect deletion outside the root.
            (folder / "metadata.json").write_text(json.dumps({"id": "../sentinel", "filename": "forged.bin"}))
            self.assertEqual(self.delete("forged.bin")[0], 404)
            self.assertEqual(sentinel.read_bytes(), b"must not be deleted")

    def test_concurrent_deletes(self):
        metadata = self.upload("race.bin")
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: self.delete("race.bin")[0], range(2)))
        self.assertEqual(sorted(statuses), [200, 404])
        self.assertFalse((Path(self.directory.name) / metadata["id"]).exists())

    def test_expiry_precedes_storage_cleanup(self):
        self.stop()
        self.start(ttl_seconds=0.3, cleanup_seconds=60)
        metadata = self.upload("temporary.bin")
        path = "/api/files/" + metadata["id"]
        folder = Path(self.directory.name) / metadata["id"]
        self.assertEqual(self.request("GET", path)[0], 200)
        self.assertEqual(json.loads(self.request("GET", path + "/meta")[2])["expires_at"], metadata["expires_at"])
        time.sleep(0.35)
        for method in ("GET", "HEAD"):
            for suffix in ("", "/meta"):
                self.assertEqual(self.request(method, path + suffix)[0], 404)
        self.assertTrue(folder.exists())
        self.assertEqual(self.delete("temporary.bin")[0], 404)
        self.server.next_cleanup = 0
        limit = time.monotonic() + 2
        while folder.exists() and time.monotonic() < limit:
            time.sleep(0.02)
        self.assertFalse(folder.exists())
        self.assertEqual(self.server.used_bytes, 0)

    def test_parallel_quota_and_restart(self):
        self.stop()
        self.start(max_storage=charge(12))
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: self.request(
                "POST", "/api/files", b"x" * 12, {"X-Filename": "quota.bin"})[0], range(2)))
        self.assertEqual(sorted(statuses), [201, 507])
        self.stop()
        self.start(max_storage=charge(12))
        self.assertEqual(self.request("POST", "/api/files", b"x")[0], 507)
        self.assertEqual(self.delete("quota.bin")[0], 200)
        self.upload("now-fits.bin", b"x")


if __name__ == "__main__":
    unittest.main()
