const state = {
  status: null,
  mediaType: "",
  cursor: null,
  loading: false,
  recordingStartedAt: null,
  cameras: [],
  selectedCameraId: null,
  switchingCamera: false,
  visionLatest: null,
  visionHealth: null,
  visionRuntime: null,
  visionTarget: null,
  visionFastLoading: false,
  visionSlowLoading: false,
  visionLastSuccessAt: 0,
  visionLatestReceivedAt: 0,
  visionAvailable: false,
  visionDrag: null,
  liveFrameAddress: "",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function init() {
  const streamHost = window.location.hostname || "127.0.0.1";
  const controlLink = $("#controlLink");
  if (controlLink) controlLink.href = `http://${streamHost}:8010/web/`;

  $("#cameraSelect").addEventListener("change", selectCamera);
  $("#cameraRefreshButton").addEventListener("click", loadCameras);
  $("#resetVisionTargetButton").addEventListener("click", resetVisionTarget);
  bindVisionSelection();
  $("#snapshotButton").addEventListener("click", takeSnapshot);
  $("#recordButton").addEventListener("click", toggleRecording);
  $("#refreshButton").addEventListener("click", () => loadMedia(true));
  $("#loadMoreButton").addEventListener("click", () => loadMedia(false));
  $("#dialogClose").addEventListener("click", closeDialog);
  $("#mediaDialog").addEventListener("click", (event) => {
    if (event.target === $("#mediaDialog")) closeDialog();
  });
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.mediaType = tab.dataset.type;
      $$(".tab").forEach((item) => {
        const selected = item === tab;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-selected", String(selected));
      });
      loadMedia(true);
    });
  });

  window.lucide?.createIcons();
  loadCameras();
  refreshStatus();
  refreshVisionLatest();
  refreshVisionService();
  loadMedia(true);
  openRequestedMedia();
  setInterval(refreshStatus, 1000);
  setInterval(() => {
    if (!document.hidden) refreshVisionLatest();
  }, 100);
  setInterval(() => {
    if (!document.hidden) refreshVisionService();
  }, 2000);
  setInterval(renderTimer, 250);
  window.addEventListener("resize", () => {
    syncVisionOverlayGeometry();
    renderVisionOverlay(state.visionLatest);
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Accept": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      if (body.detail) message = body.detail;
    } catch (_) {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

async function refreshStatus() {
  try {
    state.status = await api("/api/v1/status");
    renderStatus(state.status);
  } catch (error) {
    renderOffline(error.message);
  }
}

function renderStatus(data) {
  const online = Boolean(data.camera?.online);
  $("#onlineDot").classList.toggle("online", online);
  const cameraName = data.camera?.device || "摄像头";
  $("#cameraState").textContent = online ? `${cameraName} 在线` : `${cameraName} 离线`;
  $("#formatState").textContent = `${data.camera.width} × ${data.camera.height} · ${formatFps(data.camera.fps)} FPS`;
  $("#diskState").textContent = `${formatBytes(data.storage.free_bytes)} 可用`;
  $("#videoOffline").hidden = online;
  if (!online) $("#offlineMessage").textContent = data.camera.last_error || "正在等待重新连接";

  const active = Boolean(data.recording?.active);
  state.recordingStartedAt = active && data.recording.started_at ? new Date(data.recording.started_at) : null;
  $("#recordButton").classList.toggle("active", active);
  $("#recordButton").setAttribute("aria-pressed", String(active));
  $("#recordLabel").textContent = active ? "停止录像" : "开始录像";
  $("#recordIcon").setAttribute("data-lucide", active ? "square" : "circle");
  $(".record-timer").classList.toggle("active", active);
  $("#cameraSelect").disabled = active || state.switchingCamera;
  $("#cameraRefreshButton").disabled = state.switchingCamera;
  refreshLiveFrame(data);
  window.lucide?.createIcons();
}

function renderOffline(message) {
  $("#onlineDot").classList.remove("online");
  $("#cameraState").textContent = "服务离线";
  $("#videoOffline").hidden = false;
  $("#offlineMessage").textContent = message;
}

function refreshLiveFrame(status = state.status) {
  const streamHost = window.location.hostname || "127.0.0.1";
  const configuredPort = Number(status?.stream?.webrtc_port);
  const streamPort = Number.isInteger(configuredPort) && configuredPort > 0 && configuredPort <= 65535
    ? configuredPort
    : 8889;
  const streamPath = String(status?.stream?.path || "armcam")
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/") || "armcam";
  const address = `http://${streamHost}:${streamPort}/${streamPath}`;
  if (state.liveFrameAddress === address) return;
  state.liveFrameAddress = address;
  const query = new URLSearchParams({
    controls: "false",
    muted: "true",
    autoplay: "true",
    playsInline: "true",
    stream: String(Date.now()),
  });
  $("#liveFrame").src = `${address}?${query}`;
}

async function refreshVisionLatest() {
  if (state.visionFastLoading) return;
  state.visionFastLoading = true;
  try {
    const latest = await api("/api/v1/vision/latest");
    state.visionLatest = latest;
    state.visionLatestReceivedAt = Date.now();
    state.visionLastSuccessAt = Date.now();
    renderVisionLatest(latest);
  } catch (error) {
    if (Date.now() - state.visionLastSuccessAt > 750) renderVisionUnavailable(error.message);
  } finally {
    state.visionFastLoading = false;
  }
}

async function refreshVisionService() {
  if (state.visionSlowLoading) return;
  state.visionSlowLoading = true;
  try {
    const [healthResult, statusResult, targetResult] = await Promise.allSettled([
      api("/api/v1/vision/health"),
      api("/api/v1/vision/status"),
      api("/api/v1/vision/target/state"),
    ]);
    if (statusResult.status === "fulfilled") {
      state.visionRuntime = statusResult.value;
      renderVictorySnapshot(null);
    }
    if (targetResult.status === "fulfilled") state.visionTarget = targetResult.value;
    if (healthResult.status === "fulfilled") {
      state.visionLastSuccessAt = Date.now();
      const health = healthResult.value;
      state.visionHealth = health;
      if (health.running === false || health.camera_available === false) {
        renderVisionWarning(health.running === false ? "视觉未运行" : "视觉无画面");
      }
    } else if (Date.now() - state.visionLastSuccessAt > 1500) {
      renderVisionUnavailable(healthResult.reason?.message || "视觉服务不可用");
    }
    if (state.visionLatest) renderVisionLatest(state.visionLatest);
  } finally {
    state.visionSlowLoading = false;
  }
}

function renderVisionLatest(latest) {
  const camera = latest.camera || {};
  const cameraAvailable = camera.available !== false
    && camera.opened !== false
    && state.visionHealth?.running !== false
    && state.visionHealth?.camera_available !== false;
  const frameAgeSec = visionFrameAgeSec(latest);
  const processingSec = visionProcessingSec(latest);
  const dropped = visionDroppedFrames(latest);
  const stale = Number.isFinite(frameAgeSec) && frameAgeSec > 0.25;
  const usable = cameraAvailable && !stale;
  state.visionAvailable = usable;

  $("#visionDot").className = `vision-dot ${cameraAvailable && !stale ? "online" : "warning"}`;
  $("#visionState").textContent = cameraAvailable ? (stale ? "视觉帧过期" : "视觉在线") : "视觉无画面";
  const hasTarget = Boolean(latest.has_target ?? latest.detected ?? state.visionTarget?.has_target);
  const targetSource = latest.target_source || state.visionTarget?.target_source || "none";
  $("#visionTargetState").textContent = hasTarget ? `目标 ${targetSource}` : "目标 --";
  $("#visionFrameAgeState").textContent = `帧龄 ${formatMilliseconds(frameAgeSec)}`;
  $("#visionLatencyState").textContent = `处理 ${formatMilliseconds(processingSec)}`;
  $("#visionDroppedState").textContent = `丢帧 ${formatCount(dropped)}`;
  renderVictorySnapshot(cameraAvailable && !stale ? latest : null);
  $("#resetVisionTargetButton").disabled = !usable;

  syncVisionOverlayGeometry();
  renderVisionOverlay(usable ? latest : null);
}

function renderVisionWarning(message) {
  state.visionAvailable = false;
  $("#visionDot").className = "vision-dot warning";
  $("#visionState").textContent = message;
}

function renderVisionUnavailable(message) {
  state.visionAvailable = false;
  $("#visionDot").className = "vision-dot offline";
  $("#visionState").textContent = "视觉离线";
  $("#visionTargetState").textContent = "目标 --";
  $("#visionFrameAgeState").textContent = "帧龄 --";
  $("#visionLatencyState").textContent = "处理 --";
  $("#visionDroppedState").textContent = "丢帧 --";
  renderVictorySnapshot(null);
  $("#visionTelemetry").title = message;
  $("#resetVisionTargetButton").disabled = true;
  renderVisionOverlay(null);
}

function renderVictorySnapshot(latest) {
  const telemetry = $("#victorySnapshotTelemetry");
  const snapshot = (
    latest?.victory_snapshot
    ?? latest?.gesture?.victory_snapshot
    ?? latest?.gesture?.snapshot
    ?? state.visionRuntime?.victory_snapshot
    ?? state.visionRuntime?.gesture?.victory_snapshot
    ?? state.visionRuntime?.gesture?.snapshot
  );
  if (!snapshot || typeof snapshot !== "object") {
    telemetry.hidden = true;
    return;
  }

  telemetry.hidden = false;
  const stateNode = $("#victorySnapshotState");
  const lastNode = $("#victorySnapshotLast");
  const cooldown = Math.max(0, Number(snapshot.cooldown_remaining_sec) || 0);
  const error = String(snapshot.last_error || snapshot.error || "").trim();
  const enabled = snapshot.enabled !== false;
  let status = "等待手放下";
  let mode = "waiting";
  if (!enabled) {
    status = "Victory 拍照未启用";
    mode = "disabled";
  } else if (snapshot.in_flight) {
    status = "Victory 拍照中";
    mode = "active";
  } else if (error) {
    status = "Victory 拍照失败";
    mode = "error";
  } else if (cooldown > 0) {
    status = `Victory 冷却 ${cooldown.toFixed(1)} s`;
    mode = "cooldown";
  } else if (snapshot.armed) {
    status = "Victory 已就绪";
    mode = "ready";
  } else if (snapshot.release_required) {
    status = "Victory 等待手放下";
  }
  stateNode.textContent = status;
  telemetry.dataset.state = mode;

  const lastSnapshot = snapshot.last_snapshot;
  if (error) {
    lastNode.textContent = error;
  } else if (lastSnapshot && typeof lastSnapshot === "object") {
    const label = lastSnapshot.download_name || lastSnapshot.id || "照片已保存";
    lastNode.textContent = `最近 ${label}`;
  } else if (snapshot.last_completed_at) {
    lastNode.textContent = `最近 ${formatClockTime(snapshot.last_completed_at)}`;
  } else {
    lastNode.textContent = "";
  }
  telemetry.title = [
    `armed=${Boolean(snapshot.armed)}`,
    `in_flight=${Boolean(snapshot.in_flight)}`,
    `cooldown=${cooldown.toFixed(1)}s`,
    error ? `error=${error}` : "",
  ].filter(Boolean).join(" · ");
}

function formatClockTime(value) {
  const timestamp = parseTimestamp(value);
  if (!Number.isFinite(timestamp)) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function visionFrameAgeSec(latest) {
  const direct = Number(latest.frame_age_sec ?? latest.source_frame_age_sec);
  if (Number.isFinite(direct)) {
    const cachedForSec = latest === state.visionLatest && state.visionLatestReceivedAt > 0
      ? Math.max(0, (Date.now() - state.visionLatestReceivedAt) / 1000)
      : 0;
    return Math.max(0, direct) + cachedForSec;
  }
  const receivedAt = parseTimestamp(
    latest.frame_received_at
      ?? latest.source_frame_received_at
      ?? latest.capture_timestamp
      ?? latest.captured_at
      ?? latest.timestamp
  );
  return Number.isFinite(receivedAt) ? Math.max(0, Date.now() / 1000 - receivedAt) : NaN;
}

function visionProcessingSec(latest) {
  const direct = Number(latest.processing_latency_sec ?? latest.processing_time_sec);
  if (Number.isFinite(direct)) return Math.max(0, direct);
  const milliseconds = Number(latest.processing_latency_ms);
  if (Number.isFinite(milliseconds)) return Math.max(0, milliseconds / 1000);
  const receivedAt = parseTimestamp(latest.frame_received_at ?? latest.source_frame_received_at);
  const processedAt = parseTimestamp(latest.processed_at ?? latest.timestamp);
  return Number.isFinite(receivedAt) && Number.isFinite(processedAt)
    ? Math.max(0, processedAt - receivedAt)
    : NaN;
}

function visionDroppedFrames(latest) {
  const value = latest.dropped_source_frames
    ?? latest.source_dropped_frames
    ?? latest.camera?.dropped_frames
    ?? state.visionRuntime?.dropped_source_frames
    ?? state.visionRuntime?.camera?.dropped_frames;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, Math.round(numeric)) : NaN;
}

function parseTimestamp(value) {
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return numeric;
  const milliseconds = Date.parse(value);
  return Number.isFinite(milliseconds) ? milliseconds / 1000 : NaN;
}

function formatMilliseconds(seconds) {
  return Number.isFinite(seconds) ? `${Math.round(seconds * 1000)} ms` : "--";
}

function formatCount(value) {
  return Number.isFinite(value) ? new Intl.NumberFormat("zh-CN").format(value) : "--";
}

function renderVisionOverlay(latest) {
  const overlay = $("#visionOverlay");
  overlay.replaceChildren();
  if (!latest) return;
  const { width, height } = visionFrameSize(latest);
  if (width <= 0 || height <= 0) return;

  overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const make = (tag, attributes) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    overlay.append(node);
    return node;
  };

  const offset = latest.offset || {};
  const desired = offset.desired_center || latest.desired_center;
  const deadZoneX = Number(offset.dead_zone_x_norm ?? latest.dead_zone_x_norm ?? 0.02) * width;
  const deadZoneY = Number(offset.dead_zone_y_norm ?? latest.dead_zone_y_norm ?? 0.025) * height;
  if (Array.isArray(desired) && desired.length >= 2) {
    make("rect", {
      class: "vision-dead-zone",
      x: Number(desired[0]) - deadZoneX,
      y: Number(desired[1]) - deadZoneY,
      width: Math.max(1, deadZoneX * 2),
      height: Math.max(1, deadZoneY * 2),
    });
    make("line", {
      class: "vision-desired-center",
      x1: Number(desired[0]) - width * 0.014,
      y1: desired[1],
      x2: Number(desired[0]) + width * 0.014,
      y2: desired[1],
    });
    make("line", {
      class: "vision-desired-center",
      x1: desired[0],
      y1: Number(desired[1]) - height * 0.025,
      x2: desired[0],
      y2: Number(desired[1]) + height * 0.025,
    });
  }

  const bbox = latest.bbox || latest.target?.bbox;
  if (Array.isArray(bbox) && bbox.length >= 4) {
    make("rect", {
      class: "vision-target-box",
      x: bbox[0],
      y: bbox[1],
      width: Math.max(1, Number(bbox[2]) || 0),
      height: Math.max(1, Number(bbox[3]) || 0),
    });
  }
  const center = latest.center || latest.target?.center || offset.target_center;
  if (Array.isArray(center) && center.length >= 2) {
    make("circle", { class: "vision-target-center", cx: center[0], cy: center[1], r: 5 });
  }
  renderVisionSelectionBox();
}

function visionFrameSize(latest = state.visionLatest || {}) {
  const width = Number(latest.camera?.width || 0);
  const height = Number(latest.camera?.height || 0);
  return { width, height };
}

function syncVisionOverlayGeometry() {
  const overlay = $("#visionOverlay");
  const shell = $(".video-shell");
  const { width, height } = visionFrameSize();
  if (!shell || width <= 0 || height <= 0) {
    overlay.removeAttribute("style");
    return;
  }
  const shellWidth = shell.clientWidth;
  const shellHeight = shell.clientHeight;
  const sourceRatio = width / height;
  const shellRatio = shellWidth / shellHeight;
  const displayWidth = shellRatio > sourceRatio ? shellHeight * sourceRatio : shellWidth;
  const displayHeight = shellRatio > sourceRatio ? shellHeight : shellWidth / sourceRatio;
  overlay.style.left = `${(shellWidth - displayWidth) / 2}px`;
  overlay.style.top = `${(shellHeight - displayHeight) / 2}px`;
  overlay.style.width = `${displayWidth}px`;
  overlay.style.height = `${displayHeight}px`;
}

function bindVisionSelection() {
  const overlay = $("#visionOverlay");
  overlay.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !state.visionLatest || !state.visionAvailable) return;
    const point = visionPointerPoint(event);
    if (!point) return;
    event.preventDefault();
    overlay.setPointerCapture?.(event.pointerId);
    state.visionDrag = { pointerId: event.pointerId, start: point, end: point };
    renderVisionOverlay(state.visionLatest);
  });
  overlay.addEventListener("pointermove", (event) => {
    if (state.visionDrag?.pointerId !== event.pointerId) return;
    const point = visionPointerPoint(event);
    if (!point) return;
    state.visionDrag.end = point;
    renderVisionOverlay(state.visionLatest);
  });
  overlay.addEventListener("pointerup", finishVisionSelection);
  overlay.addEventListener("pointercancel", clearVisionSelection);
}

function visionPointerPoint(event) {
  const overlay = $("#visionOverlay");
  const rect = overlay.getBoundingClientRect();
  const { width, height } = visionFrameSize();
  if (!rect.width || !rect.height || width <= 0 || height <= 0) return null;
  return {
    x: Math.max(0, Math.min(width, ((event.clientX - rect.left) / rect.width) * width)),
    y: Math.max(0, Math.min(height, ((event.clientY - rect.top) / rect.height) * height)),
  };
}

function renderVisionSelectionBox() {
  const drag = state.visionDrag;
  if (!drag) return;
  const node = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  node.setAttribute("id", "visionSelectionBox");
  node.setAttribute("class", "vision-selection-box");
  node.setAttribute("x", String(Math.min(drag.start.x, drag.end.x)));
  node.setAttribute("y", String(Math.min(drag.start.y, drag.end.y)));
  node.setAttribute("width", String(Math.abs(drag.end.x - drag.start.x)));
  node.setAttribute("height", String(Math.abs(drag.end.y - drag.start.y)));
  $("#visionOverlay").append(node);
}

async function finishVisionSelection(event) {
  const drag = state.visionDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  const body = visionSelectionBody(drag);
  clearVisionSelection();
  if (!body) {
    toast("框选区域太小", true);
    return;
  }
  try {
    const result = await api("/api/v1/vision/target/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (result.ok === false) throw new Error(result.message || "主体框选失败");
    toast(result.message || "主体已框选");
    await Promise.all([refreshVisionLatest(), refreshVisionService()]);
  } catch (error) {
    toast(error.message, true);
  }
}

function visionSelectionBody(drag) {
  const { width, height } = visionFrameSize();
  const x = Math.max(0, Math.round(Math.min(drag.start.x, drag.end.x)));
  const y = Math.max(0, Math.round(Math.min(drag.start.y, drag.end.y)));
  const w = Math.min(width - x, Math.round(Math.abs(drag.end.x - drag.start.x)));
  const h = Math.min(height - y, Math.round(Math.abs(drag.end.y - drag.start.y)));
  return w >= 8 && h >= 8 ? { x, y, w, h } : null;
}

function clearVisionSelection() {
  state.visionDrag = null;
  renderVisionOverlay(state.visionLatest);
}

async function resetVisionTarget() {
  const button = $("#resetVisionTargetButton");
  button.disabled = true;
  try {
    const result = await api("/api/v1/vision/target/reset", { method: "POST" });
    toast(result.message || "视觉主体已重置");
    await Promise.all([refreshVisionLatest(), refreshVisionService()]);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = !state.visionAvailable;
  }
}

async function loadCameras() {
  const select = $("#cameraSelect");
  const refresh = $("#cameraRefreshButton");
  refresh.disabled = true;
  try {
    const payload = await api("/api/v1/cameras");
    state.cameras = payload.items || [];
    state.selectedCameraId = payload.selected_id || null;
    select.replaceChildren();
    if (!state.cameras.length) {
      const option = new Option("没有检测到摄像头", "");
      select.append(option);
      select.disabled = true;
      return;
    }
    state.cameras.forEach((camera) => {
      const option = new Option(camera.name, camera.id);
      option.selected = camera.id === state.selectedCameraId;
      select.append(option);
    });
    select.disabled = Boolean(state.status?.recording?.active) || state.switchingCamera;
  } catch (error) {
    select.replaceChildren(new Option("读取摄像头失败", ""));
    select.disabled = true;
    toast(error.message, true);
  } finally {
    refresh.disabled = false;
  }
}

async function selectCamera(event) {
  const select = event.currentTarget;
  const cameraId = select.value;
  if (!cameraId || cameraId === state.selectedCameraId) return;
  state.switchingCamera = true;
  select.disabled = true;
  $("#cameraRefreshButton").disabled = true;
  $("#snapshotButton").disabled = true;
  $("#recordButton").disabled = true;
  try {
    const selected = await api("/api/v1/camera", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: cameraId }),
    });
    state.selectedCameraId = selected.id;
    toast(`已切换到 ${selected.name}`);
    await refreshStatus();
    await loadCameras();
  } catch (error) {
    select.value = state.selectedCameraId || "";
    toast(error.message, true);
  } finally {
    state.switchingCamera = false;
    $("#snapshotButton").disabled = false;
    $("#recordButton").disabled = false;
    $("#cameraRefreshButton").disabled = false;
    select.disabled = Boolean(state.status?.recording?.active);
  }
}

function renderTimer() {
  if (!state.recordingStartedAt) {
    $("#recordTime").textContent = "00:00:00";
    return;
  }
  const seconds = Math.max(0, Math.floor((Date.now() - state.recordingStartedAt.getTime()) / 1000));
  const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const remaining = String(seconds % 60).padStart(2, "0");
  $("#recordTime").textContent = `${hours}:${minutes}:${remaining}`;
}

async function takeSnapshot() {
  const button = $("#snapshotButton");
  button.disabled = true;
  try {
    await api("/api/v1/snapshots", { method: "POST" });
    toast("照片已保存");
    await loadMedia(true);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function toggleRecording() {
  const button = $("#recordButton");
  button.disabled = true;
  try {
    const active = Boolean(state.status?.recording?.active);
    await api(active ? "/api/v1/recordings/stop" : "/api/v1/recordings/start", { method: "POST" });
    toast(active ? "录像已保存" : "录像已开始");
    await refreshStatus();
    if (active) await loadMedia(true);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadMedia(reset) {
  if (state.loading) return;
  state.loading = true;
  if (reset) state.cursor = null;
  $("#refreshButton").disabled = true;
  try {
    const query = new URLSearchParams({ limit: "50" });
    if (state.mediaType) query.set("type", state.mediaType);
    if (state.cursor) query.set("cursor", state.cursor);
    const payload = await api(`/api/v1/media?${query}`);
    renderMedia(payload.items, reset);
    state.cursor = payload.next_cursor;
    $("#loadMoreButton").hidden = !state.cursor;
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.loading = false;
    $("#refreshButton").disabled = false;
  }
}

async function openRequestedMedia() {
  const mediaId = new URLSearchParams(window.location.search).get("media");
  if (!mediaId) return;
  try {
    const item = await api(`/api/v1/media/${encodeURIComponent(mediaId)}`);
    openMedia(item);
  } catch (error) {
    toast(`无法打开指定录像：${error.message}`, true);
  }
}

function renderMedia(items, reset) {
  const grid = $("#mediaGrid");
  if (reset) grid.replaceChildren();
  items.forEach((item) => grid.append(mediaElement(item)));
  const count = grid.children.length;
  $("#emptyState").hidden = count > 0;
  $("#librarySummary").textContent = count
    ? `当前显示 ${count} 个文件 · 保存在当前主机`
    : "照片和录像保存在当前主机";
  window.lucide?.createIcons();
}

function mediaElement(item) {
  const article = document.createElement("article");
  article.className = "media-item";
  const preview = document.createElement("button");
  preview.className = "media-preview";
  preview.type = "button";
  preview.setAttribute("aria-label", `打开 ${item.download_name}`);
  preview.addEventListener("click", () => openMedia(item));

  const image = document.createElement("img");
  image.src = item.thumbnail_url;
  image.alt = "";
  image.loading = "lazy";
  preview.append(image);

  if (item.type === "recording") {
    const type = document.createElement("span");
    type.className = "media-type";
    type.innerHTML = `<i data-lucide="play" aria-hidden="true"></i>${formatDuration(item.duration_sec)}`;
    preview.append(type);
  }

  const meta = document.createElement("div");
  meta.className = "media-meta";
  const timestamp = document.createElement("span");
  timestamp.textContent = formatDate(item.created_at);
  timestamp.title = item.download_name;
  const download = document.createElement("a");
  download.className = "media-download icon-button";
  download.href = `${item.content_url}?download=true`;
  download.title = "下载";
  download.setAttribute("aria-label", `下载 ${item.download_name}`);
  download.innerHTML = `<i data-lucide="download" aria-hidden="true"></i>`;
  meta.append(timestamp, download);
  article.append(preview, meta);
  return article;
}

function openMedia(item) {
  const dialog = $("#mediaDialog");
  $("#dialogTitle").textContent = item.download_name;
  $("#dialogDownload").href = `${item.content_url}?download=true`;
  const content = $("#dialogContent");
  content.replaceChildren();
  if (item.type === "snapshot") {
    const image = document.createElement("img");
    image.src = item.content_url;
    image.alt = item.download_name;
    content.append(image);
  } else {
    const video = document.createElement("video");
    video.src = item.content_url;
    video.controls = true;
    video.autoplay = true;
    video.playsInline = true;
    content.append(video);
  }
  dialog.showModal();
}

function closeDialog() {
  $("#dialogContent video")?.pause();
  $("#mediaDialog").close();
  $("#dialogContent").replaceChildren();
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit >= 3 ? 1 : 0)} ${units[unit]}`;
}

function formatDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value) || 0));
  const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
  const remaining = String(seconds % 60).padStart(2, "0");
  return `${minutes}:${remaining}`;
}

function formatFps(value) {
  const text = String(value ?? "");
  if (text.includes("/")) {
    const [numerator, denominator] = text.split("/").map(Number);
    if (Number.isFinite(numerator) && Number.isFinite(denominator) && denominator !== 0) {
      return (numerator / denominator).toFixed(denominator === 1 ? 0 : 1);
    }
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(Number.isInteger(numeric) ? 0 : 1) : "--";
}

function formatDate(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

let toastTimer = null;
function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("visible"), 2600);
}

window.addEventListener("DOMContentLoaded", init);
