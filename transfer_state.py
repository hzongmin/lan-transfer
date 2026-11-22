import json
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from encryption_utils import generate_keypair
from filename_utils import safe_relative_path, unique_destination
from shared_library import SharedLibrary


class UploadTooLarge(ValueError):
    pass


MAX_SHARED_TEXT_CHARS = 20_000


class InvalidSharedText(ValueError):
    pass


@dataclass(frozen=True)
class SharedFile:
    id: str
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size(self) -> int:
        return self.path.stat().st_size


@dataclass(frozen=True)
class ConnectedClient:
    ip: str
    device: str
    last_seen: float


@dataclass(frozen=True)
class SharedText:
    text: str = ""
    version: int = 0
    source: str = ""
    updated_at: float = 0.0

    def json(self) -> dict[str, object]:
        return {
            "text": self.text,
            "version": self.version,
            "source": self.source,
            "updatedAt": self.updated_at,
        }


class TransferState:
    def __init__(
        self,
        receive_dir: Path,
        max_upload_bytes: int = 512 * 1024 * 1024,
        event_callback: Optional[Callable[[str], None]] = None,
        client_timeout_seconds: int = 600,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.receive_dir = Path(receive_dir)
        self.max_upload_bytes = max_upload_bytes
        self.client_timeout_seconds = client_timeout_seconds
        self._clock = clock
        self.encryption_keypair = generate_keypair()
        self.encryption_private_key = self.encryption_keypair.private_key
        self.encryption_public_key = self.encryption_keypair.public_key_b64
        self.access_code = f"{secrets.randbelow(1_000_000):06d}"
        self.events: list[str] = []
        self._pending: dict[str, SharedFile] = {}
        self._shared: dict[str, SharedFile] = {}
        self._clients: dict[str, ConnectedClient] = {}
        self._latest_text = SharedText()
        self._lock = threading.RLock()
        self._event_callback = event_callback
        self.library = SharedLibrary()

    def refresh_access_code(self) -> str:
        with self._lock:
            # Select a different six-digit code without a retry loop.
            offset = secrets.randbelow(999_999) + 1
            self.access_code = f"{(int(self.access_code) + offset) % 1_000_000:06d}"
            self._clients.clear()
            self.add_event("Connection code refreshed; incoming devices must reconnect")
            return self.access_code

    def touch_client(self, ip: str) -> bool:
        with self._lock:
            self._drop_expired_clients()
            client = self._clients.get(ip)
            if client is None:
                return False
            self._clients[ip] = ConnectedClient(ip, client.device, self._clock())
            return True

    def set_receive_dir(self, receive_dir: Path) -> None:
        with self._lock:
            self.receive_dir = Path(receive_dir)
            self.receive_dir.mkdir(parents=True, exist_ok=True)
            self.add_event(f"Receive folder: {self.receive_dir}")

    def add_pending_file(self, path: Path) -> str:
        file_path = Path(path).resolve()
        if not file_path.is_file():
            raise FileNotFoundError(str(path))
        file_id = uuid4().hex
        with self._lock:
            self._pending[file_id] = SharedFile(file_id, file_path)
            self.add_event(f"Queued {file_path.name}")
        return file_id

    def add_shared_file(self, path: Path) -> str:
        file_id = self.add_pending_file(path)
        self.publish_pending_files()
        return file_id

    def publish_pending_files(self) -> None:
        with self._lock:
            if not self._pending:
                self.add_event("No files queued")
                return
            count = len(self._pending)
            self._shared.update(self._pending)
            self._pending.clear()
            self.add_event(f"Published {count} file(s) to phone")

    def clear_shared_files(self) -> None:
        with self._lock:
            self._pending.clear()
            self._shared.clear()
            self.add_event("Cleared send lists")

    def get_shared_file(self, file_id: str) -> Optional[SharedFile]:
        with self._lock:
            return self._shared.get(file_id)

    def shared_files_json(self) -> list[dict[str, object]]:
        with self._lock:
            return _files_json(self._shared)

    def pending_files_json(self) -> list[dict[str, object]]:
        with self._lock:
            return _files_json(self._pending)

    def reserve_upload(self, filename: str, size: Optional[int]) -> Path:
        if size is not None and size > self.max_upload_bytes:
            raise UploadTooLarge(f"Upload exceeds {self.max_upload_bytes} bytes")
        with self._lock:
            return unique_destination(self.receive_dir, filename)

    def resume_part_path(self, filename: str, resume_id: str) -> Path:
        return self._resume_path(filename, resume_id, ".part")

    def resume_meta_path(self, filename: str, resume_id: str) -> Path:
        return self._resume_path(filename, resume_id, ".json")

    def write_resume_metadata(self, filename: str, size: int, resume_id: str, encrypted: bool) -> None:
        if size > self.max_upload_bytes:
            raise UploadTooLarge(f"Upload exceeds {self.max_upload_bytes} bytes")
        metadata = {
            "filename": safe_relative_path(filename),
            "size": int(size),
            "resumeId": _safe_resume_id(resume_id),
            "encrypted": bool(encrypted),
        }
        meta_path = self.resume_meta_path(filename, resume_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")

    def resume_offset(self, filename: str, size: int, resume_id: str, encrypted: bool) -> int:
        if size > self.max_upload_bytes:
            raise UploadTooLarge(f"Upload exceeds {self.max_upload_bytes} bytes")
        part_path = self.resume_part_path(filename, resume_id)
        if not self._resume_metadata_matches(filename, size, resume_id, encrypted):
            self._delete_resume_files(filename, resume_id)
            return 0
        if not part_path.exists():
            return 0
        offset = part_path.stat().st_size
        if offset < 0 or offset > size:
            self._delete_resume_files(filename, resume_id)
            return 0
        return offset

    def prepare_resume_upload(self, filename: str, size: int, resume_id: str, encrypted: bool, offset: int) -> Path:
        if size > self.max_upload_bytes:
            raise UploadTooLarge(f"Upload exceeds {self.max_upload_bytes} bytes")
        part_path = self.resume_part_path(filename, resume_id)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        if offset == 0:
            if part_path.exists():
                part_path.unlink()
            self.write_resume_metadata(filename, size, resume_id, encrypted)
            return part_path
        if not self._resume_metadata_matches(filename, size, resume_id, encrypted):
            raise ValueError("Resume metadata does not match")
        if not part_path.exists() or part_path.stat().st_size != offset:
            raise ValueError("Resume offset does not match partial file")
        return part_path

    def finalize_resume_upload(self, filename: str, size: int, resume_id: str) -> Path:
        part_path = self.resume_part_path(filename, resume_id)
        if not part_path.exists() or part_path.stat().st_size != size:
            raise ValueError("Partial file is incomplete")
        destination = unique_destination(self.receive_dir, filename)
        part_path.replace(destination)
        meta_path = self.resume_meta_path(filename, resume_id)
        meta_path.unlink(missing_ok=True)
        self.record_upload(destination, size)
        return destination

    def record_upload(self, path: Path, size: int) -> None:
        self.add_event(f"Received {Path(path).name} ({size} bytes)")

    def register_client(self, ip: str, device: str) -> None:
        clean_device = (device or "Phone Browser").strip()[:80] or "Phone Browser"
        with self._lock:
            is_new = ip not in self._clients
            self._clients[ip] = ConnectedClient(ip=ip, device=clean_device, last_seen=self._clock())
        if is_new:
            self.add_event(f"Connected {clean_device} ({ip})")

    def is_client_active(self, ip: str) -> bool:
        with self._lock:
            self._drop_expired_clients()
            return ip in self._clients

    def connected_clients_json(self) -> list[dict[str, object]]:
        with self._lock:
            self._drop_expired_clients()
            return [
                {
                    "ip": item.ip,
                    "device": item.device,
                    "lastSeen": item.last_seen,
                }
                for item in sorted(self._clients.values(), key=lambda client: client.last_seen, reverse=True)
            ]

    def latest_text_json(self) -> dict[str, object]:
        with self._lock:
            return self._latest_text.json()

    def set_latest_text(self, text: str, source: str) -> dict[str, object]:
        normalized = str(text).strip()
        if not normalized:
            raise InvalidSharedText("Text is empty")
        if len(normalized) > MAX_SHARED_TEXT_CHARS:
            raise InvalidSharedText(f"Text exceeds {MAX_SHARED_TEXT_CHARS} characters")
        with self._lock:
            self._latest_text = SharedText(
                text=normalized,
                version=self._latest_text.version + 1,
                source=source if source in {"desktop", "phone"} else "device",
                updated_at=self._clock(),
            )
            payload = self._latest_text.json()
        self.add_event(f"Text updated from {payload['source']}")
        return payload

    def _drop_expired_clients(self) -> None:
        now = self._clock()
        expired = [
            ip
            for ip, client in self._clients.items()
            if now - client.last_seen > self.client_timeout_seconds
        ]
        for ip in expired:
            client = self._clients.pop(ip)
            self.add_event(f"Disconnected {client.device} ({ip}) after idle timeout")

    def add_event(self, message: str) -> None:
        with self._lock:
            self.events.append(message)
        if self._event_callback:
            self._event_callback(message)

    def _resume_path(self, filename: str, resume_id: str, suffix: str) -> Path:
        root = self.receive_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        relative = Path(safe_relative_path(filename))
        folder = (root / relative.parent).resolve()
        if root != folder and root not in folder.parents:
            raise ValueError("Resume path escaped receive directory")
        folder.mkdir(parents=True, exist_ok=True)
        name = relative.name
        clean_id = _safe_resume_id(resume_id)
        path = (folder / f".{name}.{clean_id}{suffix}").resolve()
        if root != path.parent and root not in path.parent.parents:
            raise ValueError("Resume path escaped receive directory")
        return path

    def _resume_metadata_matches(self, filename: str, size: int, resume_id: str, encrypted: bool) -> bool:
        meta_path = self.resume_meta_path(filename, resume_id)
        if not meta_path.exists():
            return False
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            metadata.get("filename") == safe_relative_path(filename)
            and metadata.get("size") == int(size)
            and metadata.get("resumeId") == _safe_resume_id(resume_id)
            and metadata.get("encrypted") is bool(encrypted)
        )

    def _delete_resume_files(self, filename: str, resume_id: str) -> None:
        self.resume_part_path(filename, resume_id).unlink(missing_ok=True)
        self.resume_meta_path(filename, resume_id).unlink(missing_ok=True)


def _files_json(files: dict[str, SharedFile]) -> list[dict[str, object]]:
    return [
        {"id": item.id, "name": item.name, "size": item.size}
        for item in files.values()
    ]


def _safe_resume_id(resume_id: str) -> str:
    cleaned = "".join(char for char in str(resume_id) if char.isalnum())[:80]
    if len(cleaned) < 12:
        raise ValueError("Invalid resume id")
    return cleaned

