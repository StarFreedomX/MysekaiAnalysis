import { credentials } from "/auth.js";
const $ = (id) => document.getElementById(id);
const tabs = [...document.querySelectorAll('[role="tab"]')];
const state = { snapshot: null, auth: null, site: 5, generation: 0, images: new Map(), maxBytes: 100000000 };

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
  $("notice").classList.toggle("error", error);
}
function readAuth() {
  const uid = $("uid").value.trim();
  const password = $("password").value;
  let field, message;
  if (!/^[0-9]{6,25}$/.test(uid)) {
    field = $("uid");
    message = uid ? "UID 应为 6～25 位数字。" : "请先填写游戏 UID。";
  } else if (!/^[0-9]{6}$/.test(password)) {
    field = $("password");
    message = password ? "密码应为 6 位数字。" : "请填写密码。";
  }
  [$("uid"), $("password")].forEach(input => input.removeAttribute("aria-invalid"));
  if (field) {
    $("key-error").textContent = message;
    $("key-error").hidden = false;
    field.setAttribute("aria-invalid", "true");
    field.focus();
    return null;
  }
  $("key-error").hidden = true;
  return credentials(uid, password);
}
async function api(path, auth, options = {}) {
  const response = await fetch(path, { ...options, cache: "no-store", headers: {
    Authorization: `Bearer ${auth.token}`, "X-User-Id": auth.uid, ...(options.headers || {})
  }});
  if (!response.ok) {
    let result;
    try { result = await response.json(); } catch (_) { result = {}; }
    const error = new Error(result.message || `请求失败（${response.status}），请重试。`);
    error.code = result.error;
    throw error;
  }
  return response;
}
function clearSnapshot() {
  state.generation++;
  state.snapshot = null;
  state.auth = null;
  for (const url of state.images.values()) URL.revokeObjectURL(url);
  state.images.clear();
  $("map-image").removeAttribute("src");
  $("large-image").removeAttribute("src");
  $("download-map").removeAttribute("href");
  if ($("image-dialog").open) $("image-dialog").close();
  $("snapshot-bar").hidden = true;
  $("map-result").hidden = true;
  $("empty-state").hidden = false;
  $("empty-title").textContent = "尚无地图";
  $("empty-description").textContent = "输入 UID 和密码查询，或上传数据。";
  $("refresh-button").disabled = true;
  tabs.forEach(tab => tab.classList.remove("has-data"));
}
function setSnapshot(data, auth) {
  clearSnapshot();
  state.snapshot = data;
  state.auth = auth;
  $("snapshot-bar").hidden = false;
  $("snapshot-account").textContent = `UID ${data.uid}`;
  $("snapshot-time").textContent = `更新于 ${new Date(data.updated_at).toLocaleString("zh-CN", {hour12: false})}`;
  $("diamond-total").textContent = `钻石 × ${data.sites.reduce((total, site) => total + site.diamonds, 0)}`;
  $("refresh-button").disabled = false;
  tabs.forEach(tab => tab.classList.toggle("has-data", data.sites.some(site => site.id === Number(tab.dataset.site))));
  if (!data.sites.some(site => site.id === state.site)) state.site = data.sites[0].id;
}
async function showSite(siteId, auth) {
  state.site = siteId;
  tabs.forEach(tab => {
    const selected = Number(tab.dataset.site) === siteId;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  $("map-panel").setAttribute("aria-labelledby", `tab-${siteId}`);
  if (!state.snapshot) return;
  const site = state.snapshot.sites.find(item => item.id === siteId);
  $("map-result").hidden = true;
  $("empty-state").hidden = false;
  if (!site) {
    $("empty-title").textContent = "这次上传没有这张地图";
    $("empty-description").textContent = "重新进入游戏并同步全量数据后再查看。";
    return;
  }
  $("empty-title").textContent = "正在加载地图…";
  $("empty-description").textContent = "";
  const generation = state.generation;
  try {
    let url = state.images.get(siteId);
    if (!url) {
      const response = await api(site.image_url, auth);
      const blob = await response.blob();
      if (generation !== state.generation) return;
      url = URL.createObjectURL(blob);
      const previous = state.images.get(siteId);
      if (previous) URL.revokeObjectURL(previous);
      state.images.set(siteId, url);
    }
    if (generation !== state.generation || state.site !== siteId) return;
    $("map-image").src = url;
    $("map-image").alt = `${site.name}资源地图`;
    $("map-name").textContent = site.name;
    $("map-summary").textContent = `钻石 × ${site.diamonds} · ${site.resource_entries} 条资源记录`;
    $("download-map").href = url;
    $("download-map").download = `mysekai-${state.snapshot.uid}-${siteId}.png`;
    $("map-result").hidden = false;
    $("empty-state").hidden = true;
  } catch (error) {
    if (generation !== state.generation || state.site !== siteId) return;
    $("empty-title").textContent = "地图加载失败";
    $("empty-description").textContent = "请点击刷新地图重试。";
    notice(error.message, true);
  }
}
async function query() {
  const auth = readAuth();
  if (!auth) return;
  clearSnapshot();
  const generation = state.generation;
  $("query-button").disabled = true;
  $("query-button").textContent = "正在查询…";
  notice("");
  try {
    const result = await (await api("/api/maps", auth)).json();
    if (generation !== state.generation) return;
    setSnapshot(result.data, auth);
    await showSite(state.site, auth);
  } catch (error) {
    if (generation !== state.generation) return;
    if (error.code === "no_data") {
      $("empty-title").textContent = "没有找到地图";
      $("empty-description").textContent = "请检查 UID 和密码，或先上传数据。";
    }
    if (error.code !== "no_data") notice(error.message, true);
  } finally {
    $("query-button").disabled = false;
    $("query-button").textContent = "查看地图";
  }
}
$("access-form").addEventListener("submit", event => {event.preventDefault(); query();});
$("refresh-button").addEventListener("click", query);
[$("uid"), $("password")].forEach(input => input.addEventListener("input", () => {
  clearSnapshot(); notice("");
  $("key-error").hidden = true;
  [$("uid"), $("password")].forEach(field => field.removeAttribute("aria-invalid"));
}));
$("key-toggle").addEventListener("click", () => {
  const show = $("password").type === "password";
  $("password").type = show ? "text" : "password";
  $("key-toggle").textContent = show ? "隐藏" : "显示";
  $("key-toggle").setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
});
tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => showSite(Number(tab.dataset.site), state.auth));
  tab.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 :
      (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus(); tabs[next].click();
  });
});
function openUpload() {
  if (!readAuth()) return;
  $("upload-error").textContent = "";
  $("upload-dialog").showModal();
}
$("upload-open").addEventListener("click", openUpload);
$("upload-file").addEventListener("change", () => {
  $("file-name").textContent = $("upload-file").files[0]?.name || ".bin 或 .json";
});
$("upload-form").addEventListener("submit", async event => {
  event.preventDefault();
  const auth = readAuth();
  const file = $("upload-file").files[0];
  const generation = state.generation;
  if (!auth || !file) return;
  if (!file.size || file.size > state.maxBytes) {
    $("upload-error").textContent = `请选择非空文件，大小不超过 ${Math.round(state.maxBytes / 1000000)} MB。`;
    return;
  }
  $("upload-button").disabled = true;
  $("upload-button").textContent = "正在上传并生成地图…";
  $("upload-error").textContent = "";
  try {
    const result = await (await api("/api/upload", auth, {method: "POST", body: file,
      headers: {"Content-Type": "application/octet-stream", "X-Region": $("region").value}})).json();
    $("upload-dialog").close();
    if (generation !== state.generation) return;
    setSnapshot(result.data, auth);
    notice("地图已更新。");
    await showSite(state.site, auth);
    $("upload-form").reset();
    $("file-name").textContent = `.bin 或 .json · 最大 ${Math.round(state.maxBytes / 1000000)} MB`;
  } catch (error) { $("upload-error").textContent = error.message; }
  finally { $("upload-button").disabled = false; $("upload-button").textContent = "上传并生成地图"; }
});
$("sync-open").addEventListener("click", async () => {
  if (!readAuth()) return;
  $("sync-error").textContent = "";
  $("sync-dialog").showModal();
  try {
    const result = await (await fetch("/api/config", {cache: "no-store"})).json();
    const origin = result.data.public_base_url;
    $("sync-origin").textContent = origin;
    $("sync-hint").textContent = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(new URL(origin).hostname)
      ? "当前地址只能在服务器本机访问。手机同步前，需要将服务配置中的 PUBLIC_BASE_URL 改为手机能访问的地址。"
      : "请确认手机可以访问这个地址。";
  } catch (_) { $("sync-error").textContent = "读取同步地址失败，请稍后重试。"; }
});
$("download-module").addEventListener("click", async () => {
  const auth = readAuth();
  if (!auth) return;
  $("download-module").disabled = true;
  try {
    const response = await api("/api/module", auth);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url; link.download = "mysekai.module";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch (error) { $("sync-error").textContent = error.message; }
  finally { $("download-module").disabled = false; }
});
$("expand-map").addEventListener("click", () => {
  $("large-image").src = $("map-image").src;
  $("image-dialog").showModal();
});
fetch("/api/config").then(response => response.json()).then(result => {
  state.maxBytes = result.data.max_upload_bytes;
  $("file-name").textContent = `.bin 或 .json · 最大 ${Math.round(state.maxBytes / 1000000)} MB`;
}).catch(() => {});
