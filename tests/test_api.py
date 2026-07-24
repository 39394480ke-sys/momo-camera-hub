from pathlib import Path

from fastapi.testclient import TestClient

from momo_camera_hub.app import create_app
from momo_camera_hub.config import AppConfig
from momo_camera_hub.media import MediaStore
from momo_camera_hub.service import CameraHubService

from .test_service import FakeRunner


def make_client(tmp_path: Path) -> TestClient:
    config = AppConfig.default(platform_name="Darwin", home=tmp_path)
    config.storage.root = tmp_path / "media"
    config.storage.minimum_free_gib = 0
    service = CameraHubService(config, MediaStore(config.storage.root), FakeRunner())
    return TestClient(create_app(config, service=service, manage_runtime=False))


def test_status_exposes_public_stream_and_storage(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/status")

    assert response.status_code == 200
    body = response.json()
    assert body["camera"]["device"] == "OsmoPocket3"
    assert body["stream"]["path"] == "armcam"
    assert body["storage"]["minimum_free_gib"] == 0


def test_recording_conflicts_return_409(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        first = client.post("/api/v1/recordings/start")
        second = client.post("/api/v1/recordings/start")
        stopped = client.post("/api/v1/recordings/stop")
        missing = client.post("/api/v1/recordings/stop")

    assert first.status_code == 201
    assert second.status_code == 409
    assert stopped.status_code == 200
    assert missing.status_code == 409


def test_snapshot_content_and_media_listing(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        snapshot = client.post("/api/v1/snapshots")
        media = client.get("/api/v1/media", params={"type": "snapshot"})
        media_id = snapshot.json()["id"]
        content = client.get(f"/api/v1/media/{media_id}/content")

    assert snapshot.status_code == 201
    assert media.json()["items"][0]["id"] == media_id
    assert content.content == b"jpeg"


def test_static_app_is_served(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "MOMO Camera Hub" in response.text
