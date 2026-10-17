"""Armazenamento de arquivos enviados. Local em disco; troque por S3/GCS mantendo a interface."""

from __future__ import annotations

import hashlib
from pathlib import Path

from finance_engine.core.config import get_settings


class LocalStorage:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or get_settings().storage_dir)

    def save(self, content: bytes, namespace: str) -> str:
        digest = hashlib.sha256(content).hexdigest()
        key = f"{namespace}/{digest[:2]}/{digest}"
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return key

    def read(self, key: str) -> bytes:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("chave de arquivo inválida")
        return path.read_bytes()
