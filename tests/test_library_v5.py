"""V5 regression cases. Execution is intentionally left to user approval."""
import http.client
import json
import tempfile
import threading
import unittest
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_server import byte_range
from server import create_http_server
from shared_library import SharedLibrary
from transfer_state import TransferState


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = self.root / 'shared'
        self.shared.mkdir()
        self.library = SharedLibrary(self.root / 'settings.json')
        self.key = self.library.add(self.shared)

    def test_persistence_and_removal_do_not_delete_files(self):
        file = self.shared / 'keep.txt'
        file.write_text('keep')
        loaded = SharedLibrary(self.root / 'settings.json')
        self.assertEqual(loaded.roots()[0]['name'], 'shared')
        self.library.remove(self.key)
        self.assertTrue(file.exists())
        self.assertEqual(SharedLibrary(self.root / 'settings.json').roots(), [])

    def test_rejects_traversal_absolute_paths_and_ads(self):
        for path in ('../secret', '/secret', 'C:/secret', 'a\\b', 'movie.mp4:secret', '\x00'):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                self.library.resolve(self.key, path)

    def test_nested_unicode_listing_and_unknown_root(self):
        (self.shared / '电影').mkdir()
        (self.shared / '电影' / '片段.mp4').write_bytes(b'video')
        entries = self.library.entries(self.key, '电影')
        self.assertEqual(entries[0]['path'], '电影/片段.mp4')
        self.assertEqual(entries[0]['kind'], 'video')
        with self.assertRaises(FileNotFoundError):
            self.library.resolve('unknown')

    def test_external_symlink_is_not_exposed(self):
        outside = self.root / 'private.txt'
        outside.write_text('secret')
        try:
            (self.shared / 'link.txt').symlink_to(outside)
        except OSError:
            self.skipTest('Creating symlinks requires OS permission')
        with self.assertRaises(PermissionError):
            self.library.resolve(self.key, 'link.txt')
        self.assertEqual(self.library.entries(self.key), [])

    def test_ranges(self):
        self.assertEqual(byte_range('bytes=2-5', 10), (2, 5, True))
        self.assertEqual(byte_range('bytes=7-', 10), (7, 9, True))
        self.assertEqual(byte_range('bytes=-3', 10), (7, 9, True))
        self.assertEqual(byte_range('bytes=0-999', 10), (0, 9, True))
        self.assertEqual(byte_range(None, 0), (0, -1, False))
        for value in ('bytes=10-', 'bytes=3-1', 'bytes=-0', 'bytes=0-1,4-5', 'bytes=-'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                byte_range(value, 10)


class LibraryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / 'shared'
        self.folder.mkdir()
        (self.folder / 'clip.mp4').write_bytes(b'0123456789')
        (self.folder / 'page.html').write_text('<script>alert(1)</script>')
        self.state = TransferState(self.root / 'received')
        self.state.access_code = '123456'
        self.key = self.state.library.add(self.folder)
        self.state.register_client('127.0.0.1', 'Test phone')
        self.server = create_http_server(('127.0.0.1', 0), self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method='GET', filename='clip.mp4', code='123456', headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        url = '/api/library/file?' + urlencode({'root': self.key, 'path': filename, 'code': code})
        try:
            conn.request(method, url, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_range_and_head(self):
        status, headers, body = self.request(headers={'Range': 'bytes=2-5'})
        self.assertEqual((status, body), (206, b'2345'))
        self.assertEqual(headers['Content-Range'], 'bytes 2-5/10')
        status, headers, body = self.request('HEAD')
        self.assertEqual((status, body, headers['Content-Length']), (200, b'', '10'))

    def test_unsatisfiable_range_and_empty_file(self):
        status, headers, body = self.request(headers={'Range': 'bytes=10-'})
        self.assertEqual((status, headers['Content-Range'], body), (416, 'bytes */10', b''))
        (self.folder / 'empty.mp4').write_bytes(b'')
        self.assertEqual(self.request(filename='empty.mp4')[0], 200)
        self.assertEqual(self.request(filename='empty.mp4', headers={'Range': 'bytes=0-'})[0], 416)

    def test_auth_and_revocation(self):
        self.assertEqual(self.request(code='wrong')[0], 403)
        self.state.library.remove(self.key)
        self.assertEqual(self.request()[0], 404)

    def test_active_content_is_forced_to_download(self):
        status, headers, body = self.request(filename='page.html')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(headers['Content-Type'], 'application/octet-stream')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')


if __name__ == '__main__':
    unittest.main()
