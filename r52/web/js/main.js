import { installCompactInterface } from "./interface.js";
// R-52: точка входа окна. Загрузка, оболочка, маршруты, опрос ядра, уведомления и горячие клавиши.
import { h, icon, $, $$, sleep, randomKey, setTimeZone, number, ago, debounce } from "./dom.js";
import { api, get, post, configure, describe } from "./api.js";
import { state, emit, on } from "./state.js";
import { toast, fail, popover, closeTop, empty, skel, modal } from "./ui.js";
import { applyMotion, installSprings, spotlight, enter } from "./motion.js";
import { eventMeta } from "./meta.js";
import { NAV, openPalette } from "./palette.js";

import { magneticBackground } from "./terrain.js";
magneticBackground(document.querySelector(".backdrop"));
installCompactInterface(document.querySelector("#view"));
const VIEWS = {
  map: () => import("./views/map.js"),
  overview: () => import("./views/overview.js"),
  products: () => import("./views/products.js"),
  product: () => import("./views/product.js"),
  inbox: () => import("./views/inbox.js"),
  watch: () => import("./views/watch.js"),
  assistant: () => import("./views/assistant.js"),
  tasks: () => import("./views/tasks.js"),
  settings: () => import("./views/settings.js"),
  autocollect: () => import("./views/autocollect.js"),
  service: () => import("./views/service.js"),
};
const NOTIFY_META = ["TARGET_REACHED", "PRICE_DROPPED", "BECAME_AVAILABLE", "BECAME_UNAVAILABLE"].map(eventMeta);
const PAGE = `ui-${randomKey().replace(/[^a-zA-Z0-9]/g, "").slice(0, 20).toLowerCase()}`;

const ui = { nav: new Map() };
let current = null;
let routeToken = 0;
let screen = null;
let failures = 0;
let lastUnread = null;
let started = false;
let notifPop = null;
let audio = null;

// Маршруты вида #/раздел/параметр?ключ=значение. Карточка товара живёт под #/products/номер.
function resolve() {
  const hash = location.hash.replace(/^#\/?/, "");
  const cut = hash.indexOf("?");
  const pathPart = cut >= 0 ? hash.slice(0, cut) : hash;
  const queryPart = cut >= 0 ? hash.slice(cut + 1) : "";
  const parts = pathPart.split("/").filter(Boolean).map(decodePart);
  const known = NAV.some((item) => item.key === parts[0]);
  const key = known ? parts[0] : "overview";
  const rest = known ? parts.slice(1) : [];
  const view = key === "products" && rest[0] ? "product" : key;
  return { key, view, params: { id: rest[0] || null, rest, query: Object.fromEntries(new URLSearchParams(queryPart)) } };
}

function decodePart(part) {
  try { return decodeURIComponent(part); } catch { return part; }
}

function runSafe(fn) {
  try { fn(); } catch (error) { console.warn(error); }
}

async function route() {
  const target = resolve();
  const mapMode=target.view==='map';
  if(mapMode&&current&&current.view!=='map')sessionStorage.setItem('r52-before-map',current.view==='product'?`#/products/${current.params.id}`:`#/${current.key}`);
  document.documentElement.classList.toggle('radar-mode',mapMode);
  const sidebar=document.querySelector('.side');
  if(sidebar){sidebar.inert=mapMode;sidebar.setAttribute('aria-hidden',String(mapMode));}
  const token = ++routeToken;
  if (current) for (const fn of current.cleanups.splice(0)) runSafe(fn);
  while (closeTop()) { /* окна прошлого раздела закрываются вместе с ним */ }
  const owner = { ...target, cleanups: [] };
  current = owner;
  state.route = target;
  const meta = NAV.find((item) => item.key === target.key);
  setActiveNav(target.key);
  setHeader(meta.label, meta.sub);
  document.title = `${meta.label} · R-52`;
  const inner = h("div", { class: "view-inner" });
  ui.view.replaceChildren(inner);
  ui.view.scrollTop = 0;
  window.scrollTo(0, 0);
  const ctx = {
    params: target.params,
    alive: () => token === routeToken,
    cleanup(fn) {
      if (token === routeToken) owner.cleanups.push(fn);
      else runSafe(fn);
    },
    setHeader(title, sub) {
      if (token !== routeToken) return;
      setHeader(title, sub);
      if (title) document.title = `${title} · R-52`;
    },
  };
  emit("route", target);
  try {
    const module = await VIEWS[target.view]();
    if (token !== routeToken) return;
    await module.render(inner, ctx);
  } catch (error) {
    if (token !== routeToken) return;
    console.error(error);
    inner.replaceChildren(empty({
      icon: "warn",
      title: "Раздел не открылся",
      text: describe(error),
      action: { label: "Повторить", icon: "refresh", run: () => route() },
    }));
  }
  if (token === routeToken && !ui.view.contains(document.activeElement)) ui.view.focus({ preventScroll: true });
}

function setHeader(title, sub) {
  ui.title.textContent = title || "";
  ui.sub.textContent = sub || "";
  ui.sub.hidden = !sub;
}

function buildNav() {
  const nav = $("#nav");
  ui.pill = $("#nav-pill");
  for (const item of NAV) {
    const badge = h("span", { class: "badge pill violet num", hidden: true });
    const link = h("a", { class: "nav-link", href: `#/${item.key}`, title: item.sub },
      icon(item.icon), h("span", { class: "label", text: item.label }), badge);
    link.badge = badge;
    ui.nav.set(item.key, link);
    nav.append(link);
  }
  new ResizeObserver(() => movePill(ui.nav.get(current && current.key), false)).observe(nav);
}

function setActiveNav(key) {
  let active = null;
  for (const [navKey, link] of ui.nav) {
    const on = navKey === key;
    link.classList.toggle("active", on);
    if (on) {
      link.setAttribute("aria-current", "page");
      active = link;
    } else {
      link.removeAttribute("aria-current");
    }
  }
  movePill(active, true);
}

// Подложка активного пункта едет пружиной. Сдвиг считаем от самой подложки, чтобы не зависеть от разметки выше.
function movePill(link, animate) {
  if (!ui.pill) return;
  if (!link) {
    ui.pill.classList.remove("on");
    return;
  }
  if (!animate) ui.pill.style.setProperty("transition", "none");
  ui.pill.style.setProperty("--y", `${link.offsetTop - ui.pill.offsetTop}px`);
  ui.pill.classList.add("on");
  if (!animate) {
    void ui.pill.offsetWidth;
    ui.pill.style.removeProperty("transition");
  }
}

function setNavCount(key, value) {
  const link = ui.nav.get(key);
  if (!link) return;
  link.badge.hidden = !value;
  link.badge.textContent = value > 99 ? "99+" : number(value || 0);
}

function buildTop() {
  ui.view = $("#view");
  ui.title = $("#view-title");
  ui.sub = $("#view-sub");
  const search = h("button", { class: "search-trigger", type: "button", "aria-label": "Поиск и команды", onClick: () => openPalette() },
    icon("search"), h("span", { class: "label", text: "Поиск и команды" }), h("kbd", { text: "Ctrl K" }));
  ui.linkDot = h("span", { class: "pulse-dot" });
  ui.linkText = h("span", { text: "на связи" });
  ui.bellCount = h("span", { class: "count-dot num", hidden: true });
  ui.bell = h("button", {
    class: "icon-btn bell",
    type: "button",
    "aria-label": "Уведомления",
    "aria-haspopup": "dialog",
    "aria-expanded": "false",
    onClick: () => toggleNotifications(),
  }, icon("bell"), ui.bellCount);
  $("#top-tools").append(search,
    h("div", { class: "status-pill", role: "status", title: "Связь с ядром R-52" }, ui.linkDot, ui.linkText),
    ui.bell);
}

function renderCore(status) {
  const db = status.database || {};
  const problems = Array.isArray(status.problems) ? status.problems.length : 0;
  const ok = status.health ? status.health.ok !== false : true;
  const last = status.backups && status.backups.last;
  const lastTime = last && typeof last === "object" ? last.created_at : last;
  const row = (label, value) => h("div", { class: "core-row" }, h("span", { text: label }), h("b", { class: "num", text: value }));
  const text = !ok ? "Ядро сообщает о сбое" : problems ? `Есть замечания: ${number(problems)}` : `Ядро ${status.version || ""} работает`;
  $("#core-card").replaceChildren(h("a", { class: "core-card", href: "#/service", title: "Состояние ядра" },
    h("div", { class: "core-row" }, h("span", { class: ["pulse-dot", (!ok || problems) && "warn"] }), h("span", { class: "core-text", text })),
    row("Товаров", number(db.products || 0)),
    row("Наблюдений", number(db.observations || 0)),
    row("Копия", lastTime ? ago(lastTime) : "ещё не было")));
}

function applyStatus(status) {
  state.status = status;
  if (Array.isArray(status.sources)) state.sources = new Map(status.sources.map((source) => [source.key, source]));
  renderCore(status);
  setNavCount("inbox", status.inbox_pending || 0);
  const tasks = status.tasks || {};
  setNavCount("tasks", (tasks.RUNNING || 0) + (tasks.QUEUED || 0));
  setUnread((status.notifications && status.notifications.unread) || 0);
  emit("status", status);
}

async function refreshStatus() {
  try {
    const status = await get("/api/status", { timeout: 10000 });
    applyStatus(status);
    return status;
  } catch {
    return null;
  }
}

function setUnread(n) {
  ui.bellCount.hidden = !n;
  ui.bellCount.textContent = n > 99 ? "99+" : number(n);
  ui.bell.setAttribute("aria-label", n ? `Уведомления, непрочитанных: ${n}` : "Уведомления");
  if (lastUnread !== null && n > lastUnread) {
    ui.bell.classList.remove("ringing");
    void ui.bell.offsetWidth;
    ui.bell.classList.add("ringing");
    setTimeout(() => ui.bell.classList.remove("ringing"), 1600);
  }
  lastUnread = n;
}

function setLink(ok) {
  if (!ui.linkDot) return;
  ui.linkDot.classList.toggle("bad", !ok);
  ui.linkText.textContent = ok ? "на связи" : "нет связи";
}

// Ядро копит уведомления, окно показывает ожидающие и помечает их показанными. Тихие часы соблюдает ядро.
async function deliverPending() {
  if (document.visibilityState !== "visible" || screen) return;
  let data;
  try {
    data = await get("/api/notifications?status=PENDING&limit=5");
  } catch {
    return;
  }
  for (const item of data.items || []) {
    try {
      await post(`/api/notifications/${item.id}/shown`, {});
    } catch {
      continue;
    }
    toast({
      kind: "ok",
      title: item.title,
      text: item.body,
      timeout: 9000,
      action: item.product_id ? { label: "Открыть товар", run: () => { location.hash = `#/products/${item.product_id}`; } } : undefined,
    });
    if (state.settings && state.settings["ui.sound"]) chime();
  }
}

function chime() {
  try {
    audio = audio || new AudioContext();
    const t = audio.currentTime;
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, t);
    osc.frequency.exponentialRampToValueAtTime(1320, t + 0.12);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.07, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.6);
    osc.connect(gain).connect(audio.destination);
    osc.start(t);
    osc.stop(t + 0.62);
  } catch {
    /* звук необязателен */
  }
}

function toggleNotifications() {
  if (notifPop && notifPop.isOpen()) {
    notifPop.close();
    return;
  }
  notifPop = popover(ui.bell, { width: 390, build: (el, { close }) => buildNotifications(el, close) });
}

async function buildNotifications(el, close) {
  const list = h("div", { class: "notif-list" }, skel(58), skel(58), skel(58));
  const readAll = h("button", { class: "btn sm ghost", type: "button", hidden: true }, icon("check"), "Прочитать все");
  el.append(h("div", { class: "notif-head" }, h("b", { text: "Уведомления" }), readAll), list);
  readAll.addEventListener("click", async () => {
    try {
      await post("/api/notifications/read-all", {});
      for (const row of list.querySelectorAll(".notif.unread")) row.classList.remove("unread");
      readAll.hidden = true;
      setUnread(0);
      emit("data", { kind: "notifications" });
    } catch (error) {
      fail(error);
    }
  });
  let data;
  try {
    data = await get("/api/notifications?limit=30");
  } catch (error) {
    list.replaceChildren(h("p", { class: "muted", text: describe(error) }));
    return;
  }
  const items = data.items || [];
  readAll.hidden = !(data.counts && data.counts.unread);
  if (!items.length) {
    list.replaceChildren(empty({ icon: "bell", title: "Пока тихо", text: "Здесь появятся сообщения о целях, снижении цен и наличии." }));
    return;
  }
  list.replaceChildren(...items.map((item) => notifRow(item, close)));
  enter(Array.from(list.children), { y: 8, step: 30 });
}

function notifRow(item, close) {
  const unread = !["READ", "DISMISSED"].includes(item.status);
  const meta = NOTIFY_META.find((entry) => entry.text === item.title) || eventMeta(null);
  const note = item.status === "DEFERRED_QUIET" ? " · отложено до конца тихих часов" : "";
  const row = h("button", { class: ["notif", unread && "unread"], type: "button" },
    h("span", { class: "f-icon", style: { "--c": meta.color } }, icon(meta.icon)),
    h("span", null, h("b", { text: item.title }), h("p", { text: item.body }), h("small", { text: `${ago(item.created_at)}${note}` })));
  row.addEventListener("click", async () => {
    if (row.classList.contains("unread")) {
      try {
        await post(`/api/notifications/${item.id}/read`, {});
        row.classList.remove("unread");
        refreshStatus();
      } catch (error) {
        fail(error);
      }
    }
    close();
    if (item.product_id) location.hash = `#/products/${item.product_id}`;
    else if (/входящ|проверки|файл/i.test(item.body||'')) location.hash = '#/inbox';
    else if (/задач|расписан|импорт|копи|баз/i.test(item.title+' '+item.body)) location.hash = '#/tasks';
    else if (/наблюден|цен|налич|цел/i.test(item.title+' '+item.body)) location.hash = '#/watch';
    else modal({title:item.title,body:h('p',{text:item.body||'Нет дополнительных сведений'})});
  });
  return row;
}

// Экраны поверх интерфейса: нет связи, сеанс окна истёк, ядро остановлено.
function showScreen(kind, { title, text, actions = [] }) {
  hideScreen(true);
  const el = h("div", { class: "boot screen", role: "alertdialog", "aria-label": title },
    h("div", { class: "boot-orb idle" }, h("i")),
    h("h2", { text: title }),
    h("p", { text }),
    actions.length ? h("div", { class: "boot-actions" }, actions.map((action) =>
      h("button", { class: ["btn", action.kind], type: "button", onClick: action.run }, action.icon ? icon(action.icon) : null, action.label))) : null);
  document.body.append(el);
  screen = { kind, el };
}

function hideScreen(instant) {
  if (!screen) return;
  const { el } = screen;
  screen = null;
  if (instant) {
    el.remove();
    return;
  }
  el.classList.add("gone");
  setTimeout(() => el.remove(), 800);
}

configure({
  authLost() {
    if (screen && screen.kind === "auth") return;
    showScreen("auth", {
      title: "Окно потеряло доступ к ядру",
      text: "Сеанс окна истёк или ядро перезапустилось. Откройте R-52 заново из ярлыка или значка в трее: окно получит новый вход.",
      actions: [{ label: "Обновить страницу", icon: "refresh", kind: "primary", run: () => location.reload() }],
    });
  },
  connection(ok) {
    setLink(ok);
    if (ok) {
      failures = 0;
      if (screen && screen.kind === "offline") {
        hideScreen(false);
        toast({ kind: "ok", title: "Связь с ядром восстановлена", timeout: 2600 });
        refreshStatus();
      }
      return;
    }
    failures += 1;
    if (started && failures >= 2 && !screen) {
      showScreen("offline", {
        title: "Нет связи с ядром",
        text: "Ядро R-52 не отвечает: возможно, оно перезапускается или было остановлено. Окно переподключится само, как только ядро вернётся.",
        actions: [{ label: "Проверить сейчас", icon: "refresh", kind: "primary", run: () => refreshStatus() }],
      });
    }
  },
});

on("core-stopped", () => {
  showScreen("stopped", {
    title: "Ядро остановлено",
    text: "Данные сохранены, это окно можно закрыть. Чтобы продолжить, запустите R-52 снова.",
  });
});

async function heartbeat() {
  try {
    await post("/api/ui/heartbeat", { page: PAGE }, { timeout: 8000 });
  } catch {
    /* потерю связи покажет опрос состояния */
  }
}

function startPolling() {
  setInterval(async () => {
    if (screen && screen.kind !== "offline") return;
    const status = await refreshStatus();
    if (status) deliverPending();
  }, 15000);
  setInterval(() => {
    if (!screen) heartbeat();
  }, 60000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || screen) return;
    refreshStatus();
    heartbeat();
    deliverPending();
  });
  on("data", debounce(() => refreshStatus(), 500));
  on("settings", (values) => { state.settings = { ...state.settings, ...values }; });
  window.addEventListener("pagehide", () => {
    api("POST", "/api/ui/bye", { page: PAGE }, { keepalive: true, timeout: 3000 }).catch(() => {});
  });
}

function isTyping(target) {
  return target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
}

document.addEventListener("keydown", (event) => {
  if (!started) return;
  if ((event.ctrlKey || event.metaKey) && !event.altKey && (event.code === "KeyK" || event.key === "k" || event.key === "K")) {
    event.preventDefault();
    openPalette();
    return;
  }
  if (event.key === "Escape") {
    if (closeTop()) event.preventDefault();
    return;
  }
  if (event.ctrlKey || event.metaKey || isTyping(event.target)) return;
  if (event.key === "/" && !event.altKey) {
    event.preventDefault();
    openPalette();
    return;
  }
  if (event.altKey && /^Digit[1-8]$/.test(event.code)) {
    const item = NAV[Number(event.code.slice(5)) - 1];
    if (item) {
      event.preventDefault();
      location.hash = `#/${item.key}`;
    }
  }
});

async function boot() {
  installSprings();
  spotlight(document);
  const bootEl = $("#boot");
  const bootText = $("#boot-text");
  let status = null;
  for (let attempt = 0; attempt < 6 && !status; attempt += 1) {
    try {
      status = await get("/api/status", { timeout: 8000 });
    } catch (error) {
      if (error.status === 401) {
        bootEl.remove();
        return;
      }
      bootText.textContent = attempt < 1 ? "Подключаюсь к ядру" : "Ядро ещё запускается, пробую снова";
      await sleep(800 + attempt * 700);
    }
  }
  if (!status) {
    bootEl.remove();
    showScreen("offline", {
      title: "Ядро не отвечает",
      text: "Окно не смогло подключиться к ядру R-52. Проверьте, что ядро запущено, или запустите R52-doctor.cmd.",
      actions: [{ label: "Повторить", icon: "refresh", kind: "primary", run: () => location.reload() }],
    });
    return;
  }
  if (status.timezone) setTimeZone(status.timezone);
  try {
    state.settings = (await get("/api/settings")).values || {};
  } catch {
    state.settings = {};
  }
  applyMotion(state.settings["ui.motion"] !== false);
  buildTop();
  buildNav();
  applyStatus(status);
  $("#shell").hidden = false;
  started = true;
  bootEl.classList.add("gone");
  setTimeout(() => bootEl.remove(), 900);
  window.addEventListener("hashchange", () => route());
  if (!location.hash || location.hash === "#") history.replaceState(null, "", "#/overview");
  route();
  enter($$(".nav-link"), { x: -12, y: 0, step: 36, duration: 560 });
  heartbeat();
  deliverPending();
  startPolling();
}

boot();
