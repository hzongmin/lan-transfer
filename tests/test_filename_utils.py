import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filename_utils import safe_filename, unique_destination


class FilenameUtilsTest(unittest.TestCase):
    def test_safe_filename_removes_path_segments_and_reserved_chars(self):
        self.assertEqual(safe_filename("../../bad:name?.txt"), "bad_name_.txt")
        self.assertEqual(safe_filename("folder\\nested\\photo.png"), "photo.png")

    def test_safe_filename_uses_default_for_blank_names(self):
        self.assertEqual(safe_filename("   "), "upload.bin")
        self.assertEqual(safe_filename("..."), "upload.bin")

    def test_unique_destination_stays_inside_receive_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = unique_destination(root, "../escape.txt")

            self.assertEqual(dest.parent, root)
            self.assertEqual(dest.name, "escape.txt")

    def test_unique_destination_adds_counter_when_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "report.txt").write_text("existing", encoding="utf-8")

            dest = unique_destination(root, "report.txt")

            self.assertEqual(dest.name, "report (2).txt")


if __name__ == "__main__":
    unittest.main()
