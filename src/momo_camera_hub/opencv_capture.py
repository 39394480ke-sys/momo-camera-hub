from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from collections.abc import Sequence

import cv2


def build_rawvideo_encoder_command(
    *,
    ffmpeg_binary: str,
    width: int,
    height: int,
    fps: int,
    encoder: str,
    bitrate: str,
    keyframe_interval: int,
    rtsp_url: str,
) -> list[str]:
    command = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "rawvideo",
        "-pixel_format",
        "bgr24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        encoder,
    ]
    if encoder == "h264_videotoolbox":
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


def rotate_frame(frame, degrees: int):
    if degrees == 0:
        return frame
    if degrees == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError("rotation must be one of 0, 90, 180, or 270")


def fit_frame(frame, width: int, height: int):
    """Fit a camera frame into the configured stream without distorting it."""
    source_height, source_width = frame.shape[:2]
    if (source_width, source_height) == (width, height):
        return frame

    scale = min(width / source_width, height / source_height)
    resized_width = max(1, round(source_width * scale))
    resized_height = max(1, round(source_height * scale))
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (resized_width, resized_height), interpolation=interpolation)
    horizontal_padding = width - resized_width
    vertical_padding = height - resized_height
    return cv2.copyMakeBorder(
        resized,
        vertical_padding // 2,
        vertical_padding - vertical_padding // 2,
        horizontal_padding // 2,
        horizontal_padding - horizontal_padding // 2,
        cv2.BORDER_CONSTANT,
        value=(0, 0, 0),
    )


def run(args: argparse.Namespace) -> int:
    capture = cv2.VideoCapture(args.camera_index, cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY)
    if not capture.isOpened():
        print(f"could not open camera index {args.camera_index}", file=sys.stderr, flush=True)
        return 2
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    capture.set(cv2.CAP_PROP_FPS, args.fps)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    ok, frame = capture.read()
    if not ok or frame is None:
        capture.release()
        print(f"camera index {args.camera_index} opened but returned no frame", file=sys.stderr, flush=True)
        return 3
    frame = rotate_frame(frame, args.rotation)
    frame = fit_frame(frame, args.width, args.height)

    command = build_rawvideo_encoder_command(
        ffmpeg_binary=args.ffmpeg_binary,
        width=args.width,
        height=args.height,
        fps=args.fps,
        encoder=args.encoder,
        bitrate=args.bitrate,
        keyframe_interval=args.keyframe_interval,
        rtsp_url=args.rtsp_url,
    )
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE)
    stopping = False

    def stop_requested(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    try:
        while not stopping:
            if encoder.poll() is not None:
                print(f"FFmpeg encoder exited with {encoder.returncode}", file=sys.stderr, flush=True)
                return encoder.returncode or 5
            if encoder.stdin is None:
                return 5
            try:
                encoder.stdin.write(frame.tobytes())
            except BrokenPipeError:
                return encoder.wait()
            ok, frame = capture.read()
            if not ok or frame is None:
                print("camera frame read failed", file=sys.stderr, flush=True)
                return 6
            frame = rotate_frame(frame, args.rotation)
            frame = fit_frame(frame, args.width, args.height)
        return 0
    finally:
        capture.release()
        if encoder.stdin:
            encoder.stdin.close()
        try:
            encoder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            encoder.terminate()
            encoder.wait(timeout=5)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="OpenCV camera to FFmpeg RTSP bridge")
    result.add_argument("--camera-index", type=int, required=True)
    result.add_argument("--width", type=int, required=True)
    result.add_argument("--height", type=int, required=True)
    result.add_argument("--fps", type=int, required=True)
    result.add_argument("--rotation", type=int, choices=(0, 90, 180, 270), required=True)
    result.add_argument("--encoder", required=True)
    result.add_argument("--bitrate", required=True)
    result.add_argument("--keyframe-interval", type=int, required=True)
    result.add_argument("--ffmpeg-binary", required=True)
    result.add_argument("--rtsp-url", required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
