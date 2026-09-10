/* 今日事 - 前端逻辑：日历渲染 / 快速添加 / 提醒轮询 / 编辑弹窗 */
const $ = (s) => document.querySelector(s);

const state = {
  year: 0, month: 0,            // 当前展示的月份（month: 1-12）
  selected: "",                 // 选中的日期 YYYY-MM-DD
  todos: [],                    // 当月待办
  tags: [],                     // [{name, color}]
  editId: null,                 // 正在编辑的待办 id（null = 新建）
};

/* ---------------- 基础工具 ---------------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error((await res.json()).error || "请求失败");
  return res.json();
}

const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const WEEK_CN = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function fmtCN(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  return { md: `${m}月${d}日`, week: WEEK_CN[new Date(y, m - 1, d).getDay()] };
}

function toast(html, cls = "", actions = null) {
  const el = document.createElement("div");
  el.className = `toast ${cls}`;
  el.innerHTML = html;
  if (actions && actions.length) {
    const bar = document.createElement("div");
    bar.className = "toast-actions";
    actions.forEach((a) => {
      const b = document.createElement("button");
      b.className = "toast-btn" + (a.primary ? " primary" : "");
      b.textContent = a.label;
      b.onclick = () => { el.remove(); a.onClick && a.onClick(); };
      bar.appendChild(b);
    });
    el.appendChild(bar);
  }
  $("#toasts").appendChild(el);
  if (!actions) setTimeout(() => el.remove(), 5000);  // 带按钮的提示需用户选择，不自动消失
  return el;
}

/* ---------------- 日历 ---------------- */
async function loadMonth() {
  const ym = `${state.year}-${pad(state.month)}`;
  state.todos = await api(`/api/todos?month=${ym}`);
  renderCalendar();
  renderDay();
}

function todosOf(dateStr) {
  return state.todos.filter((t) => t.date === dateStr);
}

function renderCalendar() {
  $("#monthLabel").textContent = `${state.year}年${state.month}月`;
  const grid = $("#calGrid");
  grid.innerHTML = "";

  const first = new Date(state.year, state.month - 1, 1);
  const offset = (first.getDay() + 6) % 7;          // 周一开头
  const todayStr = iso(new Date());

  for (let i = 0; i < 42; i++) {
    const d = new Date(state.year, state.month - 1, 1 - offset + i);
    const dStr = iso(d);
    const cell = document.createElement("div");
    cell.className = "cell" + (d.getMonth() + 1 !== state.month ? " other" : "") +
      (dStr === todayStr ? " today" : "") + (dStr === state.selected ? " selected" : "");

    const dayTodos = todosOf(dStr);
    const undone = dayTodos.filter((t) => !t.done).length;
    cell.innerHTML = `<span class="d-num">${d.getDate()}</span>` +
      (undone ? `<span class="d-count">${undone}</span>` : "");

    dayTodos.slice(0, 3).forEach((t) => {
      const chip = document.createElement("div");
      chip.className = "d-chip" + (t.important ? " imp" : "");
      chip.style.setProperty("--c", t.color);
      const time = t.remind_at ? ` ${t.remind_at.slice(11, 16)}` : "";
      chip.innerHTML = `<span class="dot"></span>${t.important ? "★" : ""}${t.title}${time}`;
      chip.title = `${t.title}${time}【${t.tag}】${t.important ? " 重要" : ""}`;
      cell.appendChild(chip);
    });
    if (dayTodos.length > 3) {
      const more = document.createElement("div");
      more.className = "d-more";
      more.textContent = `+${dayTodos.length - 3} 项`;
      cell.appendChild(more);
    }

    cell.onclick = () => { state.selected = dStr; renderCalendar(); renderDay(); };
    grid.appendChild(cell);
  }
}

/* ---------------- 当日面板 ---------------- */
function renderDay() {
  const { md, week } = fmtCN(state.selected);
  $("#dayTitle").textContent = `${md} ${week}`;
  const list = todosOf(state.selected);
  $("#dayCount").textContent = `${list.length} 项 · ${list.filter((t) => !t.done).length} 待完成`;
  $("#dayEmpty").classList.toggle("hidden", list.length > 0);

  const box = $("#dayList");
  box.innerHTML = "";
  list.forEach((t) => {
    const el = document.createElement("div");
    el.className = "todo" + (t.done ? " done" : "") + (t.important ? " imp" : "");
    const time = t.remind_at ? `⏰ ${t.remind_at.slice(5, 16).replace(" ", " ")}` : "";
    el.innerHTML = `
      <button class="t-check" title="完成">✓</button>
      <div class="t-body">
        <div class="t-title">${escapeHtml(t.title)}${t.important ? '<span class="t-star">★</span>' : ""}</div>
        <div class="t-meta">
          <span class="t-tag" style="--c:${t.color}">${t.tag}</span>
          ${time ? `<span class="t-time">${time}</span>` : ""}
        </div>
        ${t.note ? `<div class="t-note">${escapeHtml(t.note)}</div>` : ""}
      </div>
      <div class="t-actions">
        <button class="act-imp ${t.important ? "on" : ""}" title="重要标识">★</button>
        <button class="act-edit" title="编辑">✎</button>
        <button class="act-del" title="删除">🗑</button>
      </div>`;
    el.querySelector(".t-check").onclick = async () => {
      await api(`/api/todos/${t.id}`, { method: "PATCH", body: JSON.stringify({ done: !t.done }) });
      loadMonth();
    };
    el.querySelector(".act-imp").onclick = async () => {
      await api(`/api/todos/${t.id}`, { method: "PATCH", body: JSON.stringify({ important: !t.important }) });
      loadMonth();
    };
    el.querySelector(".act-edit").onclick = () => openModal(t);
    el.querySelector(".act-del").onclick = async () => {
      if (!confirm(`删除「${t.title}」？`)) return;
      await api(`/api/todos/${t.id}`, { method: "DELETE" });
      toast("已删除", "ok");
      loadMonth();
    };
    box.appendChild(el);
  });
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 快速添加 ---------------- */
let previewTimer = null;

function renderPreview(p) {
  const box = $("#preview");
  const chips = [];
  if (p.remind_at) {
    chips.push(`<span class="chip">⏰ 提醒：${p.remind_at.slice(5, 16)}</span>`);
  } else if (p.date) {
    chips.push(`<span class="chip">📅 ${fmtCN(p.date).md}（${fmtCN(p.date).week}）</span>`);
  } else {
    chips.push(`<span class="chip hint-chip">📅 默认添加到今天</span>`);
  }
  chips.push(`<span class="chip tag-chip" style="--c:${p.color}">🏷 ${p.tag}</span>`);
  if (p.important) chips.push(`<span class="chip imp-chip">⭐ 重要事项</span>`);
  box.innerHTML = chips.join("");
  box.classList.remove("hidden");
}

async function quickAdd() {
  const input = $("#quickInput");
  const text = input.value.trim();
  if (!text) return;
  const todo = await api("/api/todos", {
    method: "POST",
    body: JSON.stringify({ title: text }),
  });
  input.value = "";
  $("#preview").classList.add("hidden");
  afterCreated(todo);
}

/* 创建成功后的统一处理：跳转日期 + 刷新 + 未设提醒时提示 */
function afterCreated(todo) {
  askNotifyPermission();
  const { md } = fmtCN(todo.date);
  // 跳转到待办所在月份
  const [y, m] = todo.date.split("-").map(Number);
  if (y !== state.year || m !== state.month) { state.year = y; state.month = m; }
  state.selected = todo.date;
  loadMonth();

  if (todo.remind_at) {
    toast(`已添加到 <b>${md}</b>，提醒 ${todo.remind_at.slice(11, 16)}<br>${escapeHtml(todo.title)}`, "ok");
    return;
  }
  // 没有提醒时间 -> 屏幕中央弹出询问框
  $("#askDate").textContent = `${md}（${fmtCN(todo.date).week}）`;
  $("#askTitle").textContent = todo.title;
  $("#askMask").classList.remove("hidden");
  askTodo = todo;
}

/* 居中提醒询问框 */
let askTodo = null;
function closeAsk() { $("#askMask").classList.add("hidden"); askTodo = null; }

/* ---------------- 提醒轮询 ---------------- */
function askNotifyPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
}

async function pollReminders() {
  try {
    const list = await api("/api/reminders/pending");
    if (list.length) {
      list.forEach((t) => {
        toast(`🔔 <b>提醒</b><br>${escapeHtml(t.title)}<br><small>${t.remind_at}</small>`,
              t.important ? "imp" : "");
        if ("Notification" in window && Notification.permission === "granted") {
          new Notification("今日事 · 到点提醒", {
            body: `${t.important ? "⭐ " : ""}${t.title}`,
            tag: `todo-${t.id}`,
          });
        }
      });
      await api("/api/reminders/ack", {
        method: "POST",
        body: JSON.stringify({ ids: list.map((t) => t.id) }),
      });
    }
  } catch (e) { /* 静默重试 */ }
}

/* ---------------- 编辑弹窗 ---------------- */
function fillTagOptions() {
  $("#fTag").innerHTML = state.tags
    .map((t) => `<option value="${t.name}">${t.name}</option>`).join("");
}

function openModal(todo = null, focusRemind = false) {
  state.editId = todo ? todo.id : null;
  $("#modalTitle").textContent = todo ? "编辑待办" : `添加待办 · ${fmtCN(state.selected).md}`;
  $("#fTitle").value = todo ? todo.title : "";
  $("#fDate").value = todo ? todo.date : state.selected;
  $("#fRemind").value = todo && todo.remind_at ? todo.remind_at.replace(" ", "T") : "";
  $("#fNote").value = todo ? todo.note : "";
  $("#fTag").value = todo ? todo.tag : state.tags[0]?.name || "其他";
  $("#fImportant").checked = todo ? todo.important : false;
  $("#modalMask").classList.remove("hidden");
  if (focusRemind) {
    // 从“设置提醒”提示进入：预填当天 09:00 并聚焦，方便直接调整
    if (!$("#fRemind").value) $("#fRemind").value = `${$("#fDate").value}T09:00`;
    setTimeout(() => $("#fRemind").focus(), 60);
  } else {
    $("#fTitle").focus();
  }
}

function closeModal() { $("#modalMask").classList.add("hidden"); }

/* ---------------- 背景设置 ---------------- */
const BG_PRESETS = [
  { name: "默认", bg: "#f4f5fb" },
  { name: "护眼", bg: "#f5f1e4" },
  { name: "浅蓝", bg: "#e8f1fb" },
  { name: "薄荷", bg: "#e6f6ef" },
  { name: "樱粉", bg: "#fceef3" },
  { name: "蓝紫", bg: "linear-gradient(135deg, #667eea, #764ba2)" },
  { name: "晚霞", bg: "linear-gradient(135deg, #ff9a62, #ef4e7b)" },
  { name: "夜空", bg: "linear-gradient(135deg, #232a4d, #414b80)" },
];
const BG_KEY = "today_bg";

function applyBg(bg) {
  document.body.style.background = bg;
  document.body.style.backgroundAttachment = "fixed";
  try { localStorage.setItem(BG_KEY, bg); } catch (e) { /* 隐私模式忽略 */ }
  document.querySelectorAll(".bg-swatch").forEach((el) =>
    el.classList.toggle("active", el.dataset.bg === bg));
}

function renderBgSwatches() {
  $("#bgGrid").innerHTML = BG_PRESETS.map((p) =>
    `<div class="bg-swatch" data-bg="${escapeHtml(p.bg)}"
        style="background:${p.bg}" title="${p.name}"><span class="sw-name">${p.name}</span></div>`
  ).join("");
  document.querySelectorAll(".bg-swatch").forEach((el) =>
    el.onclick = () => applyBg(el.dataset.bg));
}

function initBg() {
  let saved = BG_PRESETS[0].bg;
  try { saved = localStorage.getItem(BG_KEY) || saved; } catch (e) { /* ignore */ }
  renderBgSwatches();
  applyBg(saved);
}

async function saveModal() {
  const title = $("#fTitle").value.trim();
  if (!title) { alert("请填写标题"); return; }
  const remind = $("#fRemind").value ? $("#fRemind").value.replace("T", " ") : null;
  const body = {
    title,
    date: $("#fDate").value,
    remind_at: remind,
    note: $("#fNote").value.trim(),
    tag: $("#fTag").value,
    important: $("#fImportant").checked,
  };
  if (state.editId) {
    await api(`/api/todos/${state.editId}`, { method: "PATCH", body: JSON.stringify(body) });
    toast("已保存", "ok");
  } else {
    const created = await api("/api/todos", { method: "POST", body: JSON.stringify(body) });
    closeModal();
    afterCreated(created);
    return;
  }
  closeModal();
  const [y, m] = body.date.split("-").map(Number);
  state.year = y; state.month = m; state.selected = body.date;
  loadMonth();
}

/* ---------------- 初始化 ---------------- */
async function init() {
  const now = new Date();
  state.year = now.getFullYear();
  state.month = now.getMonth() + 1;
  state.selected = iso(now);

  const d = fmtCN(iso(now));
  $("#todayText").textContent = `${d.md} ${d.week}`;

  state.tags = await api("/api/tags");
  $("#legend").innerHTML = state.tags
    .map((t) => `<span class="legend-item"><span class="dot" style="background:${t.color}"></span>${t.name}</span>`)
    .join("");
  fillTagOptions();

  $("#prevMonth").onclick = () => {
    state.month--; if (state.month < 1) { state.month = 12; state.year--; }
    loadMonth();
  };
  $("#nextMonth").onclick = () => {
    state.month++; if (state.month > 12) { state.month = 1; state.year++; }
    loadMonth();
  };
  $("#todayBtn").onclick = () => {
    state.year = now.getFullYear(); state.month = now.getMonth() + 1;
    state.selected = iso(now); loadMonth();
  };

  $("#addBtn").onclick = quickAdd;
  $("#quickInput").addEventListener("keydown", (e) => { if (e.key === "Enter") quickAdd(); });
  $("#quickInput").addEventListener("input", () => {
    clearTimeout(previewTimer);
    const text = $("#quickInput").value.trim();
    if (!text) { $("#preview").classList.add("hidden"); return; }
    previewTimer = setTimeout(async () => {
      try { renderPreview(await api("/api/parse", { method: "POST", body: JSON.stringify({ text }) })); }
      catch (e) { /* ignore */ }
    }, 250);
  });

  $("#addDayBtn").onclick = () => openModal(null);
  $("#modalCancel").onclick = closeModal;
  $("#modalSave").onclick = saveModal;
  $("#clearRemind").onclick = () => { $("#fRemind").value = ""; };
  $("#modalMask").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    closeModal();
    closeAsk();
    $("#settingsMask").classList.add("hidden");
  });

  // 居中提醒询问框
  $("#askNo").onclick = closeAsk;
  $("#askYes").onclick = () => {
    const t = askTodo;
    closeAsk();
    if (t) openModal(t, true);
  };
  $("#askMask").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeAsk(); });

  // 背景设置
  $("#settingsBtn").onclick = () => { renderBgSwatches(); $("#settingsMask").classList.remove("hidden"); };
  $("#settingsClose").onclick = () => $("#settingsMask").classList.add("hidden");
  $("#settingsMask").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) $("#settingsMask").classList.add("hidden");
  });
  $("#bgApply").onclick = () => applyBg($("#bgColor").value);
  $("#bgReset").onclick = () => applyBg(BG_PRESETS[0].bg);

  initBg();
  await loadMonth();
  pollReminders();
  setInterval(pollReminders, 15000);
  askNotifyPermission();
}

init();
