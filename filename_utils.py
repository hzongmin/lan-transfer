from pathlib import Path


_RESERVED_CHARS = '<>:"\\|?*\x00'


def safe_filename(name: str, default: str = "upload.bin") -> str:
    raw = (name or "").replace("\\", "/").split("/")[-1].strip()
    cleaned = "".join("_" if char in _RESERVED_CHARS else char for char in raw)
    cleaned = cleaned.strip(" .")
    return cleaned or default


def safe_relative_path(name: str, default: str = "upload.bin") -> str:
    parts = []
    for raw_part in (name or "").replace("\\", "/").split("/"):
        part = safe_filename(raw_part, default="")
        if part and part not in {".", ".."}:
            parts.append(part)
    return "/".join(parts) or default


def unique_destination(receive_dir: Path, original_name: str) -> Path:
    root = receive_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)

    filename = safe_relative_path(original_name)
    relative = Path(filename)
    base = relative.stem
    suffix = relative.suffix
    parent = (root / relative.parent).resolve()
    if root != parent and root not in parent.parents:
        raise ValueError("Destination escaped receive directory")
    parent.mkdir(parents=True, exist_ok=True)

    counter = 1
    while True:
        candidate_name = filename if counter == 1 else f"{base} ({counter}){suffix}"
        candidate = (root / candidate_name).resolve() if counter == 1 else (parent / candidate_name).resolve()
        if root != candidate.parent and root not in candidate.parent.parents:
            raise ValueError("Destination escaped receive directory")
        if not candidate.exists():
            return candidate
        counter += 1
