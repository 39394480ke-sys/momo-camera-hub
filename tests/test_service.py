from dataclasses import replace
from pathlib import Path

import pytest

from momo_camera_hub.config import AppConfig
from momo_camera_hub.media import MediaStore
from momo_camera_hub.service import AlreadyRecordingError, CameraHubService, NotRecordingError


class FakeRunner:
    def __init__(self) -> None:
        self.recording = False
        self.calls: list[tuple[str, object]] = []

    async def snapshot(self, output: Path) -> None:
        self.calls.append(("snapshot", output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"jpeg")

    async def start_recording(self, output: Path) -> None:
        self.calls.append(("start", output))
        self.recording = True
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"partial")

    async def stop_recording(self, partial: Path, final: Path) -> float:
        self.calls.append(("stop", partial))
        self.recording = False
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"mp4")
        partial.unlink(missing_ok=True)
        return 8.25

    async def recover(self, partial: Path, final: Path) -> float:
        self.calls.append(("recover", partial))
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"recovered")
        partial.unlink(missing_ok=True)
        return 4.5

    async def thumbnail(self, source: Path, output: Path) -> None:
        self.calls.append(("thumbnail", source))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"thumbnail")


@pytest.fixture
def service(tmp_path: Path) -> CameraHubService:
    config = AppConfig.default(platform_name="Darwin", home=tmp_path)
    config.storage.root = tmp_path / "media"
    config.storage.minimum_free_gib = 0
    return CameraHubService(config, MediaStore(config.storage.root), FakeRunner())


@pytest.mark.asyncio
async def test_snapshot_is_available_during_recording(service: CameraHubService) -> None:
    recording = await service.start_recording()
    snapshot = await service.create_snapshot()

    assert recording.status == "recording"
    assert snapshot.status == "ready"


@pytest.mark.asyncio
async def test_duplicate_start_is_rejected(service: CameraHubService) -> None:
    await service.start_recording()

    with pytest.raises(AlreadyRecordingError):
        await service.start_recording()


@pytest.mark.asyncio
async def test_stop_finalizes_mp4_and_clears_state(service: CameraHubService) -> None:
    started = await service.start_recording()
    stopped = await service.stop_recording()

    assert stopped.id == started.id
    assert stopped.status == "ready"
    assert stopped.content_path.suffix == ".mp4"
    assert stopped.duration_sec == 8.25
    assert stopped.thumbnail_path.read_bytes() == b"thumbnail"
    assert service.recording_status() is None


@pytest.mark.asyncio
async def test_stop_without_recording_is_rejected(service: CameraHubService) -> None:
    with pytest.raises(NotRecordingError):
        await service.stop_recording()


@pytest.mark.asyncio
async def test_recover_interrupted_recording(service: CameraHubService) -> None:
    record = service.store.allocate("recording")
    assert record.partial_path is not None
    record.partial_path.parent.mkdir(parents=True)
    record.partial_path.write_bytes(b"partial")
    service.store.save(replace(record, status="interrupted"))

    recovered = await service.recover_interrupted()

    assert recovered[0].status == "ready"
    assert recovered[0].duration_sec == 4.5
    assert recovered[0].content_path.read_bytes() == b"recovered"
