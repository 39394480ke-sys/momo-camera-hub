from __future__ import annotations

import sys
from pathlib import Path

from .config import AppConfig


def rotation_filter(degrees: int) -> str | None:
    filters = {0: None, 90: "transpose=1", 180: "hflip,vflip", 270: "transpose=2"}
    if degrees not in filters:
        raise ValueError("rotation must be one of 0, 90, 180, or 270")
    return filters[degrees]


def build_capture_command(config: AppConfig) -> list[str]:
    camera = config.camera
    if camera.backend == "opencv":
        return [
            sys.executable,
            "-m",
            "momo_camera_hub.opencv_capture",
            "--camera-index",
            str(camera.index),
            "--width",
            str(camera.width),
            "--height",
            str(camera.height),
            "--fps",
            str(camera.fps),
            "--rotation",
            str(camera.rotation),
            "--encoder",
            config.encoder.implementation,
            "--bitrate",
            config.encoder.bitrate,
            "--keyframe-interval",
            str(config.encoder.keyframe_interval),
            "--ffmpeg-binary",
            config.ffmpeg_binary,
            "--rtsp-url",
            config.stream.rtsp_url,
        ]
    command = [config.ffmpeg_binary, "-hide_banner", "-nostdin", "-loglevel", "warning"]
    if camera.backend == "avfoundation":
        command += [
            "-f",
            "avfoundation",
            "-pixel_format",
            camera.pixel_format,
            "-framerate",
            str(camera.fps),
            "-video_size",
            f"{camera.width}x{camera.height}",
            "-use_wallclock_as_timestamps",
            "1",
            "-i",
            f"{camera.device}:none",
        ]
    elif camera.backend == "v4l2":
        command += [
            "-f",
            "v4l2",
            "-input_format",
            camera.pixel_format,
            "-framerate",
            str(camera.fps),
            "-video_size",
            f"{camera.width}x{camera.height}",
            "-use_wallclock_as_timestamps",
            "1",
            "-i",
            camera.device,
        ]
    else:
        command += ["-re", "-f", "lavfi", "-i", camera.device]

    video_filter = rotation_filter(camera.rotation)
    if video_filter:
        command += ["-vf", video_filter]
    command += ["-r", str(camera.fps), "-fps_mode", "cfr"]

    if config.encoder.implementation == "h264_videotoolbox":
        command += [
            "-an",
            "-c:v",
            "h264_videotoolbox",
            "-realtime",
            "1",
            "-allow_sw",
            "1",
            "-profile:v",
            "baseline",
        ]
    else:
        command += [
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-profile:v",
            "baseline",
        ]
    command += [
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        config.encoder.bitrate,
        "-maxrate",
        config.encoder.bitrate,
        "-bufsize",
        config.encoder.bitrate,
        "-g",
        str(config.encoder.keyframe_interval),
        "-bf",
        "0",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        config.stream.rtsp_url,
    ]
    return command


def _rtsp_input(config: AppConfig) -> list[str]:
    return ["-rtsp_transport", "tcp", "-i", config.stream.rtsp_url]


def build_snapshot_command(config: AppConfig, output: Path) -> list[str]:
    return [
        config.ffmpeg_binary,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        *_rtsp_input(config),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-f",
        "image2",
        "-y",
        str(output),
    ]


def build_record_command(config: AppConfig, output: Path) -> list[str]:
    return [
        config.ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        *_rtsp_input(config),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-f",
        "matroska",
        "-y",
        str(output),
    ]


def build_remux_command(config: AppConfig, partial: Path, output: Path) -> list[str]:
    return [
        config.ffmpeg_binary,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(partial),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        "-y",
        str(output),
    ]


def build_thumbnail_command(config: AppConfig, source: Path, output: Path) -> list[str]:
    return [
        config.ffmpeg_binary,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        "0",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        "scale=480:-2",
        "-q:v",
        "3",
        "-f",
        "image2",
        "-y",
        str(output),
    ]
