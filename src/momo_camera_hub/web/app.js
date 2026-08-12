const state = {
  status: null,
  mediaType: "",
  cursor: null,
  loading: false,
  recordingStartedAt: null,
  cameras: [],
  selectedCameraId: null,
  switchingCamera: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function init() {
  refreshLiveFrame();
  const streamHost = window.location.hostname || "127.0.0.1";
  const controlLink = $("#controlLink");
  if (controlLink) controlLink.href = `http://${streamHost}:8110/`;

  $("#cameraSelect").addEventListener("change", selectCamera);
  $("#cameraRefreshButton").addEventListener("click", loadCameras);
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
  loadMedia(true);
  openRequestedMedia();
  setInterval(refreshStatus, 1000);
  setInterval(renderTimer, 250);
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
  window.lucide?.createIcons();
}

function renderOffline(message) {
  $("#onlineDot").classList.remove("online");
  $("#cameraState").textContent = "服务离线";
  $("#videoOffline").hidden = false;
  $("#offlineMessage").textContent = message;
}

function refreshLiveFrame() {
  const streamHost = window.location.hostname || "127.0.0.1";
  const query = new URLSearchParams({
    controls: "false",
    muted: "true",
    autoplay: "true",
    playsInline: "true",
    stream: String(Date.now()),
  });
  $("#liveFrame").src = `http://${streamHost}:8889/armcam?${query}`;
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
    refreshLiveFrame();
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
