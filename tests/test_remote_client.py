import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import remote_client as remote_client_module
from remote_client import (
    RemoteSendError,
    build_resume_id,
    connect_remote,
    normalize_base_url,
    send_file_to_remote,
    send_files_to_remote,
    verify_sha256,
)
from server import create_http_server
from transfer_state import TransferState


class RemoteClientTest(unittest.TestCase):
    def test_normalize_base_url_removes_trailing_slash(self):
        self.assertEqual(normalize_base_url("http://192.168.1.8:8765/"), "http://192.168.1.8:8765")

    def test_send_file_to_remote_uploads_to_other_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            source = root / "source.txt"
            source.write_text("hello pc", encoding="utf-8")

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=1024)
            state.access_code = "123456"
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_file_to_remote(base_url, "123456", source)

                self.assertEqual((receive_dir / "source.txt").read_text(encoding="utf-8"), "hello pc")
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_connect_remote_confirms_other_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TransferState(receive_dir=Path(tmp), max_upload_bytes=1024)
            state.access_code = "123456"
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                result = connect_remote(base_url, "123456")

                self.assertEqual(result["status"], "connected")
                self.assertIn("encryptionPublicKey", result)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_send_file_to_remote_can_encrypt_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            source = root / "secret.txt"
            source.write_text("encrypted hello", encoding="utf-8")

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=1024)
            state.access_code = "123456"
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_file_to_remote(base_url, "123456", source, encrypted=True)

                self.assertEqual((receive_dir / "secret.txt").read_text(encoding="utf-8"), "encrypted hello")
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_send_files_to_remote_uploads_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("first body", encoding="utf-8")
            second.write_text("second body", encoding="utf-8")

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=1024)
            state.access_code = "123456"
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_files_to_remote(base_url, "123456", [first, second])

                self.assertEqual((receive_dir / "first.txt").read_text(encoding="utf-8"), "first body")
                self.assertEqual((receive_dir / "second.txt").read_text(encoding="utf-8"), "second body")
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_send_files_to_remote_preserves_folder_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            folder = root / "album"
            nested = folder / "nested"
            nested.mkdir(parents=True)
            (folder / "cover.txt").write_text("cover", encoding="utf-8")
            (nested / "photo.txt").write_text("photo", encoding="utf-8")

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=1024)
            state.access_code = "123456"
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_files_to_remote(base_url, "123456", [folder])

                self.assertEqual((receive_dir / "album" / "cover.txt").read_text(encoding="utf-8"), "cover")
                self.assertEqual((receive_dir / "album" / "nested" / "photo.txt").read_text(encoding="utf-8"), "photo")
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_send_files_to_remote_encrypts_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            first = root / "secret-a.txt"
            second = root / "secret-b.txt"
            first.write_text("secret a", encoding="utf-8")
            second.write_text("secret b", encoding="utf-8")

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=1024)
            state.access_code = "123456"
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_files_to_remote(base_url, "123456", [first, second], encrypted=True)

                self.assertEqual((receive_dir / "secret-a.txt").read_text(encoding="utf-8"), "secret a")
                self.assertEqual((receive_dir / "secret-b.txt").read_text(encoding="utf-8"), "secret b")
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_send_files_to_remote_encrypts_large_file_in_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            source = root / "large-secret.bin"
            content = (b"0123456789abcdef" * 40) + b"tail"
            source.write_bytes(content)

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=2048)
            state.access_code = "123456"
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_files_to_remote(base_url, "123456", [source], encrypted=True, encrypted_chunk_size=31)

                self.assertEqual((receive_dir / "large-secret.bin").read_bytes(), content)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_send_files_to_remote_reports_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"abc")
            second.write_bytes(b"defg")
            progress_events = []

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=1024)
            state.access_code = "123456"
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_files_to_remote(base_url, "123456", [first, second], progress_callback=progress_events.append)

                self.assertEqual(progress_events[-1]["totalBytesSent"], 7)
                self.assertEqual(progress_events[-1]["totalBytes"], 7)
                self.assertEqual(progress_events[-1]["fileCount"], 2)
                self.assertTrue(progress_events[-1]["verified"])
                self.assertEqual(len(progress_events[-1]["sha256"]), 64)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_send_files_to_remote_retries_temporary_send_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            source = root / "retry.txt"
            source.write_text("retry body", encoding="utf-8")

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=1024)
            state.access_code = "123456"
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            original_upload = remote_client_module._upload
            calls = []

            def flaky_upload(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    raise RemoteSendError("temporary network failure")
                return original_upload(*args, **kwargs)

            remote_client_module._upload = flaky_upload
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_files_to_remote(base_url, "123456", [source], max_retries=2, retry_delay_seconds=0)

                self.assertEqual(len(calls), 2)
                self.assertEqual((receive_dir / "retry.txt").read_text(encoding="utf-8"), "retry body")
            finally:
                remote_client_module._upload = original_upload
                httpd.shutdown()
                httpd.server_close()

    def test_verify_sha256_rejects_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.txt"
            source.write_text("body", encoding="utf-8")

            with self.assertRaises(RemoteSendError):
                verify_sha256(source, "0" * 64)

    def test_send_files_to_remote_resumes_plain_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            source = root / "resume.txt"
            content = b"already here and then the rest"
            source.write_bytes(content)
            progress_events = []

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=1024)
            state.access_code = "123456"
            resume_id = build_resume_id(source)
            part_path = state.resume_part_path(source.name, resume_id)
            part_path.parent.mkdir(parents=True, exist_ok=True)
            part_path.write_bytes(content[:12])
            state.write_resume_metadata(source.name, len(content), resume_id, encrypted=False)
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_files_to_remote(base_url, "123456", [source], progress_callback=progress_events.append)

                self.assertEqual((receive_dir / "resume.txt").read_bytes(), content)
                self.assertFalse(part_path.exists())
                self.assertGreaterEqual(progress_events[0]["fileBytesSent"], 12)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_send_files_to_remote_resumes_encrypted_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receive_dir = root / "receive"
            source = root / "resume-secret.bin"
            content = b"encrypted resume content" * 20
            source.write_bytes(content)

            state = TransferState(receive_dir=receive_dir, max_upload_bytes=2048)
            state.access_code = "123456"
            resume_id = build_resume_id(source)
            part_path = state.resume_part_path(source.name, resume_id)
            part_path.parent.mkdir(parents=True, exist_ok=True)
            part_path.write_bytes(content[:37])
            state.write_resume_metadata(source.name, len(content), resume_id, encrypted=True)
            httpd = create_http_server(("127.0.0.1", 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{httpd.server_address[1]}"

                send_files_to_remote(base_url, "123456", [source], encrypted=True, encrypted_chunk_size=29)

                self.assertEqual((receive_dir / "resume-secret.bin").read_bytes(), content)
                self.assertFalse(part_path.exists())
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_send_files_to_remote_rejects_empty_selection(self):
        with self.assertRaises(ValueError):
            send_files_to_remote("http://127.0.0.1:9", "123456", [])


if __name__ == "__main__":
    unittest.main()
