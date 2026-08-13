from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from momo_camera_hub.cameras import CameraDevice
from momo_camera_hub.config import AppConfig
from momo_camera_hub.runtime import (
    CommandError,
    StreamSupervisor,
    count_path_readers,
    path_is_ready,
    render_mediamtx_config,
    validate_stream_fps,
)


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
    supervisor._probe_stream_once.assert_awaited_with("rtsp://127.0.0.1:8554/armcam")


def test_mediamtx_config_registers_primary_and_analysis_paths(tmp_path: Path) -> None:
    config = AppConfig.default(platform_name="Darwin", home=tmp_path)

    rendered = render_mediamtx_config(config, tmp_path).read_text(encoding="utf-8")

    assert "  armcam:\n" in rendered
    assert "  armcam-analysis:\n" in rendered
    assert "rtspTransports: [tcp]\n" in rendered
    assert "srt: false\n" in rendered
    assert "moq: false\n" in rendered


@pytest.mark.asyncio
async def test_capture_start_probes_and_validates_both_streams(tmp_path: Path) -> None:
    supervisor = StreamSupervisor(AppConfig.default(platform_name="Darwin", home=tmp_path))
    process = SimpleNamespace(returncode=None)
    supervisor.mediamtx_process = SimpleNamespace(returncode=None)  # type: ignore[assignment]
    supervisor._spawn = AsyncMock(return_value=process)  # type: ignore[method-assign]
    supervisor._probe_stream = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"width": 1920, "height": 1080, "fps": "30/1"},
            {"width": 640, "height": 360, "fps": "30/1"},
        ]
    )

    await supervisor._start_capture()

    assert supervisor.actual_stream == {"width": 1920, "height": 1080, "fps": "30/1"}
    assert supervisor.actual_analysis_stream == {"width": 640, "height": 360, "fps": "30/1"}
    assert supervisor._probe_stream.await_args_list[1].args == (supervisor.config.analysis_rtsp_url,)
    assert supervisor.status()["analysis_online"] is True


def test_path_readiness_uses_mediamtx_ready_and_online_signals() -> None:
    payload = {
        "items": [
            {"name": "armcam", "ready": True, "online": True},
            {"name": "armcam-analysis", "ready": True, "online": False},
        ]
    }

    assert path_is_ready(payload, "armcam") is True
    assert path_is_ready(payload, "armcam-analysis") is False
    assert path_is_ready(payload, "missing") is False


@pytest.mark.asyncio
async def test_media_server_watchdog_restarts_server_and_recycles_stale_capture(tmp_path: Path) -> None:
    supervisor = StreamSupervisor(AppConfig.default(platform_name="Darwin", home=tmp_path))
    media_stderr = SimpleNamespace(read=AsyncMock(return_value=b"listener failed"))
    failed_media = SimpleNamespace(returncode=1, stderr=media_stderr, wait=AsyncMock())
    stale_capture = SimpleNamespace(returncode=None)
    restarted_media = SimpleNamespace(returncode=None)
    supervisor.mediamtx_process = failed_media  # type: ignore[assignment]
    supervisor.capture_process = stale_capture  # type: ignore[assignment]
    supervisor.main_online = True
    supervisor.analysis_online = True
    supervisor._restart_delay_initial = 0
    supervisor._terminate = AsyncMock()  # type: ignore[method-assign]

    async def restart_media_server() -> None:
        supervisor.mediamtx_process = restarted_media  # type: ignore[assignment]
        supervisor.stopping = True

    supervisor._start_media_server = AsyncMock(side_effect=restart_media_server)  # type: ignore[method-assign]

    await supervisor._watch_media_server()

    supervisor._start_media_server.assert_awaited_once()
    supervisor._terminate.assert_awaited_once_with(stale_capture)
    assert supervisor.mediamtx_process is restarted_media
    assert supervisor.capture_process is None
    assert supervisor.media_server_restarts == 1
    assert supervisor.main_online is False
    assert supervisor.analysis_online is False
    assert "listener failed" in supervisor.last_error


@pytest.mark.asyncio
async def test_media_server_start_waits_for_rtsp_and_control_api(tmp_path: Path) -> None:
    supervisor = StreamSupervisor(AppConfig.default(platform_name="Darwin", home=tmp_path))
    process = SimpleNamespace(returncode=None)
    supervisor._spawn = AsyncMock(return_value=process)  # type: ignore[method-assign]
    supervisor._wait_for_port = AsyncMock()  # type: ignore[method-assign]

    await supervisor._start_media_server()

    assert supervisor.mediamtx_process is process
    assert [call.args[1] for call in supervisor._wait_for_port.await_args_list] == [
        supervisor.config.stream.rtsp_port,
        supervisor.config.stream.api_port,
    ]


@pytest.mark.asyncio
async def test_invalid_analysis_stream_stops_capture_for_watchdog_restart(tmp_path: Path) -> None:
    supervisor = StreamSupervisor(AppConfig.default(platform_name="Darwin", home=tmp_path))
    process = SimpleNamespace(returncode=None)
    supervisor._spawn = AsyncMock(return_value=process)  # type: ignore[method-assign]
    supervisor._terminate = AsyncMock()  # type: ignore[method-assign]
    supervisor._probe_stream = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"width": 1920, "height": 1080, "fps": "30/1"},
            {"width": 1280, "height": 720, "fps": "30/1"},
        ]
    )

    with pytest.raises(ValueError, match="1280x720.*640x360"):
        await supervisor._start_capture()

    supervisor._terminate.assert_awaited_once_with(process)
    assert supervisor.actual_stream is None
    assert supervisor.actual_analysis_stream is None


def test_stream_fps_validation_accepts_ntsc_and_rejects_wrong_rate() -> None:
    validate_stream_fps(expected=30, actual="30000/1001")

    with pytest.raises(ValueError, match="15 FPS.*30 FPS"):
        validate_stream_fps(expected=30, actual="15/1")


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
