from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig
from .media import MediaNotFoundError, MediaStore
from .runtime import FFmpegMediaRunner, StreamSupervisor
from .service import (
    AlreadyRecordingError,
    CameraHubService,
    InsufficientStorageError,
    NotRecordingError,
)

WEB_ROOT = Path(__file__).parent / "web"


def create_app(
    config: AppConfig,
    *,
    service: CameraHubService | None = None,
    manage_runtime: bool = True,
) -> FastAPI:
    store = MediaStore(config.storage.root)
    runner = FFmpegMediaRunner(config)
    supervisor = StreamSupervisor(config)
    hub = service or CameraHubService(config, store, runner, stream_supervisor=supervisor)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            if manage_runtime:
                await supervisor.start()
                await hub.recover_interrupted()
            yield
        finally:
            if manage_runtime:
                await runner.close()
                await supervisor.stop()

    app = FastAPI(title="MOMO Camera Hub", version="0.0.0", lifespan=lifespan)
    app.state.hub = hub

    @app.get("/api/v1/status")
    async def status() -> dict[str, Any]:
        return hub.status()

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
