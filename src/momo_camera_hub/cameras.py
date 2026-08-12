from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import CameraConfig


@dataclass(frozen=True, slots=True)
class CameraDevice:
    id: str
    name: str
    backend: str
    device: str
    index: int

    def public_dict(self, *, selected: bool = False) -> dict[str, Any]:
        return {**asdict(self), "selected": selected}


class CameraSelectionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if not isinstance(payload.get("backend"), str) or not isinstance(payload.get("device"), str):
            return None
        if not isinstance(payload.get("index"), int):
            return None
        return payload

    def apply(self, camera: CameraConfig) -> bool:
        payload = self.load()
        if payload is None:
            return False
        camera.backend = payload["backend"]
        camera.device = payload["device"]
        camera.index = payload["index"]
        return True

    def save(self, camera: CameraConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(
                {"backend": camera.backend, "device": camera.device, "index": camera.index},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def discover_cameras(
    camera: CameraConfig,
    *,
    ffmpeg_binary: str = "ffmpeg",
    platform_name: str | None = None,
) -> list[CameraDevice]:
    operating_system = platform_name or platform.system()
    if operating_system == "Darwin":
        return discover_avfoundation_cameras(ffmpeg_binary, backend=camera.backend)
    if operating_system == "Linux":
        return discover_v4l2_cameras()
    return [
        CameraDevice(
            id=f"{camera.backend}:{camera.index}",
            name=camera.device or f"Camera {camera.index}",
            backend=camera.backend,
            device=camera.device,
            index=camera.index,
        )
    ]


def discover_avfoundation_cameras(ffmpeg_binary: str, *, backend: str = "opencv") -> list[CameraDevice]:
    process = subprocess.run(  # noqa: S603 - configured local FFmpeg executable
        [ffmpeg_binary, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True,
        text=True,
        check=False,
        timeout=8,
    )
    return parse_avfoundation_devices(process.stderr, backend=backend)


def parse_avfoundation_devices(output: str, *, backend: str = "opencv") -> list[CameraDevice]:
    devices: list[CameraDevice] = []
    in_video_section = False
    pattern = re.compile(r"\]\s+\[(\d+)\]\s+(.+?)\s*$")
    for line in output.splitlines():
        if "AVFoundation video devices:" in line:
            in_video_section = True
            continue
        if "AVFoundation audio devices:" in line:
            break
        if not in_video_section:
            continue
        match = pattern.search(line)
        if not match:
            continue
        index = int(match.group(1))
        name = match.group(2).strip()
        if name.lower().startswith("capture screen"):
            continue
        devices.append(
            CameraDevice(
                id=f"{backend}:{index}",
                name=name,
                backend=backend,
                device=name,
                index=index,
            )
        )
    return devices


def discover_v4l2_cameras(device_root: str | Path = "/dev") -> list[CameraDevice]:
    root = Path(device_root)
    devices: list[CameraDevice] = []
    for index, path in enumerate(sorted(root.glob("video*"))):
        name_path = Path("/sys/class/video4linux") / path.name / "name"
        try:
            name = name_path.read_text(encoding="utf-8").strip()
        except OSError:
            name = path.name
        devices.append(
            CameraDevice(
                id=f"v4l2:{path}",
                name=name,
                backend="v4l2",
                device=str(path),
                index=index,
            )
        )
    return devices


def resolve_configured_camera(camera: CameraConfig, devices: list[CameraDevice]) -> CameraDevice | None:
    if not devices:
        return None
    exact_name = next((item for item in devices if item.name == camera.device), None)
    if exact_name is not None:
        camera.backend = exact_name.backend
        camera.device = exact_name.device
        camera.index = exact_name.index
        return exact_name
    exact_device = next((item for item in devices if item.device == camera.device), None)
    if exact_device is not None:
        camera.backend = exact_device.backend
        camera.index = exact_device.index
        return exact_device
    exact_index = next(
        (item for item in devices if item.backend == camera.backend and item.index == camera.index),
        None,
    )
    return exact_index


__all__ = [
    "CameraDevice",
    "CameraSelectionStore",
    "discover_avfoundation_cameras",
    "discover_cameras",
    "discover_v4l2_cameras",
    "parse_avfoundation_devices",
    "resolve_configured_camera",
]
