from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class MediaNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class MediaRecord:
    id: str
    kind: str
    created_at: datetime
    status: str
    content_path: Path
    metadata_path: Path
    thumbnail_path: Path
    partial_path: Path | None = None
    size_bytes: int | None = None
    duration_sec: float | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.kind,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
            "size_bytes": self.size_bytes,
            "duration_sec": self.duration_sec,
            "content_url": f"/api/v1/media/{self.id}/content",
            "thumbnail_url": f"/api/v1/media/{self.id}/thumbnail",
            "download_name": self.content_path.name,
        }


@dataclass(frozen=True)
class MediaPage:
    items: list[MediaRecord]
    next_cursor: str | None


class MediaStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def allocate(self, kind: str, now: datetime | None = None) -> MediaRecord:
        if kind not in {"snapshot", "recording"}:
            raise ValueError(f"unsupported media type: {kind}")
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        media_id = str(uuid.uuid4())
        short_id = media_id.split("-")[0]
        date = timestamp.strftime("%Y-%m-%d")
        stem = f"{kind}_{timestamp.strftime('%Y%m%dT%H%M%S')}_{short_id}"
        media_dir = self.root / ("snapshots" if kind == "snapshot" else "recordings") / date
        extension = ".jpg" if kind == "snapshot" else ".mp4"
        content = media_dir / f"{stem}{extension}"
        partial = media_dir / f"{stem}.partial.mkv" if kind == "recording" else None
        return MediaRecord(
            id=media_id,
            kind=kind,
            created_at=timestamp,
            status="recording" if kind == "recording" else "creating",
            content_path=content,
            partial_path=partial,
            metadata_path=self.root / "metadata" / date / f"{media_id}.json",
            thumbnail_path=self.root / "thumbnails" / date / f"{media_id}.jpg",
        )

    def save(self, record: MediaRecord) -> None:
        record.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": record.id,
            "type": record.kind,
            "created_at": record.created_at.isoformat(),
            "status": record.status,
            "relative_path": str(record.content_path.relative_to(self.root)),
            "relative_thumbnail_path": str(record.thumbnail_path.relative_to(self.root)),
            "relative_partial_path": str(record.partial_path.relative_to(self.root)) if record.partial_path else None,
            "size_bytes": record.size_bytes,
            "duration_sec": record.duration_sec,
        }
        temporary = record.metadata_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, record.metadata_path)

    def finalize(
        self,
        record: MediaRecord,
        *,
        size_bytes: int,
        duration_sec: float | None = None,
    ) -> MediaRecord:
        ready = replace(record, status="ready", size_bytes=size_bytes, duration_sec=duration_sec)
        self.save(ready)
        return ready

    def get(self, media_id: str) -> MediaRecord:
        try:
            parsed = str(uuid.UUID(media_id))
        except (ValueError, AttributeError) as exc:
            raise MediaNotFoundError(media_id) from exc
        matches = list((self.root / "metadata").glob(f"*/*{parsed}.json"))
        if not matches:
            raise MediaNotFoundError(media_id)
        return self._read(matches[0])

    def list_media(
        self,
        *,
        kind: str | None = None,
        date: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> MediaPage:
        if kind not in {None, "snapshot", "recording"}:
            raise ValueError("type must be snapshot or recording")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        metadata_root = self.root / "metadata"
        paths = metadata_root.glob(f"{date}/*.json") if date else metadata_root.glob("*/*.json")
        records = [self._read(path) for path in paths]
        records = [record for record in records if record.status == "ready" and (kind is None or record.kind == kind)]
        records.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        offset = self._decode_cursor(cursor)
        selected = records[offset : offset + limit]
        next_cursor = self._encode_cursor(offset + limit) if offset + limit < len(records) else None
        return MediaPage(selected, next_cursor)

    def interrupted(self) -> list[MediaRecord]:
        metadata_root = self.root / "metadata"
        if not metadata_root.exists():
            return []
        return [
            record
            for record in (self._read(path) for path in metadata_root.glob("*/*.json"))
            if record.status in {"recording", "interrupted"} and record.partial_path and record.partial_path.exists()
        ]

    def _read(self, path: Path) -> MediaRecord:
        payload = json.loads(path.read_text(encoding="utf-8"))
        content = self._safe_relative(payload["relative_path"])
        thumbnail = self._safe_relative(payload["relative_thumbnail_path"])
        partial_value = payload.get("relative_partial_path")
        partial = self._safe_relative(partial_value) if partial_value else None
        return MediaRecord(
            id=payload["id"],
            kind=payload["type"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            status=payload["status"],
            content_path=content,
            metadata_path=path,
            thumbnail_path=thumbnail,
            partial_path=partial,
            size_bytes=payload.get("size_bytes"),
            duration_sec=payload.get("duration_sec"),
        )

    def _safe_relative(self, value: str) -> Path:
        candidate = (self.root / value).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("metadata path escapes storage root")
        return candidate

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            padding = "=" * (-len(cursor) % 4)
            return max(0, int(base64.urlsafe_b64decode(cursor + padding).decode()))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid cursor") from exc
