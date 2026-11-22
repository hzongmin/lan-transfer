"""Read-only directory shares, separate from the V4 transfer queue."""
import json
import mimetypes
import threading
from pathlib import Path, PureWindowsPath
from uuid import uuid4


def preview_kind(path):
    suffix = path.suffix.lower()
    if suffix in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.avif'}:
        return 'image'
    if suffix in {'.mp4', '.m4v', '.webm', '.mov', '.mkv', '.avi', '.ts'}:
        return 'video'
    if suffix in {'.mp3', '.m4a', '.aac', '.wav', '.ogg', '.flac'}:
        return 'audio'
    return 'download'


def media_type(path):
    overrides = {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.webm': 'video/webm',
                 '.mkv': 'video/x-matroska', '.m4a': 'audio/mp4', '.ts': 'video/mp2t'}
    return overrides.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'


class SharedLibrary:
    def __init__(self, config_path=None):
        self._lock = threading.RLock()
        self._roots = {}
        self.config_path = Path(config_path) if config_path else None
        self.load_error = ''
        if self.config_path and self.config_path.exists():
            try:
                paths = json.loads(self.config_path.read_text(encoding='utf-8'))
                if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                    raise ValueError('Invalid share configuration')
                for path in paths:
                    self._roots[uuid4().hex] = Path(path).resolve()
            except (OSError, ValueError, RuntimeError) as exc:
                self.load_error = str(exc)

    def _save(self):
        if self.config_path:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.config_path.with_suffix('.tmp')
            temporary.write_text(json.dumps([str(p) for p in self._roots.values()], ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.config_path)

    def add(self, path):
        root = Path(path).resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        with self._lock:
            for key, existing in self._roots.items():
                if existing == root:
                    return key
            key = uuid4().hex
            self._roots[key] = root
            try:
                self._save()
            except OSError:
                del self._roots[key]
                raise
            return key

    def remove(self, key):
        with self._lock:
            root = self._roots.pop(key)
            try:
                self._save()
            except OSError:
                self._roots[key] = root
                raise

    def roots(self, local=False):
        with self._lock:
            result = []
            for key, path in self._roots.items():
                item = {'id': key, 'name': path.name or str(path), 'available': path.is_dir()}
                if local:
                    item['localPath'] = str(path)
                result.append(item)
            return result

    def resolve(self, key, relative=''):
        # Reject Windows drive/UNC paths, ADS, traversal and ambiguous separators.
        if '\\' in relative or ':' in relative or '\x00' in relative:
            raise PermissionError('Invalid path')
        if PureWindowsPath(relative).drive or relative.startswith('/'):
            raise PermissionError('Absolute path is forbidden')
        if any(part in {'.', '..'} for part in relative.split('/')):
            raise PermissionError('Traversal is forbidden')
        with self._lock:
            root = self._roots.get(key)
            if root is None:
                raise FileNotFoundError('Share removed')
            current_root = root.resolve(strict=True)
            if current_root != root:
                raise PermissionError('Share target changed')
            target = (root / relative).resolve(strict=True)
            try:
                target.relative_to(root)
            except ValueError:
                raise PermissionError('Outside shared folder')
            return target

    def entries(self, key, relative=''):
        directory = self.resolve(key, relative)
        if not directory.is_dir():
            raise NotADirectoryError('Not a folder')
        entries = []
        for child in directory.iterdir():
            child_relative = '/'.join(filter(None, [relative, child.name]))
            try:
                target = self.resolve(key, child_relative)
                is_dir = target.is_dir()
                if not is_dir and not target.is_file():
                    continue
                stat = target.stat()
                entries.append({'name': child.name, 'path': child_relative,
                                'directory': is_dir, 'size': 0 if is_dir else stat.st_size,
                                'kind': 'folder' if is_dir else preview_kind(target)})
            except (OSError, ValueError, RuntimeError):
                continue
        entries.sort(key=lambda item: (not item['directory'], item['name'].casefold()))
        return entries
