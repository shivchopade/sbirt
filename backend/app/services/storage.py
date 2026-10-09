"""Upload storage outside any served folder: UUID file names, SHA-256 recorded, quarantine for dangerous files."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.core.config import Settings


class Storage:
    def __init__(self, settings: Settings):
        self.uploads = settings.uploads_dir
        self.quarantine_dir = settings.quarantine_dir
        self.previews = settings.previews_dir
        for path in (self.uploads, self.quarantine_dir, self.previews):
            path.mkdir(parents=True, exist_ok=True)

    def save_upload(self, data: bytes, kind: str) -> Path:
        path = self.uploads / f"{uuid.uuid4().hex}.{kind}"
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def read(self, stored: str) -> bytes:
        return Path(stored).read_bytes()

    def quarantine(self, stored: str) -> str:
        src = Path(stored)
        if not src.exists() or src.parent == self.quarantine_dir:
            return stored
        dst = self.quarantine_dir / src.name
        shutil.move(str(src), dst)
        return str(dst)

    def preview_path(self, resume_id: str, page: int) -> Path:
        folder = self.previews / resume_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"page-{page}.png"

    def clear_previews(self, resume_id: str) -> None:
        shutil.rmtree(self.previews / resume_id, ignore_errors=True)
