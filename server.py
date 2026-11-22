import json
import mimetypes
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from encryption_utils import STREAM_VERSION, create_stream_receiver, decrypt_from_peer, decrypt_stream_chunk
from hash_utils import sha256_file
from transfer_state import InvalidSharedText, MAX_SHARED_TEXT_CHARS, TransferState, UploadTooLarge
from web_ui import PHONE_PAGE
from media_server import LibraryRequestMixin


def create_http_server(address, state: TransferState) -> ThreadingHTTPServer:
    class TransferRequestHandler(RequestHandler):
        transfer_state = state

    return ThreadingHTTPServer(address, TransferRequestHandler)


class RequestHandler(LibraryRequestMixin, BaseHTTPRequestHandler):
    transfer_state: TransferState
    server_version = "LanTransfer/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if self.library_request(parsed):
            return
        if parsed.path == "/":
            self._send_bytes(HTTPStatus.OK, PHONE_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/files":
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            payload = {"files": self.transfer_state.shared_files_json()}
            self._send_json(HTTPStatus.OK, payload)
            return
        if parsed.path == "/api/text":
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            self._send_json(HTTPStatus.OK, self.transfer_state.latest_text_json())
            return
        if parsed.path.startswith("/api/download/"):
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            file_id = parsed.path.rsplit("/", 1)[-1]
            self._download(file_id)
            return
        if parsed.path == "/qr.svg":
            query = parse_qs(parsed.query)
            text = query.get("text", [""])[0]
            self._send_bytes(HTTPStatus.OK, _simple_qr_svg(text).encode("utf-8"), "image/svg+xml")
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if not self.library_request(parsed, head=True):
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == '/api/library/heartbeat':
            if self._authorized(parsed) and self._connected():
                self.transfer_state.touch_client(self.client_address[0])
                self._send_json(200, {'status': 'connected'})
            return
        if parsed.path == "/api/resume-status":
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            self._resume_status()
            return
        if parsed.path == "/api/upload":
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            self._upload(parsed)
            return
        if parsed.path == "/api/text":
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            self._set_text()
            return
        if parsed.path == "/api/upload-resume":
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            self._upload_resume(parsed)
            return
        if parsed.path == "/api/upload-encrypted":
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            self._upload_encrypted(parsed)
            return
        if parsed.path == "/api/upload-encrypted-stream":
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            self._upload_encrypted_stream(parsed)
            return
        if parsed.path == "/api/upload-encrypted-stream-resume":
            if not self._authorized(parsed):
                return
            if not self._connected():
                return
            self._upload_encrypted_stream_resume(parsed)
            return
        if parsed.path == "/api/connect":
            if not self._authorized(parsed):
                return
            self._connect()
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def log_message(self, format, *args) -> None:
        return

    def _authorized(self, parsed) -> bool:
        query = parse_qs(parsed.query)
        code = query.get("code", [""])[0]
        if code != self.transfer_state.access_code:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid access code"})
            return False
        return True

    def _connected(self) -> bool:
        if self.transfer_state.is_client_active(self.client_address[0]):
            return True
        self._send_json(HTTPStatus.FORBIDDEN, {"error": "Connection expired"})
        return False

    def _upload(self, parsed) -> None:
        query = parse_qs(parsed.query)
        filename = unquote(query.get("filename", ["upload.bin"])[0])
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text else None
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return

        try:
            destination = self.transfer_state.reserve_upload(filename, length)
        except UploadTooLarge:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Upload too large"})
            return

        written = 0
        limit = self.transfer_state.max_upload_bytes
        with destination.open("wb") as output:
            remaining = length
            while remaining is None or remaining > 0:
                chunk_size = 64 * 1024 if remaining is None else min(64 * 1024, remaining)
                chunk = self.rfile.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    output.close()
                    destination.unlink(missing_ok=True)
                    self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Upload too large"})
                    return
                output.write(chunk)
                if remaining is not None:
                    remaining -= len(chunk)

        self.transfer_state.record_upload(destination, written)
        self._send_json(HTTPStatus.CREATED, {"name": destination.name, "size": written})

    def _set_text(self) -> None:
        try:
            payload = self._read_json_body(MAX_SHARED_TEXT_CHARS * 4 + 1024)
            text = payload["text"]
            if not isinstance(text, str):
                raise TypeError("Text must be a string")
            result = self.transfer_state.set_latest_text(text, "phone")
        except InvalidSharedText as exc:
            status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if "exceeds" in str(exc) else HTTPStatus.BAD_REQUEST
            self._send_json(status, {"error": str(exc)})
            return
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid text request"})
            return
        self._send_json(HTTPStatus.OK, result)

    def _resume_status(self) -> None:
        try:
            payload = self._read_json_body(8192)
            filename = str(payload["filename"])
            size = int(payload["size"])
            resume_id = str(payload["resumeId"])
            encrypted = bool(payload.get("encrypted"))
            offset = self.transfer_state.resume_offset(filename, size, resume_id, encrypted)
        except UploadTooLarge:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Upload too large"})
            return
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid resume request"})
            return
        self._send_json(HTTPStatus.OK, {"offset": offset, "size": size, "resumable": True})

    def _upload_resume(self, parsed) -> None:
        query = parse_qs(parsed.query)
        filename = unquote(query.get("filename", ["upload.bin"])[0])
        resume_id = query.get("resumeId", [""])[0]
        try:
            offset = int(query.get("offset", ["0"])[0])
            total_size = int(query.get("size", ["0"])[0])
            length = int(self.headers.get("Content-Length") or "0")
            if offset < 0 or total_size < 0 or length < 0 or offset + length > total_size:
                raise ValueError("Invalid resume range")
            part_path = self.transfer_state.prepare_resume_upload(filename, total_size, resume_id, False, offset)
        except UploadTooLarge:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Upload too large"})
            return
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid resume upload"})
            return

        written = 0
        mode = "ab" if offset else "wb"
        with part_path.open(mode) as output:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                output.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)

        current_size = offset + written
        if current_size > total_size:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid resume upload"})
            return
        if current_size == total_size:
            try:
                destination = self.transfer_state.finalize_resume_upload(filename, total_size, resume_id)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid resume upload"})
                return
            self._send_json(
                HTTPStatus.CREATED,
                {"name": destination.name, "size": total_size, "resumed": offset > 0, "sha256": sha256_file(destination)},
            )
            return
        self._send_json(HTTPStatus.OK, {"name": part_path.name, "size": current_size, "partial": True})

    def _upload_encrypted(self, parsed) -> None:
        query = parse_qs(parsed.query)
        filename = unquote(query.get("filename", ["upload.bin"])[0])
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text else 0
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return
        if length <= 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing encrypted payload"})
            return
        if length > self.transfer_state.max_upload_bytes * 2 + 4096:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Upload too large"})
            return

        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
            plaintext = decrypt_from_peer(self.transfer_state.encryption_private_key, payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid encrypted payload"})
            return

        try:
            destination = self.transfer_state.reserve_upload(filename, len(plaintext))
        except UploadTooLarge:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Upload too large"})
            return

        destination.write_bytes(plaintext)
        self.transfer_state.record_upload(destination, len(plaintext))
        self._send_json(HTTPStatus.CREATED, {"name": destination.name, "size": len(plaintext), "encrypted": True})

    def _upload_encrypted_stream(self, parsed) -> None:
        query = parse_qs(parsed.query)
        filename = unquote(query.get("filename", ["upload.bin"])[0])
        length_text = self.headers.get("Content-Length")
        try:
            remaining = int(length_text) if length_text else 0
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return
        if remaining <= 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing encrypted stream"})
            return

        try:
            header_line, remaining = self._read_body_line(remaining, 8192)
            header = json.loads(header_line.decode("utf-8"))
            if header.get("version") != STREAM_VERSION:
                raise ValueError("Unsupported stream version")
            original_size = int(header["size"])
            chunk_size = int(header["chunkSize"])
            if chunk_size <= 0:
                raise ValueError("Invalid chunk size")
            destination = self.transfer_state.reserve_upload(filename, original_size)
            stream_context = create_stream_receiver(
                self.transfer_state.encryption_private_key,
                str(header["ephemeralPublicKey"]),
                str(header["noncePrefix"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UploadTooLarge):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid encrypted stream header"})
            return

        written = 0
        expected_index = 0
        completed = False
        line_limit = chunk_size * 2 + 4096
        try:
            with destination.open("wb") as output:
                while remaining > 0:
                    line, remaining = self._read_body_line(remaining, line_limit)
                    frame = json.loads(line.decode("utf-8"))
                    if frame.get("final"):
                        completed = True
                        break
                    index = int(frame["index"])
                    if index != expected_index:
                        raise ValueError("Unexpected chunk index")
                    plaintext = decrypt_stream_chunk(stream_context, index, str(frame["ciphertext"]))
                    written += len(plaintext)
                    if written > original_size or written > self.transfer_state.max_upload_bytes:
                        raise UploadTooLarge()
                    output.write(plaintext)
                    expected_index += 1
            if not completed or written != original_size:
                raise ValueError("Incomplete encrypted stream")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UploadTooLarge):
            destination.unlink(missing_ok=True)
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid encrypted stream"})
            return

        self.transfer_state.record_upload(destination, written)
        self._send_json(HTTPStatus.CREATED, {"name": destination.name, "size": written, "encrypted": True, "streamed": True})

    def _upload_encrypted_stream_resume(self, parsed) -> None:
        query = parse_qs(parsed.query)
        filename = unquote(query.get("filename", ["upload.bin"])[0])
        length_text = self.headers.get("Content-Length")
        try:
            remaining = int(length_text) if length_text else 0
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
            return
        if remaining <= 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing encrypted stream"})
            return

        try:
            header_line, remaining = self._read_body_line(remaining, 8192)
            header = json.loads(header_line.decode("utf-8"))
            if header.get("version") != STREAM_VERSION:
                raise ValueError("Unsupported stream version")
            original_size = int(header["size"])
            chunk_size = int(header["chunkSize"])
            offset = int(header["offset"])
            resume_id = str(header["resumeId"])
            if chunk_size <= 0 or offset < 0 or offset > original_size:
                raise ValueError("Invalid stream range")
            part_path = self.transfer_state.prepare_resume_upload(filename, original_size, resume_id, True, offset)
            stream_context = create_stream_receiver(
                self.transfer_state.encryption_private_key,
                str(header["ephemeralPublicKey"]),
                str(header["noncePrefix"]),
            )
        except UploadTooLarge:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Upload too large"})
            return
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid encrypted stream header"})
            return

        written = offset
        expected_index = 0
        completed = False
        line_limit = chunk_size * 2 + 4096
        try:
            mode = "ab" if offset else "wb"
            with part_path.open(mode) as output:
                while remaining > 0:
                    line, remaining = self._read_body_line(remaining, line_limit)
                    frame = json.loads(line.decode("utf-8"))
                    if frame.get("final"):
                        completed = True
                        break
                    index = int(frame["index"])
                    if index != expected_index:
                        raise ValueError("Unexpected chunk index")
                    plaintext = decrypt_stream_chunk(stream_context, index, str(frame["ciphertext"]))
                    written += len(plaintext)
                    if written > original_size or written > self.transfer_state.max_upload_bytes:
                        raise UploadTooLarge()
                    output.write(plaintext)
                    expected_index += 1
            if not completed:
                self._send_json(HTTPStatus.OK, {"name": part_path.name, "size": part_path.stat().st_size, "partial": True})
                return
            if written != original_size:
                raise ValueError("Incomplete encrypted stream")
            destination = self.transfer_state.finalize_resume_upload(filename, original_size, resume_id)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, UploadTooLarge):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid encrypted stream"})
            return

        self._send_json(
            HTTPStatus.CREATED,
            {
                "name": destination.name,
                "size": original_size,
                "encrypted": True,
                "streamed": True,
                "resumed": offset > 0,
                "sha256": sha256_file(destination),
            },
        )

    def _read_body_line(self, remaining: int, limit: int) -> tuple[bytes, int]:
        to_read = min(remaining, limit + 1)
        line = self.rfile.readline(to_read)
        if not line or len(line) > limit or not line.endswith(b"\n"):
            raise ValueError("Invalid body line")
        return line[:-1], remaining - len(line)

    def _read_json_body(self, limit: int) -> dict:
        length_text = self.headers.get("Content-Length")
        length = int(length_text) if length_text else 0
        if length < 0 or length > limit:
            raise ValueError("Invalid JSON body length")
        body = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(body.decode("utf-8") or "{}")

    def _connect(self) -> None:
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text else 0
        except ValueError:
            length = 0
        body = self.rfile.read(min(length, 4096)) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        device = str(payload.get("device") or self.headers.get("User-Agent") or "Phone Browser")
        self.transfer_state.register_client(self.client_address[0], device)
        self._send_json(
            HTTPStatus.OK,
            {
                "status": "connected",
                "clients": self.transfer_state.connected_clients_json(),
                "encryptionPublicKey": self.transfer_state.encryption_public_key,
            },
        )

    def _download(self, file_id: str) -> None:
        shared = self.transfer_state.get_shared_file(file_id)
        if shared is None or not shared.path.exists():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "File not found"})
            return

        content_type = mimetypes.guess_type(shared.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(shared.size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(shared.name)}")
        self.end_headers()
        with shared.path.open("rb") as source:
            while True:
                chunk = source.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        self.transfer_state.add_event(f"Downloaded {shared.name}")

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        self._send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)


def _simple_qr_svg(text: str) -> str:
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="260" height="260" viewBox="0 0 260 260">
<rect width="260" height="260" fill="white"/>
<rect x="20" y="20" width="58" height="58" fill="#111"/>
<rect x="182" y="20" width="58" height="58" fill="#111"/>
<rect x="20" y="182" width="58" height="58" fill="#111"/>
<text x="130" y="125" text-anchor="middle" font-family="Arial" font-size="14" fill="#111">Open URL</text>
<text x="130" y="148" text-anchor="middle" font-family="Arial" font-size="11" fill="#333">{escaped}</text>
</svg>"""

