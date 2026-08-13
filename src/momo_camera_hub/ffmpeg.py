from __future__ import annotations

import sys
from pathlib import Path

from .config import AppConfig


def rotation_filter(degrees: int) -> str | None:
    filters = {0: None, 90: "transpose=1", 180: "hflip,vflip", 270: "transpose=2"}
    if degrees not in filters:
        raise ValueError("rotation must be one of 0, 90, 180, or 270")
    return filters[degrees]


def _encoder_output(
    *,
    implementation: str,
    bitrate: str,
    keyframe_interval: int,
    fps: int,
    rtsp_url: str,
    mapping: str | None = None,
) -> list[str]:
    command: list[str] = []
    if mapping:
        command += ["-map", mapping]
    command += ["-an", "-c:v", implementation]
    if implementation == "h264_videotoolbox":
        command += ["-realtime", "1", "-allow_sw", "1", "-profile:v", "baseline"]
    else:
        command += ["-preset", "ultrafast", "-tune", "zerolatency", "-profile:v", "baseline"]
    command += [
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-fps_mode",
        "cfr",
        "-b:v",
        bitrate,
        "-maxrate",
        bitrate,
        "-bufsize",
        bitrate,
        "-g",
        str(keyframe_interval),
        "-bf",
        "0",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_url,
    ]
    return command


def build_capture_command(config: AppConfig) -> list[str]:
    camera = config.camera
    if camera.backend == "opencv":
        command = [
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
        if config.analysis_stream.enabled:
            command += [
                "--analysis-rtsp-url",
                config.analysis_rtsp_url,
                "--analysis-width",
                str(config.analysis_stream.width),
                "--analysis-height",
                str(config.analysis_stream.height),
                "--analysis-fps",
                str(config.analysis_stream.fps),
                "--analysis-bitrate",
                config.analysis_stream.bitrate,
                "--analysis-keyframe-interval",
                str(config.analysis_stream.keyframe_interval),
            ]
        return command
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
    if config.analysis_stream.enabled:
        primary_input = f"[0:v]{video_filter}," if video_filter else "[0:v]"
        filter_graph = (
            f"{primary_input}split=2[primary][analysis-source];"
            f"[analysis-source]scale={config.analysis_stream.width}:{config.analysis_stream.height},"
            f"fps={config.analysis_stream.fps}[analysis]"
        )
        command += ["-filter_complex", filter_graph]
        command += _encoder_output(
            implementation=config.encoder.implementation,
            bitrate=config.encoder.bitrate,
            keyframe_interval=config.encoder.keyframe_interval,
            fps=camera.fps,
            rtsp_url=config.stream.rtsp_url,
            mapping="[primary]",
        )
        command += _encoder_output(
            implementation=config.encoder.implementation,
            bitrate=config.analysis_stream.bitrate,
            keyframe_interval=config.analysis_stream.keyframe_interval,
            fps=config.analysis_stream.fps,
            rtsp_url=config.analysis_rtsp_url,
            mapping="[analysis]",
        )
    else:
        if video_filter:
            command += ["-vf", video_filter]
        command += _encoder_output(
            implementation=config.encoder.implementation,
            bitrate=config.encoder.bitrate,
            keyframe_interval=config.encoder.keyframe_interval,
            fps=camera.fps,
            rtsp_url=config.stream.rtsp_url,
        )
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
