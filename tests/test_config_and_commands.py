from pathlib import Path

import pytest

from momo_camera_hub.config import AppConfig, CameraConfig, load_config
from momo_camera_hub.ffmpeg import (
    build_capture_command,
    build_record_command,
    build_remux_command,
    build_snapshot_command,
    rotation_filter,
)
from momo_camera_hub.opencv_capture import build_rawvideo_encoder_command
from momo_camera_hub.runtime import validate_stream_dimensions


def test_default_macos_config_uses_external_storage() -> None:
    config = AppConfig.default(platform_name="Darwin", home=Path("/Users/test"))

    assert config.camera.backend == "opencv"
    assert config.camera.device == "OsmoPocket3"
    assert config.camera.index == 0
    assert config.storage.root == Path("/Users/test/MOMO-Camera-Data")
    assert config.server.port == 8020


def test_default_linux_config_uses_v4l2_and_system_storage() -> None:
    config = AppConfig.default(platform_name="Linux", home=Path("/home/fibo"))

    assert config.camera.backend == "v4l2"
    assert config.camera.device == "/dev/momo-camera"
    assert config.storage.root == Path("/var/lib/momo-camera-hub")


def test_local_yaml_deep_merges_without_losing_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "camera:\n"
        "  width: 1280\n"
        "storage:\n"
        "  root: ./output\n"
        "  minimum_free_gib: 2\n"
        "stream:\n"
        "  mediamtx_binary: ~/.local/bin/mediamtx\n",
        encoding="utf-8",
    )

    config = load_config(path, platform_name="Darwin", home=Path("/Users/test"))

    assert config.camera.width == 1280
    assert config.camera.height == 1080
    assert config.camera.device == "OsmoPocket3"
    assert config.storage.minimum_free_gib == 2
    assert config.storage.root == tmp_path / "output"
    assert config.stream.mediamtx_binary == "/Users/test/.local/bin/mediamtx"


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [(0, None), (90, "transpose=1"), (180, "hflip,vflip"), (270, "transpose=2")],
)
def test_rotation_filter(degrees: int, expected: str | None) -> None:
    assert rotation_filter(degrees) == expected


def test_rotation_rejects_unsupported_degrees() -> None:
    with pytest.raises(ValueError, match="rotation"):
        CameraConfig(rotation=45)


def test_macos_capture_command_publishes_browser_safe_h264() -> None:
    config = AppConfig.default(platform_name="Darwin", home=Path("/Users/test"))

    command = build_capture_command(config)

    assert command[1:4] == ["-m", "momo_camera_hub.opencv_capture", "--camera-index"]
    assert command[4] == "0"
    assert command[-1] == "rtsp://127.0.0.1:8554/armcam"


def test_opencv_bridge_encodes_bgr_frames_with_declared_output_size() -> None:
    command = build_rawvideo_encoder_command(
        ffmpeg_binary="ffmpeg",
        width=1920,
        height=1080,
        fps=30,
        encoder="h264_videotoolbox",
        bitrate="6M",
        keyframe_interval=30,
        rtsp_url="rtsp://127.0.0.1:8554/armcam",
    )

    assert command[command.index("-pixel_format") + 1] == "bgr24"
    assert command[command.index("-video_size") + 1] == "1920x1080"
    assert command[command.index("-r") + 1] == "30"
    assert command[-1] == "rtsp://127.0.0.1:8554/armcam"


def test_linux_capture_command_uses_v4l2() -> None:
    config = AppConfig.default(platform_name="Linux", home=Path("/home/fibo"))

    command = build_capture_command(config)

    assert "v4l2" in command
    assert "/dev/momo-camera" in command
    assert "libx264" in command


def test_snapshot_and_record_commands_read_shared_rtsp(tmp_path: Path) -> None:
    config = AppConfig.default(platform_name="Darwin", home=tmp_path)
    snapshot = build_snapshot_command(config, tmp_path / "shot.jpg")
    recording = build_record_command(config, tmp_path / "record.partial.mkv")

    assert "rtsp://127.0.0.1:8554/armcam" in snapshot
    assert snapshot[snapshot.index("-f") + 1] == "image2"
    assert snapshot[-1] == str(tmp_path / "shot.jpg")
    assert recording[-1] == str(tmp_path / "record.partial.mkv")
    assert ["-c:v", "copy"] == recording[recording.index("-c:v") : recording.index("-c:v") + 2]

    remux = build_remux_command(config, tmp_path / "record.partial.mkv", tmp_path / "record.mp4.tmp")
    assert remux[remux.index("-f") + 1] == "mp4"


def test_stream_validation_rejects_wrong_orientation() -> None:
    with pytest.raises(ValueError, match="1080x1920.*1920x1080"):
        validate_stream_dimensions(expected=(1920, 1080), actual=(1080, 1920))


def test_stream_validation_accepts_expected_dimensions() -> None:
    validate_stream_dimensions(expected=(1920, 1080), actual=(1920, 1080))
