from __future__ import annotations

import asyncio
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .config import AppConfig
from .media import MediaRecord, MediaStore


class AlreadyRecordingError(RuntimeError):
    pass


class NotRecordingError(RuntimeError):
    pass


class InsufficientStorageError(RuntimeError):
    pass


class MediaRunner(Protocol):
    async def snapshot(self, output: Path) -> None: ...

    async def start_recording(self, output: Path) -> None: ...

    async def stop_recording(self, partial: Path, final: Path) -> float: ...

    async def recover(self, partial: Path, final: Path) -> float: ...

    async def thumbnail(self, source: Path, output: Path) -> None: ...


class CameraHubService:
    def __init__(
        self,
        config: AppConfig,
        store: MediaStore,
        runner: MediaRunner,
        *,
        stream_supervisor: Any | None = None,
    ):
        self.config = config
        self.store = store
        self.runner = runner
        self.stream_supervisor = stream_supervisor
        self._active_recording: MediaRecord | None = None
        self._lock = asyncio.Lock()

    async def create_snapshot(self) -> MediaRecord:
        self._ensure_storage(allow_low_space=True)
        record = self.store.allocate("snapshot")
        await self.runner.snapshot(record.content_path)
        return self.store.finalize(record, size_bytes=record.content_path.stat().st_size)

    async def start_recording(self) -> MediaRecord:
        async with self._lock:
            if self._active_recording is not None:
                raise AlreadyRecordingError("a recording is already active")
            self._ensure_storage()
            record = self.store.allocate("recording")
            if record.partial_path is None:
                raise RuntimeError("recording allocation has no partial path")
            self.store.save(record)
            try:
                await self.runner.start_recording(record.partial_path)
            except Exception:
                failed = replace(record, status="failed")
                self.store.save(failed)
                raise
            self._active_recording = record
            return record

    async def stop_recording(self) -> MediaRecord:
        async with self._lock:
            record = self._active_recording
            if record is None:
                raise NotRecordingError("no recording is active")
            if record.partial_path is None:
                raise RuntimeError("active recording has no partial path")
            try:
                duration = await self.runner.stop_recording(record.partial_path, record.content_path)
                await self.runner.thumbnail(record.content_path, record.thumbnail_path)
                ready = self.store.finalize(
                    record,
                    size_bytes=record.content_path.stat().st_size,
                    duration_sec=duration,
                )
            except Exception:
                self.store.save(replace(record, status="interrupted"))
                raise
            finally:
                self._active_recording = None
            return ready

    async def recover_interrupted(self) -> list[MediaRecord]:
        recovered: list[MediaRecord] = []
        for record in self.store.interrupted():
            if record.partial_path is None:
                continue
            try:
                duration = await self.runner.recover(record.partial_path, record.content_path)
                await self.runner.thumbnail(record.content_path, record.thumbnail_path)
                recovered.append(
                    self.store.finalize(
                        record,
                        size_bytes=record.content_path.stat().st_size,
                        duration_sec=duration,
                    )
                )
            except Exception:
                self.store.save(replace(record, status="interrupted"))
        return recovered

    def recording_status(self) -> MediaRecord | None:
        return self._active_recording

    def status(self) -> dict[str, Any]:
        disk = shutil.disk_usage(self.config.storage.root)
        runtime = self.stream_supervisor.status() if self.stream_supervisor else {"running": True, "last_error": None}
        actual_stream = runtime.get("actual_stream") or {}
        active = self._active_recording
        now = datetime.now(UTC)
        return {
            "camera": {
                "device": self.config.camera.device,
                "backend": self.config.camera.backend,
                "width": actual_stream.get("width", self.config.camera.width),
                "height": actual_stream.get("height", self.config.camera.height),
                "fps": actual_stream.get("fps", self.config.camera.fps),
                "rotation": self.config.camera.rotation,
                "online": bool(runtime.get("running")),
                "last_error": runtime.get("last_error"),
                "capture_restarts": runtime.get("capture_restarts", 0),
            },
            "stream": {
                "path": self.config.stream.path,
                "webrtc_port": self.config.stream.webrtc_port,
                "hls_port": self.config.stream.hls_port,
                "rtsp_url": self.config.stream.rtsp_url,
                "viewer_count": runtime.get("viewer_count"),
            },
            "recording": {
                "active": active is not None,
                "id": active.id if active else None,
                "started_at": active.created_at.isoformat() if active else None,
                "elapsed_sec": (now - active.created_at).total_seconds() if active else 0,
            },
            "storage": {
                "root": str(self.config.storage.root),
                "free_bytes": disk.free,
                "total_bytes": disk.total,
                "minimum_free_gib": self.config.storage.minimum_free_gib,
            },
        }

    def _ensure_storage(self, *, allow_low_space: bool = False) -> None:
        self.config.storage.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.config.storage.root).free
        minimum = int(self.config.storage.minimum_free_gib * 1024**3)
        if not allow_low_space and free < minimum:
            raise InsufficientStorageError(
                f"recording requires at least {self.config.storage.minimum_free_gib:g} GiB free"
            )
