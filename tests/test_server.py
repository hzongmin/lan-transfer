import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import create_http_server
from transfer_state import TransferState
from encryption_utils import encrypt_for_peer


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = [1000.0]
        self.state = TransferState(
            receive_dir=self.root,
            max_upload_bytes=1024,
            client_timeout_seconds=60,
            clock=lambda: self.now[0],
        )
        self.state.access_code = "123456"
        self.httpd = create_http_server(("127.0.0.1", 0), self.state)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, dict(response.getheaders()), data

    def test_files_requires_access_code(self):
        status, _, body = self.request("GET", "/api/files?code=000000")

        self.assertEqual(status, 403)
        self.assertIn(b"Invalid access code", body)

    def test_connect_registers_phone_client(self):
        status, _, body = self.request(
            "POST",
            "/api/connect?code=123456",
            body=json.dumps({"device": "Phone Browser"}).encode("utf-8"),
            headers={"Content-Length": "27", "Content-Type": "application/json"},
        )

        self.assertEqual(status, 200)
        self.assertIn(b"connected", body)
        self.assertEqual(self.state.connected_clients_json()[0]["device"], "Phone Browser")

    def test_connect_returns_encryption_public_key(self):
        status, _, body = self.request(
            "POST",
            "/api/connect?code=123456",
            body=json.dumps({"device": "Other PC"}).encode("utf-8"),
            headers={"Content-Length": "22", "Content-Type": "application/json"},
        )

        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("encryptionPublicKey", payload)
        self.assertGreater(len(payload["encryptionPublicKey"]), 30)

    def test_files_returns_shared_files(self):
        shared = self.root / "pc.txt"
        shared.write_text("from pc", encoding="utf-8")
        self.state.add_pending_file(shared)
        self.state.publish_pending_files()
        self.state.register_client("127.0.0.1", "Phone Browser")

        status, _, body = self.request("GET", "/api/files?code=123456")

        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["files"][0]["name"], "pc.txt")

    def test_upload_saves_file(self):
        self.state.register_client("127.0.0.1", "Phone Browser")

        status, _, _ = self.request(
            "POST",
            "/api/upload?code=123456&filename=phone.txt",
            body=b"hello",
            headers={"Content-Length": "5"},
        )

        self.assertEqual(status, 201)
        self.assertEqual((self.root / "phone.txt").read_bytes(), b"hello")

    def test_encrypted_upload_decrypts_and_saves_file(self):
        self.state.register_client("127.0.0.1", "Other PC")
        encrypted = encrypt_for_peer(self.state.encryption_public_key, b"secret pc file")

        status, _, _ = self.request(
            "POST",
            "/api/upload-encrypted?code=123456&filename=secure.txt",
            body=json.dumps(encrypted).encode("utf-8"),
            headers={"Content-Length": str(len(json.dumps(encrypted).encode("utf-8"))), "Content-Type": "application/json"},
        )

        self.assertEqual(status, 201)
        self.assertEqual((self.root / "secure.txt").read_bytes(), b"secret pc file")

    def test_upload_rejects_large_file(self):
        self.state.register_client("127.0.0.1", "Phone Browser")

        status, _, body = self.request(
            "POST",
            "/api/upload?code=123456&filename=large.bin",
            body=b"abcdef",
            headers={"Content-Length": "2048"},
        )

        self.assertEqual(status, 413)
        self.assertIn(b"too large", body)

    def test_download_returns_shared_file(self):
        shared = self.root / "pc.txt"
        shared.write_text("from pc", encoding="utf-8")
        file_id = self.state.add_pending_file(shared)
        self.state.publish_pending_files()
        self.state.register_client("127.0.0.1", "Phone Browser")

        status, headers, body = self.request("GET", f"/api/download/{file_id}?code=123456")

        self.assertEqual(status, 200)
        self.assertEqual(body, b"from pc")
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_files_disconnects_after_one_minute_in_timeout_test(self):
        self.state.register_client("127.0.0.1", "Phone Browser")
        self.now[0] += 61

        status, _, body = self.request("GET", "/api/files?code=123456")

        self.assertEqual(status, 403)
        self.assertIn(b"Connection expired", body)

    def test_phone_page_does_not_restore_cached_access_code(self):
        status, _, body = self.request("GET", "/")

        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertNotIn("localStorage.getItem('lanTransferCode')", html)
        self.assertNotIn("localStorage.setItem('lanTransferCode'", html)

    def test_phone_page_can_disconnect_without_reload_on_invalid_code(self):
        status, _, body = self.request("GET", "/")

        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("function disconnect", html)
        self.assertIn("连接已断开，请输入新的访问码", html)

    def test_phone_download_does_not_navigate_current_page(self):
        status, _, body = self.request("GET", "/")

        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("function downloadFile", html)
        self.assertIn("downloadFrame", html)
        self.assertNotIn("link.href = `/api/download/", html)


if __name__ == "__main__":
    unittest.main()
