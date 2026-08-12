from pathlib import Path

from momo_camera_hub.cameras import (
    CameraDevice,
    CameraSelectionStore,
    parse_avfoundation_devices,
    resolve_configured_camera,
)
from momo_camera_hub.config import CameraConfig

AVFOUNDATION_OUTPUT = """
[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] MacBook Air相机
[AVFoundation indev @ 0x1] [1] OsmoPocket3
[AVFoundation indev @ 0x1] [2] Capture screen 0
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] MacBook Air麦克风
"""


def test_avfoundation_camera_names_map_to_opencv_indexes() -> None:
    devices = parse_avfoundation_devices(AVFOUNDATION_OUTPUT)

    assert devices == [
        CameraDevice("opencv:0", "MacBook Air相机", "opencv", "MacBook Air相机", 0),
        CameraDevice("opencv:1", "OsmoPocket3", "opencv", "OsmoPocket3", 1),
    ]

    camera = CameraConfig(backend="opencv", device="OsmoPocket3", index=0)
    selected = resolve_configured_camera(camera, devices)

    assert selected == devices[1]
    assert camera.index == 1


def test_camera_selection_store_round_trips_without_touching_yaml(tmp_path: Path) -> None:
    store = CameraSelectionStore(tmp_path / ".camera-selection.json")
    selected = CameraConfig(backend="opencv", device="OsmoPocket3", index=1)
    store.save(selected)

    restored = CameraConfig(backend="opencv", device="MacBook Air相机", index=0)
    assert store.apply(restored) is True
    assert restored.device == "OsmoPocket3"
    assert restored.index == 1
