import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transfer_state import InvalidSharedText, TransferState, UploadTooLarge


class TransferStateTest(unittest.TestCase):
    def test_latest_text_starts_empty_and_is_not_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = TransferState(receive_dir=root)

            self.assertEqual(first.latest_text_json()["text"], "")
            first.set_latest_text("hello", "desktop")

            second = TransferState(receive_dir=root)
            self.assertEqual(second.latest_text_json()["text"], "")
            self.assertEqual(second.latest_text_json()["version"], 0)

    def test_latest_text_overwrites_and_increments_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TransferState(receive_dir=Path(tmp), clock=lambda: 1234.5)

            first = state.set_latest_text(" first ", "desktop")
            second = state.set_latest_text("https://example.com", "phone")

            self.assertEqual(first["text"], "first")
            self.assertEqual(second["text"], "https://example.com")
            self.assertEqual(second["version"], first["version"] + 1)
            self.assertEqual(second["source"], "phone")
            self.assertEqual(second["updatedAt"], 1234.5)

    def test_latest_text_rejects_empty_and_too_long(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TransferState(receive_dir=Path(tmp))

            with self.assertRaises(InvalidSharedText):
                state.set_latest_text("   ", "desktop")
            with self.assertRaises(InvalidSharedText):
                state.set_latest_text("x" * 20001, "phone")

    def test_access_code_is_six_digits(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TransferState(receive_dir=Path(tmp), max_upload_bytes=1024)

            self.assertRegex(state.access_code, r"^\d{6}$")

    def test_shared_files_use_generated_ids_not_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "hello.txt"
            shared.write_text("hello", encoding="utf-8")
            state = TransferState(receive_dir=root, max_upload_bytes=1024)

            file_id = state.add_pending_file(shared)
            state.publish_pending_files()
            listing = state.shared_files_json()

            self.assertEqual(listing[0]["id"], file_id)
            self.assertEqual(listing[0]["name"], "hello.txt")
            self.assertNotIn(str(shared), str(listing))

    def test_pending_files_are_not_visible_until_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "draft.txt"
            shared.write_text("draft", encoding="utf-8")
            state = TransferState(receive_dir=root, max_upload_bytes=1024)

            state.add_pending_file(shared)

            self.assertEqual(state.pending_files_json()[0]["name"], "draft.txt")
            self.assertEqual(state.shared_files_json(), [])

    def test_publish_pending_files_makes_them_available_to_phone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "ready.txt"
            shared.write_text("ready", encoding="utf-8")
            state = TransferState(receive_dir=root, max_upload_bytes=1024)

            file_id = state.add_pending_file(shared)
            state.publish_pending_files()

            self.assertEqual(state.shared_files_json()[0]["id"], file_id)
            self.assertEqual(state.pending_files_json(), [])

    def test_publish_pending_files_appends_to_existing_phone_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            state = TransferState(receive_dir=root, max_upload_bytes=1024)

            state.add_pending_file(first)
            state.publish_pending_files()
            state.add_pending_file(second)
            state.publish_pending_files()

            names = [item["name"] for item in state.shared_files_json()]
            self.assertEqual(names, ["first.txt", "second.txt"])

    def test_rejects_upload_larger_than_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TransferState(receive_dir=Path(tmp), max_upload_bytes=5)

            with self.assertRaises(UploadTooLarge):
                state.reserve_upload("large.bin", 6)

    def test_records_uploaded_file_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TransferState(receive_dir=Path(tmp), max_upload_bytes=1024)
            dest = state.reserve_upload("note.txt", 4)
            dest.write_text("note", encoding="utf-8")
            state.record_upload(dest, 4)

            self.assertIn("Received note.txt", state.events[-1])

    def test_register_connected_client_updates_visible_client_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TransferState(receive_dir=Path(tmp), max_upload_bytes=1024)

            state.register_client("192.168.3.9", "Mobile Safari")

            clients = state.connected_clients_json()
            self.assertEqual(clients[0]["ip"], "192.168.3.9")
            self.assertEqual(clients[0]["device"], "Mobile Safari")
            self.assertIn("Connected Mobile Safari", state.events[-1])

    def test_client_expires_after_one_minute_in_timeout_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [1000.0]
            state = TransferState(
                receive_dir=Path(tmp),
                max_upload_bytes=1024,
                client_timeout_seconds=60,
                clock=lambda: now[0],
            )
            state.register_client("192.168.3.9", "Mobile Safari")

            now[0] += 61

            self.assertFalse(state.is_client_active("192.168.3.9"))
            self.assertEqual(state.connected_clients_json(), [])


if __name__ == "__main__":
    unittest.main()

