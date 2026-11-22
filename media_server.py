"""Authenticated read-only browsing and bounded-memory media streaming."""
import os
import re
import time
from urllib.parse import parse_qs, quote

from shared_library import media_type, preview_kind


def byte_range(value, size):
    if value is None:
        return 0, size - 1, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value.strip())
    if not match or size == 0:
        raise ValueError('Invalid range')
    first, last = match.groups()
    if not first:
        if not last or int(last) <= 0:
            raise ValueError('Invalid suffix')
        return max(0, size - int(last)), size - 1, True
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError('Unsatisfiable range')
    return start, end, True


class LibraryRequestMixin:
    def library_request(self, parsed, head=False):
        if parsed.path not in {'/api/library/roots', '/api/library/list', '/api/library/file'}:
            return False
        if not self._authorized(parsed) or not self._connected():
            return True
        self.transfer_state.touch_client(self.client_address[0])
        library = self.transfer_state.library
        query = parse_qs(parsed.query)
        key = query.get('root', [''])[0]
        relative = query.get('path', [''])[0]
        try:
            if parsed.path == '/api/library/roots':
                self._send_json(200, {'roots': library.roots()})
            elif parsed.path == '/api/library/list':
                self._send_json(200, {'entries': library.entries(key, relative)})
            else:
                self._library_file(library, key, relative, query, head)
        except PermissionError:
            self._send_json(403, {'error': '没有权限访问此文件或目录'})
        except (FileNotFoundError, NotADirectoryError):
            self._send_json(404, {'error': '文件不存在、磁盘离线或已停止共享'})
        except (OSError, ValueError, RuntimeError):
            self._send_json(400, {'error': '无法读取此文件或目录'})
        return True

    def _library_file(self, library, key, relative, query, head):
        request_code = query.get('code', [''])[0]
        path = library.resolve(key, relative)
        if not path.is_file():
            raise FileNotFoundError('Not a regular file')
        with path.open('rb') as source:
            size = os.fstat(source.fileno()).st_size
            try:
                start, end, partial = byte_range(self.headers.get('Range'), size)
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', 'bytes */%d' % size)
                self.send_header('Content-Length', '0')
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                return
            download = query.get('download', [''])[0] == '1' or preview_kind(path) == 'download'
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type', 'application/octet-stream' if download else media_type(path))
            self.send_header('Content-Length', str(max(0, end - start + 1)))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "sandbox; default-src 'none'")
            self.send_header('Referrer-Policy', 'no-referrer')
            disposition = 'attachment' if download else 'inline'
            self.send_header('Content-Disposition', "%s; filename*=UTF-8''%s" % (disposition, quote(path.name, safe='')))
            if partial:
                self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
            self.end_headers()
            if head:
                return
            source.seek(start)
            remaining = max(0, end - start + 1)
            last_refresh = 0
            try:
                while remaining:
                    if request_code != self.transfer_state.access_code:
                        break
                    # Revoking the share also stops an already-running response.
                    if library.resolve(key, relative) != path:
                        break
                    now = time.monotonic()
                    if now - last_refresh >= 30:
                        if not self.transfer_state.touch_client(self.client_address[0]):
                            break
                        last_refresh = now
                    chunk = source.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (OSError, ValueError, RuntimeError):
                # Headers have been sent. Never append JSON to a media response.
                self.close_connection = True
