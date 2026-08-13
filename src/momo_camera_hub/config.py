from __future__ import annotations

import platform
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml


@dataclass
class CameraConfig:
    backend: str = "opencv"
    device: str = "OsmoPocket3"
    index: int = 0
    width: int = 1920
    height: int = 1080
    fps: int = 30
    rotation: int = 0
    pixel_format: str = "nv12"

    def __post_init__(self) -> None:
        if self.rotation not in {0, 90, 180, 270}:
            raise ValueError("camera rotation must be one of 0, 90, 180, or 270")
        if self.width <= 0 or self.height <= 0 or self.fps <= 0:
            raise ValueError("camera width, height, and fps must be positive")
        if self.backend not in {"opencv", "avfoundation", "v4l2", "lavfi"}:
            raise ValueError(f"unsupported camera backend: {self.backend}")


@dataclass
class EncoderConfig:
    implementation: str = "h264_videotoolbox"
    bitrate: str = "6M"
    keyframe_interval: int = 30


@dataclass
class StreamConfig:
    path: str = "armcam"
    rtsp_port: int = 8554
    hls_port: int = 8888
    webrtc_port: int = 8889
    webrtc_udp_port: int = 8189
    api_port: int = 9997
    mediamtx_binary: str = "mediamtx"

    @property
    def rtsp_url(self) -> str:
        return self.rtsp_url_for(self.path)

    def rtsp_url_for(self, path: str) -> str:
        return f"rtsp://127.0.0.1:{self.rtsp_port}/{path}"


@dataclass
class AnalysisStreamConfig:
    enabled: bool = True
    path: str = "armcam-analysis"
    width: int = 640
    height: int = 360
    fps: int = 30
    bitrate: str = "1M"
    keyframe_interval: int = 30

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or self.fps <= 0:
            raise ValueError("analysis stream width, height, and fps must be positive")
        if self.keyframe_interval <= 0:
            raise ValueError("analysis stream keyframe interval must be positive")
        if not self.path:
            raise ValueError("analysis stream path cannot be empty")
        if not self.bitrate:
            raise ValueError("analysis stream bitrate cannot be empty")


@dataclass
class VisionConfig:
    base_url: str = "http://127.0.0.1:8000"

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.base_url:
            raise ValueError("vision base URL cannot be empty")


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8020


@dataclass
class StorageConfig:
    root: Path = Path("~/MOMO-Camera-Data")
    minimum_free_gib: float = 5.0

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()
        if self.minimum_free_gib < 0:
            raise ValueError("minimum_free_gib cannot be negative")


@dataclass
class AppConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    encoder: EncoderConfig = field(default_factory=EncoderConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    analysis_stream: AnalysisStreamConfig = field(default_factory=AnalysisStreamConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"

    def __post_init__(self) -> None:
        if self.analysis_stream.enabled and self.analysis_stream.path == self.stream.path:
            raise ValueError("analysis stream path must differ from the primary stream path")

    @property
    def analysis_rtsp_url(self) -> str:
        return self.stream.rtsp_url_for(self.analysis_stream.path)

    @classmethod
    def default(cls, platform_name: str | None = None, home: Path | None = None) -> AppConfig:
        operating_system = platform_name or platform.system()
        user_home = (home or Path.home()).resolve()
        if operating_system == "Linux":
            return cls(
                camera=CameraConfig(backend="v4l2", device="/dev/momo-camera", pixel_format="yuyv422"),
                encoder=EncoderConfig(implementation="libx264"),
                storage=StorageConfig(root=Path("/var/lib/momo-camera-hub")),
            )
        return cls(storage=StorageConfig(root=user_home / "MOMO-Camera-Data"))

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["storage"]["root"] = str(self.storage.root)
        return payload


ConfigType = TypeVar("ConfigType")


def _merge_dataclass(instance: ConfigType, overrides: dict[str, Any]) -> ConfigType:
    known = {item.name: item for item in fields(instance)}  # type: ignore[arg-type]
    unknown = set(overrides) - set(known)
    if unknown:
        raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
    values: dict[str, Any] = {}
    for name in known:
        current = getattr(instance, name)
        if name not in overrides:
            values[name] = current
        elif is_dataclass(current) and isinstance(overrides[name], dict):
            values[name] = _merge_dataclass(current, overrides[name])
        elif isinstance(current, Path):
            values[name] = Path(overrides[name])
        else:
            values[name] = overrides[name]
    return type(instance)(**values)


def load_config(
    path: str | Path | None = None,
    *,
    platform_name: str | None = None,
    home: Path | None = None,
) -> AppConfig:
    user_home = (home or Path.home()).resolve()
    config = AppConfig.default(platform_name=platform_name, home=user_home)
    if path is None:
        return config
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"configuration file not found: {config_path}")
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a mapping")
    merged = _merge_dataclass(config, payload)
    if not merged.storage.root.is_absolute():
        merged.storage.root = (config_path.parent / merged.storage.root).resolve()
    if merged.stream.mediamtx_binary.startswith("~/"):
        merged.stream.mediamtx_binary = str(user_home / merged.stream.mediamtx_binary[2:])
    return merged
