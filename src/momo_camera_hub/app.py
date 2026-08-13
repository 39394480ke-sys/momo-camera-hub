from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .cameras import CameraDevice, CameraSelectionStore, discover_cameras, resolve_configured_camera
from .config import AppConfig
from .media import MediaNotFoundError, MediaStore
from .runtime import FFmpegMediaRunner, StreamSupervisor
from .service import (
    AlreadyRecordingError,
    CameraBusyError,
    CameraHubService,
    InsufficientStorageError,
    NotRecordingError,
)

WEB_ROOT = Path(__file__).parent / "web"


class CameraSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=256)


class VisionTargetSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1)
    h: int = Field(ge=1)


class VisionProxyError(RuntimeError):
    """Raised when Camera Hub cannot obtain a valid response from vision."""


def create_app(
    config: AppConfig,
    *,
    service: CameraHubService | None = None,
    manage_runtime: bool = True,
    camera_discovery: Callable[[], list[CameraDevice]] | None = None,
    selection_store: CameraSelectionStore | None = None,
) -> FastAPI:
    selection_store = selection_store or CameraSelectionStore(config.storage.root / ".camera-selection.json")
    selection_store.apply(config.camera)
    camera_discovery = camera_discovery or (
        lambda: discover_cameras(config.camera, ffmpeg_binary=config.ffmpeg_binary)
    )
    store = MediaStore(config.storage.root)
    runner = FFmpegMediaRunner(config)
    supervisor = StreamSupervisor(config)
    hub = service or CameraHubService(config, store, runner, stream_supervisor=supervisor)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            if manage_runtime:
                devices = await asyncio.to_thread(camera_discovery)
                resolve_configured_camera(config.camera, devices)
                await supervisor.start()
                await hub.recover_interrupted()
            yield
        finally:
            if manage_runtime:
                await runner.close()
                await supervisor.stop()

    app = FastAPI(title="MOMO Camera Hub", version="0.0.0", lifespan=lifespan)
    app.state.hub = hub
    vision_config = getattr(config, "vision", None)
    vision_base_url = _validated_vision_base_url(
        str(getattr(vision_config, "base_url", "http://127.0.0.1:8000"))
    )

    async def vision_json(
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                _request_vision_json,
                vision_base_url,
                path,
                method=method,
                payload=payload,
            )
        except VisionProxyError as exc:
            raise HTTPException(status_code=503, detail=f"视觉服务不可用：{exc}") from exc

    @app.get("/api/v1/status")
    async def status() -> dict[str, Any]:
        return hub.status()

    @app.get("/api/v1/cameras")
    async def camera_list() -> dict[str, Any]:
        try:
            devices = await asyncio.to_thread(camera_discovery)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"could not list cameras: {exc}") from exc
        selected = resolve_configured_camera(config.camera, devices)
        return {
            "items": [item.public_dict(selected=item.id == selected.id if selected else False) for item in devices],
            "selected_id": selected.id if selected else None,
        }

    @app.put("/api/v1/camera")
    async def camera_select(request: CameraSelectionRequest) -> dict[str, Any]:
        try:
            devices = await asyncio.to_thread(camera_discovery)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"could not list cameras: {exc}") from exc
        selected = next((item for item in devices if item.id == request.id), None)
        if selected is None:
            raise HTTPException(status_code=404, detail="camera is no longer available")
        try:
            await hub.select_camera(selected, selection_store=selection_store)
        except CameraBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return selected.public_dict(selected=True)

    @app.get("/api/v1/vision/health")
    async def vision_health() -> dict[str, Any]:
        return await vision_json("/health")

    @app.get("/api/v1/vision/latest")
    async def vision_latest() -> dict[str, Any]:
        return await vision_json("/latest")

    @app.get("/api/v1/vision/status")
    async def vision_status() -> dict[str, Any]:
        return await vision_json("/status")

    @app.get("/api/v1/vision/target/state")
    async def vision_target_state() -> dict[str, Any]:
        return await vision_json("/target/state")

    @app.post("/api/v1/vision/target/select")
    async def vision_target_select(request: VisionTargetSelectRequest) -> dict[str, Any]:
        return await vision_json("/target/select", method="POST", payload=request.model_dump())

    @app.post("/api/v1/vision/target/reset")
    async def vision_target_reset() -> dict[str, Any]:
        return await vision_json("/target/reset", method="POST", payload={})

    @app.post("/api/v1/snapshots", status_code=201)
    async def snapshot() -> dict[str, Any]:
        try:
            return (await hub.create_snapshot()).public_dict()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/v1/recordings/start", status_code=201)
    async def recording_start() -> dict[str, Any]:
        try:
            return (await hub.start_recording()).public_dict()
        except AlreadyRecordingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InsufficientStorageError as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/v1/recordings/stop")
    async def recording_stop() -> dict[str, Any]:
        try:
            return (await hub.stop_recording()).public_dict()
        except NotRecordingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/v1/media")
    async def media_list(
        type: str | None = Query(None, pattern="^(snapshot|recording)$"),
        date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
    ) -> dict[str, Any]:
        try:
            page = hub.store.list_media(kind=type, date=date, cursor=cursor, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": [item.public_dict() for item in page.items], "next_cursor": page.next_cursor}

    @app.get("/api/v1/media/{media_id}")
    async def media_detail(media_id: str) -> dict[str, Any]:
        return _get_ready_media(hub.store, media_id).public_dict()

    @app.get("/api/v1/media/{media_id}/content")
    async def media_content(media_id: str, download: bool = False) -> FileResponse:
        record = _get_ready_media(hub.store, media_id)
        media_type = "image/jpeg" if record.kind == "snapshot" else "video/mp4"
        return FileResponse(
            record.content_path,
            media_type=media_type,
            filename=record.content_path.name if download else None,
            content_disposition_type="attachment" if download else "inline",
        )

    @app.get("/api/v1/media/{media_id}/thumbnail")
    async def media_thumbnail(media_id: str) -> FileResponse:
        record = _get_ready_media(hub.store, media_id)
        source = record.thumbnail_path if record.thumbnail_path.exists() else record.content_path
        return FileResponse(source, media_type="image/jpeg")

    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
    return app


def _get_ready_media(store: MediaStore, media_id: str):
    try:
        record = store.get(media_id)
    except MediaNotFoundError as exc:
        raise HTTPException(status_code=404, detail="media not found") from exc
    if record.status != "ready" or not record.content_path.exists():
        raise HTTPException(status_code=404, detail="media not ready")
    return record


def _request_vision_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 1.5,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = _vision_error_detail(exc.read())
        suffix = f"：{detail}" if detail else ""
        raise VisionProxyError(f"上游返回 HTTP {exc.code}{suffix}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise VisionProxyError(str(reason)) from exc

    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VisionProxyError("上游返回了无效 JSON") from exc
    if not isinstance(result, dict):
        raise VisionProxyError("上游返回值不是 JSON 对象")
    return result


def _validated_vision_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("vision base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("vision base URL cannot contain credentials, a query, or a fragment")
    return base_url.rstrip("/")


def _vision_error_detail(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("detail") or payload.get("message") or "")
