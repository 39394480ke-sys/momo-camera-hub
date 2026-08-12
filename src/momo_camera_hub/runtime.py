from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from .cameras import CameraDevice
from .config import AppConfig
from .ffmpeg import (
    build_capture_command,
    build_record_command,
    build_remux_command,
    build_snapshot_command,
    build_thumbnail_command,
)


class CommandError(RuntimeError):
    pass


def count_path_readers(payload: dict[str, Any], path_name: str) -> int:
    for item in payload.get("items", []):
        if item.get("name") == path_name:
            return len(item.get("readers", []))
    return 0


def validate_stream_dimensions(*, expected: tuple[int, int], actual: tuple[int, int]) -> None:
    if actual != expected:
        raise ValueError(
            f"camera produced {actual[0]}x{actual[1]}, expected {expected[0]}x{expected[1]}; "
            "adjust camera.rotation or the configured dimensions"
        )


async def _run_checked(command: list[str], timeout: float = 20.0) -> None:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        os.killpg(process.pid, signal.SIGTERM)
        await process.wait()
        raise CommandError(f"command timed out after {timeout:.0f}s: {command[0]}") from None
    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip()
        raise CommandError(message or f"{command[0]} exited with {process.returncode}")


class FFmpegMediaRunner:
    def __init__(self, config: AppConfig):
        self.config = config
        self.recording_process: asyncio.subprocess.Process | None = None

    async def snapshot(self, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".jpg.tmp")
        await _run_checked(build_snapshot_command(self.config, temporary), timeout=12)
        os.replace(temporary, output)

    async def start_recording(self, output: Path) -> None:
        if self.recording_process and self.recording_process.returncode is None:
            raise RuntimeError("recording process is already running")
        output.parent.mkdir(parents=True, exist_ok=True)
        self.recording_process = await asyncio.create_subprocess_exec(
            *build_record_command(self.config, output),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        await asyncio.sleep(0.3)
        if self.recording_process.returncode is not None:
            stderr = await self.recording_process.stderr.read() if self.recording_process.stderr else b""
            self.recording_process = None
            raise CommandError(stderr.decode(errors="replace").strip() or "recording process exited during startup")

    async def stop_recording(self, partial: Path, final: Path) -> float:
        process = self.recording_process
        if process and process.returncode is None:
            if process.stdin:
                process.stdin.write(b"q\n")
                await process.stdin.drain()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGTERM)
                await process.wait()
        self.recording_process = None
        return await self._finalize_partial(partial, final)

    async def recover(self, partial: Path, final: Path) -> float:
        return await self._finalize_partial(partial, final)

    async def thumbnail(self, source: Path, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        await _run_checked(build_thumbnail_command(self.config, source, output), timeout=12)

    async def close(self) -> None:
        process = self.recording_process
        if process and process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            await process.wait()
        self.recording_process = None

    async def _finalize_partial(self, partial: Path, final: Path) -> float:
        if not partial.exists() or partial.stat().st_size == 0:
            raise CommandError("recording did not produce any video")
        final.parent.mkdir(parents=True, exist_ok=True)
        temporary = final.with_suffix(".mp4.tmp")
        await _run_checked(build_remux_command(self.config, partial, temporary), timeout=60)
        os.replace(temporary, final)
        partial.unlink(missing_ok=True)
        return await self._probe_duration(final)

    async def _probe_duration(self, source: Path) -> float:
        process = await asyncio.create_subprocess_exec(
            self.config.ffprobe_binary,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(source),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise CommandError(stderr.decode(errors="replace").strip() or "ffprobe failed")
        return float(json.loads(stdout)["format"]["duration"])


def render_mediamtx_config(config: AppConfig, runtime_dir: Path) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "mediamtx.yml"
    content = f"""\
logLevel: info
rtsp: true
rtspAddress: 127.0.0.1:{config.stream.rtsp_port}
rtmp: false
hls: true
hlsAddress: :{config.stream.hls_port}
hlsAllowOrigins: ["*"]
webrtc: true
webrtcAddress: :{config.stream.webrtc_port}
webrtcAllowOrigins: ["*"]
webrtcLocalUDPAddress: :{config.stream.webrtc_udp_port}
webrtcAdditionalHosts: []
api: true
apiAddress: 127.0.0.1:{config.stream.api_port}
metrics: false
playback: false
pathDefaults:
  source: publisher
paths:
  {config.stream.path}:
"""
    temporary = path.with_suffix(".yml.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
    return path


class StreamSupervisor:
    def __init__(self, config: AppConfig, runtime_dir: Path | None = None):
        self.config = config
        self.runtime_dir = (runtime_dir or Path(tempfile.gettempdir()) / "momo-camera-hub").resolve()
        self.mediamtx_process: asyncio.subprocess.Process | None = None
        self.capture_process: asyncio.subprocess.Process | None = None
        self.watch_task: asyncio.Task[None] | None = None
        self.viewer_task: asyncio.Task[None] | None = None
        self.stopping = False
        self.last_error = ""
        self.capture_restarts = 0
        self.viewer_count = 0
        self.capture_started_at: float | None = None
        self.actual_stream: dict[str, Any] | None = None
        self._capture_lock = asyncio.Lock()

    async def start(self) -> None:
        self._check_dependencies()
        mediamtx_config = render_mediamtx_config(self.config, self.runtime_dir)
        self.mediamtx_process = await self._spawn([self.config.stream.mediamtx_binary, str(mediamtx_config)])
        await self._wait_for_port("127.0.0.1", self.config.stream.rtsp_port, timeout=10)
        try:
            await self._start_capture()
        except Exception as exc:
            self.capture_process = None
            self.last_error = str(exc)
        self.watch_task = asyncio.create_task(self._watch_capture(), name="camera-capture-watchdog")
        self.viewer_task = asyncio.create_task(self._watch_viewers(), name="stream-viewer-counter")

    async def stop(self) -> None:
        self.stopping = True
        if self.watch_task:
            self.watch_task.cancel()
            await asyncio.gather(self.watch_task, return_exceptions=True)
        if self.viewer_task:
            self.viewer_task.cancel()
            await asyncio.gather(self.viewer_task, return_exceptions=True)
        await self._terminate(self.capture_process)
        await self._terminate(self.mediamtx_process)
        self.capture_process = None
        self.mediamtx_process = None

    async def select_camera(self, camera: CameraDevice) -> None:
        async with self._capture_lock:
            previous = (
                self.config.camera.backend,
                self.config.camera.device,
                self.config.camera.index,
            )
            previous_process = self.capture_process
            self.capture_process = None
            await self._terminate(previous_process)
            self.config.camera.backend = camera.backend
            self.config.camera.device = camera.device
            self.config.camera.index = camera.index
            self.actual_stream = None
            try:
                await self._start_capture()
            except Exception as selection_error:
                self.config.camera.backend, self.config.camera.device, self.config.camera.index = previous
                self.capture_process = None
                try:
                    await self._start_capture()
                except Exception as restore_error:
                    self.capture_process = None
                    self.last_error = f"camera selection failed: {selection_error}; restore failed: {restore_error}"
                else:
                    self.last_error = f"camera selection failed: {selection_error}"
                raise CommandError(f"could not switch to {camera.name}: {selection_error}") from selection_error
            self.capture_restarts += 1
            self.last_error = ""

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self.capture_process and self.capture_process.returncode is None),
            "media_server_running": bool(self.mediamtx_process and self.mediamtx_process.returncode is None),
            "capture_restarts": self.capture_restarts,
            "capture_started_at": self.capture_started_at,
            "actual_stream": self.actual_stream,
            "viewer_count": self.viewer_count,
            "last_error": self.last_error or None,
        }

    async def _watch_viewers(self) -> None:
        while not self.stopping:
            try:
                self.viewer_count = await asyncio.to_thread(self._fetch_viewer_count)
            except Exception:
                self.viewer_count = 0
            await asyncio.sleep(1)

    def _fetch_viewer_count(self) -> int:
        url = f"http://127.0.0.1:{self.config.stream.api_port}/v3/paths/list"
        with urllib.request.urlopen(url, timeout=1) as response:
            payload = json.load(response)
        return count_path_readers(payload, self.config.stream.path)

    async def _start_capture(self) -> None:
        self.capture_process = await self._spawn(build_capture_command(self.config))
        self.capture_started_at = time.time()
        await asyncio.sleep(0.8)
        if self.capture_process.returncode is not None:
            stderr = await self.capture_process.stderr.read() if self.capture_process.stderr else b""
            raise CommandError(stderr.decode(errors="replace").strip() or "camera capture exited during startup")
        try:
            self.actual_stream = await self._probe_stream()
            validate_stream_dimensions(
                expected=(self.config.camera.width, self.config.camera.height),
                actual=(self.actual_stream["width"], self.actual_stream["height"]),
            )
        except Exception:
            await self._terminate(self.capture_process)
            raise

    async def _watch_capture(self) -> None:
        delay = 1.0
        while not self.stopping:
            process = self.capture_process
            if not process:
                await asyncio.sleep(delay)
                async with self._capture_lock:
                    if self.capture_process is not None or self.stopping:
                        continue
                    try:
                        await self._start_capture()
                        delay = 1.0
                        self.last_error = ""
                    except Exception as exc:
                        self.capture_process = None
                        self.last_error = str(exc)
                        delay = min(delay * 2, 30)
                continue
            stderr = await process.stderr.read() if process.stderr else b""
            await process.wait()
            if self.stopping:
                return
            async with self._capture_lock:
                if process is not self.capture_process:
                    continue
                self.capture_process = None
                self.last_error = stderr.decode(errors="replace").strip()[-1000:] or "camera capture stopped"
                self.capture_restarts += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
                try:
                    await self._start_capture()
                    delay = 1.0
                    self.last_error = ""
                except Exception as exc:
                    self.capture_process = None
                    self.last_error = str(exc)

    def _check_dependencies(self) -> None:
        missing = [
            command
            for command in (self.config.ffmpeg_binary, self.config.ffprobe_binary, self.config.stream.mediamtx_binary)
            if not (Path(command).exists() or shutil.which(command))
        ]
        if missing:
            raise FileNotFoundError(f"missing required executables: {', '.join(missing)}")

    async def _probe_stream(self, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return await self._probe_stream_once()
            except CommandError as exc:
                last_error = exc
                await asyncio.sleep(0.2)
        raise CommandError(f"published stream was not ready within {timeout:g}s: {last_error}")

    async def _probe_stream_once(self) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            self.config.ffprobe_binary,
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "json",
            self.config.stream.rtsp_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3)
        except TimeoutError:
            os.killpg(process.pid, signal.SIGTERM)
            await process.wait()
            raise CommandError("timed out while validating the published camera stream") from None
        if process.returncode != 0:
            raise CommandError(stderr.decode(errors="replace").strip() or "could not validate camera stream")
        streams = json.loads(stdout).get("streams", [])
        if not streams:
            raise CommandError("published camera stream contains no video track")
        track = streams[0]
        return {
            "width": int(track["width"]),
            "height": int(track["height"]),
            "fps": str(track.get("r_frame_rate") or ""),
        }

    @staticmethod
    async def _spawn(command: list[str]) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process | None) -> None:
        if not process or process.returncode is not None:
            return
        os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    @staticmethod
    async def _wait_for_port(host: str, port: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                reader, writer = await asyncio.open_connection(host, port)
                writer.close()
                await writer.wait_closed()
                return
            except (ConnectionError, OSError):
                await asyncio.sleep(0.1)
        raise TimeoutError(f"timed out waiting for {host}:{port}")


def local_ip_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("10.255.255.255", 1))
            return str(connection.getsockname()[0])
    except OSError:
        return "127.0.0.1"
