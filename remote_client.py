import json
import hashlib
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from encryption_utils import STREAM_VERSION, create_stream_sender, encrypt_for_peer, encrypt_stream_chunk
from hash_utils import sha256_file


DEFAULT_ENCRYPTED_CHUNK_SIZE = 1024 * 1024
RESUME_FINGERPRINT_BYTES = 64 * 1024


class RemoteSendError(RuntimeError):
    pass


@dataclass(frozen=True)
class TransferItem:
    path: Path
    relative_path: str


def normalize_base_url(base_url: str) -> str:
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "http://" + cleaned
    return cleaned


def send_file_to_remote(
    base_url: str,
    access_code: str,
    file_path: Path,
    encrypted: bool = False,
    progress_callback=None,
    encrypted_chunk_size: int = DEFAULT_ENCRYPTED_CHUNK_SIZE,
    max_retries: int = 3,
    retry_delay_seconds: float = 0.5,
) -> None:
    send_files_to_remote(
        base_url,
        access_code,
        [file_path],
        encrypted=encrypted,
        progress_callback=progress_callback,
        encrypted_chunk_size=encrypted_chunk_size,
        max_retries=max_retries,
        retry_delay_seconds=retry_delay_seconds,
    )


def send_files_to_remote(
    base_url: str,
    access_code: str,
    file_paths,
    encrypted: bool = False,
    progress_callback=None,
    encrypted_chunk_size: int = DEFAULT_ENCRYPTED_CHUNK_SIZE,
    max_retries: int = 3,
    retry_delay_seconds: float = 0.5,
) -> None:
    base = normalize_base_url(base_url)
    items = collect_transfer_items(file_paths)
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    connection = connect_remote(base, access_code)
    sizes = [item.path.stat().st_size for item in items]
    total_bytes = sum(sizes)
    completed_bytes = 0

    def make_progress(index: int, item: TransferItem, file_size: int, initial_sent: int):
        file_sent = initial_sent

        def report(extra=None) -> None:
            if progress_callback:
                payload = {
                    "fileIndex": index + 1,
                    "fileCount": len(items),
                    "path": str(item.path),
                    "relativePath": item.relative_path,
                    "fileName": item.path.name,
                    "fileBytesSent": file_sent,
                    "fileSize": file_size,
                    "totalBytesSent": completed_bytes + file_sent,
                    "totalBytes": total_bytes,
                    "verified": False,
                }
                if extra:
                    payload.update(extra)
                progress_callback(payload)

        if initial_sent:
            report()

        def emit(delta: int) -> None:
            nonlocal file_sent
            file_sent += delta
            report()

        def verified(sha256: str) -> None:
            nonlocal file_sent
            file_sent = file_size
            report({"verified": True, "sha256": sha256})

        return emit, verified

    if encrypted:
        peer_public_key = connection.get("encryptionPublicKey")
        if not peer_public_key:
            raise RemoteSendError("Remote PC does not support encrypted transfer")
        for index, item in enumerate(items):
            completed_bytes += _send_item_with_retries(
                lambda offset, emit: _upload_encrypted_stream(
                    base,
                    access_code,
                    item,
                    str(peer_public_key),
                    encrypted_chunk_size,
                    emit,
                    build_resume_id(item.path, item.relative_path),
                    offset,
                ),
                base,
                access_code,
                item,
                sizes[index],
                True,
                index,
                make_progress,
                max_retries,
                retry_delay_seconds,
            )
        return
    for index, item in enumerate(items):
        completed_bytes += _send_item_with_retries(
            lambda offset, emit: _upload(
                base,
                access_code,
                item,
                emit,
                build_resume_id(item.path, item.relative_path),
                offset,
            ),
            base,
            access_code,
            item,
            sizes[index],
            False,
            index,
            make_progress,
            max_retries,
            retry_delay_seconds,
        )


def collect_transfer_items(file_paths) -> list[TransferItem]:
    items: list[TransferItem] = []
    for raw_path in file_paths:
        path = Path(raw_path)
        if path.is_file():
            items.append(TransferItem(path=path, relative_path=path.name))
            continue
        if path.is_dir():
            root_name = path.name
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    relative = Path(root_name) / child.relative_to(path)
                    items.append(TransferItem(path=child, relative_path=relative.as_posix()))
            continue
        raise FileNotFoundError(str(path))
    if not items:
        raise ValueError("No files selected")
    return items


def _send_item_with_retries(
    upload_callback,
    base_url: str,
    access_code: str,
    item: TransferItem,
    file_size: int,
    encrypted: bool,
    index: int,
    make_progress,
    max_retries: int,
    retry_delay_seconds: float,
) -> int:
    resume_id = build_resume_id(item.path, item.relative_path)
    last_error = None
    for attempt in range(max_retries):
        try:
            offset = _resume_status(base_url, access_code, item, resume_id, encrypted=encrypted)
            emit, verified = make_progress(index, item, file_size, offset)
            response = upload_callback(offset, emit)
            remote_hash = str(response.get("sha256") or "")
            verify_sha256(item.path, remote_hash)
            verified(remote_hash)
            return file_size
        except (RemoteSendError, urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt + 1 >= max_retries:
                break
            time.sleep(retry_delay_seconds)
    if isinstance(last_error, RemoteSendError):
        raise last_error
    raise RemoteSendError(str(last_error))


def connect_remote(base_url: str, access_code: str) -> dict:
    base = normalize_base_url(base_url)
    body = json.dumps({"device": "LanTransfer Desktop"}).encode("utf-8")
    url = f"{base}/api/connect?code={urllib.parse.quote(access_code)}"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    return _open_json_or_raise(request)


def build_resume_id(path: Path, relative_path: Optional[str] = None) -> str:
    file_path = Path(path)
    stat = file_path.stat()
    digest = hashlib.sha256()
    digest.update((relative_path or file_path.name).encode("utf-8", errors="surrogatepass"))
    digest.update(str(stat.st_size).encode("ascii"))
    digest.update(str(stat.st_mtime_ns).encode("ascii"))
    with file_path.open("rb") as source:
        digest.update(source.read(RESUME_FINGERPRINT_BYTES))
        if stat.st_size > RESUME_FINGERPRINT_BYTES:
            source.seek(max(0, stat.st_size - RESUME_FINGERPRINT_BYTES))
            digest.update(source.read(RESUME_FINGERPRINT_BYTES))
    return digest.hexdigest()


def verify_sha256(path: Path, expected_sha256: str) -> None:
    if not expected_sha256 or len(expected_sha256) != 64:
        raise RemoteSendError("Remote PC did not return a verification hash")
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        raise RemoteSendError("SHA256 verification failed")


def _resume_status(base_url: str, access_code: str, item: TransferItem, resume_id: str, encrypted: bool) -> int:
    body = json.dumps(
        {
            "filename": item.relative_path,
            "size": item.path.stat().st_size,
            "resumeId": resume_id,
            "encrypted": encrypted,
        }
    ).encode("utf-8")
    url = f"{base_url}/api/resume-status?code={urllib.parse.quote(access_code)}"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    payload = _open_json_or_raise(request)
    offset = int(payload.get("offset", 0))
    return max(0, min(offset, item.path.stat().st_size))


def _upload(base_url: str, access_code: str, item: TransferItem, progress_callback=None, resume_id: str = "", offset: int = 0) -> dict:
    filename = urllib.parse.quote(item.relative_path, safe="")
    code = urllib.parse.quote(access_code)
    resume = urllib.parse.quote(resume_id)
    size = item.path.stat().st_size
    offset = max(0, min(offset, size))
    url = f"{base_url}/api/upload-resume?code={code}&filename={filename}&resumeId={resume}&offset={offset}&size={size}"
    stream = ProgressFileReader(item.path, progress_callback, offset=offset)
    try:
        request = urllib.request.Request(
            url,
            data=stream,
            method="POST",
            headers={"Content-Length": str(size - offset)},
        )
        return _open_json_or_raise(request, expected=(201,))
    finally:
        stream.close()


def _upload_encrypted(base_url: str, access_code: str, filename_text: str, payload: dict) -> None:
    filename = urllib.parse.quote(filename_text)
    code = urllib.parse.quote(access_code)
    url = f"{base_url}/api/upload-encrypted?code={code}&filename={filename}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    _open_or_raise(request, expected=(201,))


def _upload_encrypted_stream(
    base_url: str,
    access_code: str,
    item: TransferItem,
    peer_public_key: str,
    chunk_size: int,
    progress_callback=None,
    resume_id: str = "",
    offset: int = 0,
) -> dict:
    if chunk_size <= 0:
        raise ValueError("Encrypted chunk size must be greater than zero")
    filename = urllib.parse.quote(item.relative_path, safe="")
    code = urllib.parse.quote(access_code)
    url = f"{base_url}/api/upload-encrypted-stream-resume?code={code}&filename={filename}"
    body = EncryptedStreamBody(item.path, peer_public_key, chunk_size, progress_callback, resume_id=resume_id, offset=offset)
    try:
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-ndjson",
                "Content-Length": str(body.content_length),
            },
        )
        return _open_json_or_raise(request, expected=(201,))
    finally:
        body.close()


class ProgressFileReader:
    def __init__(self, path: Path, progress_callback=None, offset: int = 0) -> None:
        self._stream = Path(path).open("rb")
        self._progress_callback = progress_callback
        self._stream.seek(offset)

    def read(self, size: int = -1) -> bytes:
        data = self._stream.read(size)
        if data and self._progress_callback:
            self._progress_callback(len(data))
        return data

    def close(self) -> None:
        self._stream.close()


class EncryptedStreamBody:
    def __init__(
        self,
        path: Path,
        peer_public_key: str,
        chunk_size: int,
        progress_callback=None,
        resume_id: str = "",
        offset: int = 0,
    ) -> None:
        self.path = Path(path)
        self.chunk_size = chunk_size
        self.file_size = self.path.stat().st_size
        self.offset = max(0, min(offset, self.file_size))
        self.resume_id = resume_id
        self.context = create_stream_sender(peer_public_key)
        self._stream = self.path.open("rb")
        self._stream.seek(self.offset)
        self._progress_callback = progress_callback
        self._chunk_index = 0
        self._finished = False
        self._buffer = bytearray(self._header_line())
        self.content_length = self._calculate_content_length()

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            parts = []
            while True:
                chunk = self.read(64 * 1024)
                if not chunk:
                    break
                parts.append(chunk)
            return b"".join(parts)
        while len(self._buffer) < size and not self._finished:
            self._fill_next_frame()
        if not self._buffer:
            return b""
        output = bytes(self._buffer[:size])
        del self._buffer[:size]
        return output

    def close(self) -> None:
        self._stream.close()

    def _fill_next_frame(self) -> None:
        plaintext = self._stream.read(self.chunk_size)
        if plaintext:
            ciphertext = encrypt_stream_chunk(self.context, self._chunk_index, plaintext)
            self._buffer.extend(_json_line({"index": self._chunk_index, "ciphertext": ciphertext}))
            self._chunk_index += 1
            if self._progress_callback:
                self._progress_callback(len(plaintext))
            return
        self._buffer.extend(_json_line({"final": True, "chunks": self._chunk_index}))
        self._finished = True

    def _header_line(self) -> bytes:
        return _json_line(
            {
                "version": STREAM_VERSION,
                "ephemeralPublicKey": self.context.ephemeral_public_key_b64,
                "noncePrefix": self.context.nonce_prefix_b64,
                "size": self.file_size,
                "chunkSize": self.chunk_size,
                "offset": self.offset,
                "resumeId": self.resume_id,
            }
        )

    def _calculate_content_length(self) -> int:
        total = len(self._header_line()) + len(_json_line({"final": True, "chunks": self._chunk_count()}))
        remaining = self.file_size - self.offset
        index = 0
        while remaining > 0:
            plaintext_size = min(self.chunk_size, remaining)
            ciphertext_b64_size = _b64_length(plaintext_size + 16)
            total += len(_json_line({"index": index, "ciphertext": "x" * ciphertext_b64_size}))
            remaining -= plaintext_size
            index += 1
        return total

    def _chunk_count(self) -> int:
        remaining = self.file_size - self.offset
        if remaining == 0:
            return 0
        return math.ceil(remaining / self.chunk_size)


def _json_line(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def _b64_length(byte_count: int) -> int:
    return 4 * math.ceil(byte_count / 3)


def _open_or_raise(request: urllib.request.Request, expected=(200,)) -> None:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in expected:
                raise RemoteSendError(f"Remote returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RemoteSendError(f"Remote returned HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise RemoteSendError(f"Could not reach remote PC: {exc.reason}") from exc


def _open_json_or_raise(request: urllib.request.Request, expected=(200,)) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            if response.status not in expected:
                raise RemoteSendError(f"Remote returned HTTP {response.status}")
            return json.loads(body.decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RemoteSendError(f"Remote returned HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise RemoteSendError(f"Could not reach remote PC: {exc.reason}") from exc
