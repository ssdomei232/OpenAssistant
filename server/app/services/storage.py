"""Object storage: local filesystem implementation (S3-compatible interface)."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from ..config import get_settings


class LocalStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or get_settings().storage_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, *, suffix: str = ".wav", prefix: str = "audio") -> str:
        digest = hashlib.sha256(data).hexdigest()
        rel = Path(prefix) / digest[:2] / digest[2:4] / f"{digest}{suffix}"
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        return rel.as_posix()

    def path(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError("invalid_storage_path")
        return path

    def read(self, rel: str) -> bytes:
        return self.path(rel).read_bytes()

    def exists(self, rel: str) -> bool:
        return self.path(rel).is_file()

    def delete(self, rel: str) -> None:
        path = self.path(rel)
        if path.is_file():
            path.unlink()

    def temp_copy(self, rel: str) -> str:
        path = self.path(rel)
        fd, tmp = tempfile.mkstemp(suffix=path.suffix)
        os.close(fd)
        shutil.copyfile(path, tmp)
        return tmp


_storage: LocalStorage | None = None


def get_storage() -> LocalStorage:
    global _storage
    if _storage is None:
        _storage = LocalStorage()
    return _storage


def set_storage(storage: LocalStorage | None) -> None:
    global _storage
    _storage = storage
