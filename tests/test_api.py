from pathlib import Path

from fastapi.testclient import TestClient

from momo_camera_hub.app import create_app
from momo_camera_hub.cameras import CameraDevice, CameraSelectionStore
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


def test_camera_can_be_selected_and_persisted(tmp_path: Path) -> None:
    config = AppConfig.default(platform_name="Darwin", home=tmp_path)
    config.storage.root = tmp_path / "media"
    config.storage.minimum_free_gib = 0
    selection_store = CameraSelectionStore(tmp_path / "selection.json")
    devices = [
        CameraDevice("opencv:0", "MacBook Air相机", "opencv", "MacBook Air相机", 0),
        CameraDevice("opencv:1", "OsmoPocket3", "opencv", "OsmoPocket3", 1),
    ]
    service = CameraHubService(config, MediaStore(config.storage.root), FakeRunner())
    app = create_app(
        config,
        service=service,
        manage_runtime=False,
        camera_discovery=lambda: devices,
        selection_store=selection_store,
    )

    with TestClient(app) as client:
        listed = client.get("/api/v1/cameras")
        selected = client.put("/api/v1/camera", json={"id": "opencv:1"})
        status = client.get("/api/v1/status")

    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["items"]] == ["MacBook Air相机", "OsmoPocket3"]
    assert selected.status_code == 200
    assert selected.json()["name"] == "OsmoPocket3"
    assert status.json()["camera"]["device"] == "OsmoPocket3"
    assert selection_store.load() == {"backend": "opencv", "device": "OsmoPocket3", "index": 1}


def test_camera_switch_is_rejected_while_recording(tmp_path: Path) -> None:
    config = AppConfig.default(platform_name="Darwin", home=tmp_path)
    config.storage.root = tmp_path / "media"
    config.storage.minimum_free_gib = 0
    devices = [CameraDevice("opencv:1", "OsmoPocket3", "opencv", "OsmoPocket3", 1)]
    service = CameraHubService(config, MediaStore(config.storage.root), FakeRunner())
    app = create_app(
        config,
        service=service,
        manage_runtime=False,
        camera_discovery=lambda: devices,
    )

    with TestClient(app) as client:
        assert client.post("/api/v1/recordings/start").status_code == 201
        response = client.put("/api/v1/camera", json={"id": "opencv:1"})
        client.post("/api/v1/recordings/stop")

    assert response.status_code == 409
