from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from momo_camera_hub.cameras import CameraDevice
from momo_camera_hub.config import AppConfig
from momo_camera_hub.runtime import CommandError, StreamSupervisor, count_path_readers


@pytest.mark.asyncio
async def test_stream_probe_retries_while_publisher_is_starting(tmp_path: Path) -> None:
    supervisor = StreamSupervisor(AppConfig.default(platform_name="Darwin", home=tmp_path))
    supervisor._probe_stream_once = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            CommandError("404 Not Found"),
            {"width": 1920, "height": 1080, "fps": "30/1"},
        ]
    )

    result = await supervisor._probe_stream(timeout=1)

    assert result["width"] == 1920
    assert supervisor._probe_stream_once.await_count == 2


def test_count_path_readers_only_counts_selected_stream() -> None:
    payload = {
        "items": [
            {"name": "armcam", "readers": [{"id": "one"}, {"id": "two"}]},
            {"name": "other", "readers": [{"id": "three"}]},
        ]
    }

    assert count_path_readers(payload, "armcam") == 2
    assert count_path_readers(payload, "missing") == 0


@pytest.mark.asyncio
async def test_select_camera_restarts_capture_with_selected_device(tmp_path: Path) -> None:
    config = AppConfig.default(platform_name="Darwin", home=tmp_path)
    supervisor = StreamSupervisor(config)
    old_process = object()
    supervisor.capture_process = old_process  # type: ignore[assignment]
    supervisor._terminate = AsyncMock()  # type: ignore[method-assign]
    supervisor._start_capture = AsyncMock()  # type: ignore[method-assign]
    selected = CameraDevice("opencv:1", "OsmoPocket3", "opencv", "OsmoPocket3", 1)

    await supervisor.select_camera(selected)

    supervisor._terminate.assert_awaited_once_with(old_process)
    supervisor._start_capture.assert_awaited_once()
    assert config.camera.device == "OsmoPocket3"
    assert config.camera.index == 1


@pytest.mark.asyncio
async def test_failed_camera_switch_restores_previous_capture(tmp_path: Path) -> None:
    config = AppConfig.default(platform_name="Darwin", home=tmp_path)
    config.camera.device = "MacBook Air相机"
    config.camera.index = 0
    supervisor = StreamSupervisor(config)
    supervisor._terminate = AsyncMock()  # type: ignore[method-assign]
    supervisor._start_capture = AsyncMock(  # type: ignore[method-assign]
        side_effect=[CommandError("unsupported mode"), None]
    )
    selected = CameraDevice("opencv:1", "OsmoPocket3", "opencv", "OsmoPocket3", 1)

    with pytest.raises(CommandError, match="could not switch"):
        await supervisor.select_camera(selected)

    assert supervisor._start_capture.await_count == 2
    assert config.camera.device == "MacBook Air相机"
    assert config.camera.index == 0
