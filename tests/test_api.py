from pathlib import Path

from fastapi.testclient import TestClient

from momo_camera_hub.app import VisionProxyError, create_app
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
        detail = client.get(f"/api/v1/media/{media_id}")
        content = client.get(f"/api/v1/media/{media_id}/content")

    assert snapshot.status_code == 201
    assert media.json()["items"][0]["id"] == media_id
    assert detail.status_code == 200
    assert detail.json()["id"] == media_id
    assert content.content == b"jpeg"


def test_static_app_is_served(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "MOMO Camera Hub" in response.text


def test_camera_hub_proxies_only_fixed_vision_routes(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str, str, dict | None]] = []

    def fake_request(base_url: str, path: str, *, method: str, payload: dict | None):
        calls.append((base_url, path, method, payload))
        return {"path": path, "method": method, "payload": payload}

    monkeypatch.setattr("momo_camera_hub.app._request_vision_json", fake_request)
    with make_client(tmp_path) as client:
        responses = [
            client.get("/api/v1/vision/health"),
            client.get("/api/v1/vision/latest"),
            client.get("/api/v1/vision/status"),
            client.get("/api/v1/vision/target/state"),
            client.post("/api/v1/vision/target/select", json={"x": 10, "y": 20, "w": 80, "h": 90}),
            client.post("/api/v1/vision/target/reset"),
        ]

    assert all(response.status_code == 200 for response in responses)
    assert {call[0] for call in calls} == {"http://127.0.0.1:8000"}
    assert [call[1] for call in calls] == [
        "/health",
        "/latest",
        "/status",
        "/target/state",
        "/target/select",
        "/target/reset",
    ]
    assert calls[-2][2:] == ("POST", {"x": 10, "y": 20, "w": 80, "h": 90})
    assert calls[-1][2:] == ("POST", {})


def test_vision_proxy_failure_returns_clear_503(tmp_path: Path, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise VisionProxyError("连接被拒绝")

    monkeypatch.setattr("momo_camera_hub.app._request_vision_json", fail)
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/vision/latest")

    assert response.status_code == 503
    assert response.json()["detail"] == "视觉服务不可用：连接被拒绝"


def test_invalid_vision_base_url_is_rejected_at_app_creation(tmp_path: Path) -> None:
    config = AppConfig.default(platform_name="Darwin", home=tmp_path)
    config.storage.root = tmp_path / "media"
    config.vision.base_url = "file:///tmp/vision.json"
    service = CameraHubService(config, MediaStore(config.storage.root), FakeRunner())

    try:
        create_app(config, service=service, manage_runtime=False)
    except ValueError as exc:
        assert str(exc) == "vision base URL must be an absolute HTTP(S) URL"
    else:
        raise AssertionError("invalid vision base URL was accepted")


def test_vision_target_selection_is_validated_before_proxy(tmp_path: Path, monkeypatch) -> None:
    called = False

    def fake_request(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("momo_camera_hub.app._request_vision_json", fake_request)
    with make_client(tmp_path) as client:
        negative = client.post("/api/v1/vision/target/select", json={"x": -1, "y": 20, "w": 80, "h": 90})
        empty = client.post("/api/v1/vision/target/select", json={"x": 1, "y": 20, "w": 0, "h": 90})
        arbitrary = client.get("/api/v1/vision", params={"url": "http://example.com"})

    assert negative.status_code == 422
    assert empty.status_code == 422
    assert arbitrary.status_code == 404
    assert called is False


def test_web_ui_uses_webrtc_with_json_vision_overlay(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        index = client.get("/")
        script = client.get("/app.js")

    assert "visionOverlay" in index.text
    assert "http://localhost:8010/web/" in index.text
    assert "status?.stream?.webrtc_port" in script.text
    assert 'status?.stream?.path || "armcam"' in script.text
    assert "state.liveFrameAddress === address" in script.text
    assert "/api/v1/vision/latest" in script.text
    assert "/api/v1/vision/target/select" in script.text
    assert "if (result.ok === false)" in script.text
    assert "victorySnapshotTelemetry" in index.text
    assert "latest?.victory_snapshot" in script.text
    assert "latest?.gesture?.snapshot" in script.text
    assert "state.visionRuntime?.victory_snapshot" in script.text
    assert "renderVictorySnapshot(null)" in script.text
    assert "cooldown_remaining_sec" in script.text
    assert "last_snapshot" in script.text
    assert "frame.jpg" not in script.text


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
