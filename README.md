# MOMO Camera Hub

`momo-camera-hub` 是独立于机械臂控制程序的本地摄像头服务。它只打开一次物理摄像头，再把同一画面提供给局域网 WebRTC 预览、拍照、手动录像和 MOMO 视觉模块。

## 首版能力

- WebRTC 低延迟实时预览，HLS 端口同时保留。
- 服务端拍照、录像启停和本地媒体库。
- 浏览器断开不会停止录像。
- 一个活动录像，录像期间仍可拍照。
- 摄像头采集进程异常退出后自动重启。
- macOS AVFoundation、Linux V4L2 和无硬件 `testsrc` 三种采集后端。
- 录像先写 Matroska 临时文件，停止后无损转封装为 MP4。

首版不包含账号、外网访问、网页删除、云同步、循环录像或摄像头音频。

## Mac 快速开始

要求：

- Apple Silicon Mac 和 Python 3.11。
- Homebrew FFmpeg；当前开发环境使用 FFmpeg 8.1。
- Osmo Pocket 3 已进入 Webcam 模式。

```bash
brew install ffmpeg uv
./scripts/setup-mediamtx.sh
uv sync
cp config.example.yaml config.local.yaml
uv run momo-camera-hub --config config.local.yaml
```

打开：

```text
摄像头控制台：http://localhost:8020/
机械臂控制台：http://localhost:8010/web/
```

同一局域网里的设备使用 Mac 的局域网 IP 或主机名，例如：

```text
http://your-mac.local:8020/
```

录像和照片默认保存在 `~/MOMO-Camera-Data/`，不会进入这个位于 iCloud 的 Git 仓库。

## 摄像头探测

列出 macOS 视频设备：

```bash
ffmpeg -hide_banner -f avfoundation -list_devices true -i ""
```

列出 Pocket 3 支持的尺寸和帧率：

```bash
ffmpeg -hide_banner -f avfoundation -video_size 1x1 -i "OsmoPocket3:none" -t 1 -f null -
```

若预览方向不正确，在 `config.local.yaml` 中将 `rotation` 调整为 `90`、`180` 或 `270`。若 1080p 不稳定，改为 `1280x720`。

## MOMO 视觉联动

Camera Hub 是物理摄像头的唯一采集者。MOMO 视觉服务读取本机 RTSP：

```bash
export ARM_VISION_SOURCE_TYPE=rtsp
export ARM_VISION_RTSP_URL=rtsp://127.0.0.1:8554/armcam
```

视觉服务继续绑定 `127.0.0.1:8000`，MOMO Web 控制台继续使用 `8010`，Camera Hub 使用 `8020`。

## Linux ARM64

Linux 默认配置使用：

```yaml
camera:
  backend: v4l2
  device: /dev/momo-camera
encoder:
  implementation: libx264
storage:
  root: /var/lib/momo-camera-hub
```

`deploy/momo-camera-hub.service` 是开发板部署模板。正式安装前需要为摄像头建立稳定的 udev 别名 `/dev/momo-camera`，并根据开发板 FFmpeg 能力替换为硬件 H.264 编码器。

## 开发与验证

```bash
uv run pytest -q
uv run ruff check src tests
```

无物理摄像头调试时，把配置改为：

```yaml
camera:
  backend: lavfi
  device: testsrc=size=1280x720:rate=30
  width: 1280
  height: 720
  fps: 30
encoder:
  implementation: libx264
```

## 端口

| 端口 | 用途 |
|---:|---|
| 8020 | Camera Hub Web 与 API |
| 8554 | 本机 RTSP |
| 8888 | HLS |
| 8889 | WebRTC/WHEP |
| 8189/UDP | WebRTC 媒体 |
| 9997 | 本机 MediaMTX Control API |
