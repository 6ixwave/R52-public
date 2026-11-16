// R-52: всплывающие уведомления, модальные окна, панели и мелкие элементы управления.
import { h, icon, svg, uid, clamp, setStyle, number } from "./dom.js";
import { describe } from "./api.js";
import { motionOn } from "./motion.js";
import { AVAILABILITY, sourceColor, sourceLabel } from "./meta.js";

const layers = [];
let layerOrder=50;
const raiseLayers=(...elements)=>{for(const el of elements)el.style.zIndex=String(++layerOrder);};
const layerRoot = () => document.getElementById("layer");

export function register(entry) {
  layers.push(entry);
  return () => {
    const index = layers.indexOf(entry);
    if (index >= 0) layers.splice(index, 1);
  };
}

export function closeTop() {
  const top = layers[layers.length - 1];
  if (!top) return false;
  top.close();
  return true;
}

// Закрытие с анимацией: класс closing запускает CSS, удаление по animationend или по таймеру.
export function animateOut(el) {
  return new Promise((resolve) => {
    if (!el || !el.isConnected) { resolve(); return; }
    if (!motionOn()) { el.remove(); resolve(); return; }
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      el.remove();
      resolve();
    };
    el.addEventListener("animationend", (event) => { if (event.target === el) finish(); });
    el.classList.add("closing");
    setTimeout(finish, 460);
  });
}

function trapTab(event, container) {
  if (event.key !== "Tab") return;
  const items = Array.from(container.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"))
    .filter((el) => !el.disabled && !el.hidden && el.getClientRects().length > 0);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

function restoreFocus(el) {
  if (el && el.isConnected && typeof el.focus === "function") el.focus({ preventScroll: true });
}

const TOAST_ICONS = { info: "info", ok: "ok", warn: "warn", error: "fail" };

export function toast({ title, text, kind = "info", timeout = 4200, action } = {}) {
  const host = document.getElementById("toasts");
  if (!host) return { close() {} };
  let closed = false;
  let el = null;
  const close = () => {
    if (closed) return;
    closed = true;
    animateOut(el);
  };
  const progress = timeout ? h("i", { class: "t-progress", style: { "--d": `${timeout}ms` } }) : null;
  const actionRow = action
    ? h("div", { class: "t-actions" }, h("button", { class: "btn sm", type: "button", onClick: () => { close(); action.run(); } }, action.label))
    : null;
  el = h("div", { class: ["toast glass-panel", kind], role: kind === "error" ? "alert" : "status" },
    h("div", { class: "t-icon" }, icon(TOAST_ICONS[kind] || "info")),
    h("div", null, h("b", { text: title || "" }), text ? h("p", { text }) : null, actionRow),
    h("button", { class: "icon-btn sm bare", type: "button", "aria-label": "Закрыть", onClick: close }, icon("x")),
    progress);
  if (progress) progress.addEventListener("animationend", (event) => { if (event.target === progress) close(); });
  host.append(el);
  while (host.children.length > 4) host.firstElementChild.remove();
  return { close };
}

export function fail(error, title = "Не получилось") {
  if (error && (error.code === "CORE_UNREACHABLE" || error.status === 401)) return;
  console.warn(error);
  toast({ kind: "error", title, text: describe(error), timeout: 7000 });
}

// Модальное окно. Действие может вернуть false, тогда окно остаётся открытым (например, при ошибке в поле).
export function modal({ title, body, actions = [], width = 520, onClose, className } = {}) {
  const opener = document.activeElement;
  const scrim = h("div", { class: "scrim" });
  const bodyEl = h("div", { class: "modal-body" }, body);
  let closed = false;
  const unregister = register({ close: () => close() });
  const buttons = actions.map(makeButton);
  const box = h("div", { class: ["modal glass-panel", className], role: "dialog", "aria-modal": "true", "aria-label": title, style: { "--w": `${width}px` } },
    h("div", { class: "modal-head" },
      h("h2", { text: title }),
      h("button", { class: "icon-btn sm bare", type: "button", "aria-label": "Закрыть", onClick: () => close() }, icon("x"))),
    bodyEl,
    buttons.length ? h("div", { class: "modal-foot" }, buttons) : null);
  const wrap = h("div", { class: "modal-wrap" }, box);

  async function close(result) {
    if (closed) return;
    closed = true;
    unregister();
    animateOut(scrim);
    await animateOut(box);
    wrap.remove();
    restoreFocus(opener);
    if (onClose) onClose(result);
  }

  function makeButton(action) {
    const button = h("button", { class: ["btn", action.kind], type: "button", "data-autofocus": action.autofocus || undefined },
      action.icon ? icon(action.icon) : null, action.label);
    if (action.kind === "primary" || action.kind === "danger") button.dataset.primary = "1";
    button.addEventListener("click", async () => {
      if (!action.run) { close(action.value); return; }
      if (button.classList.contains("busy")) return;
      button.classList.add("busy");
      try {
        const result = await action.run({ close, body: bodyEl, button });
        if (result !== false) close(action.value === undefined ? result : action.value);
      } catch (error) {
        fail(error);
      } finally {
        button.classList.remove("busy");
      }
    });
    return button;
  }

  scrim.addEventListener("click", () => close());
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing && event.target instanceof HTMLInputElement) {
      const primary = buttons.find((button) => button.dataset.primary);
      if (primary) { event.preventDefault(); primary.click(); }
    }
    trapTab(event, box);
  });
  raiseLayers(scrim,wrap);
  layerRoot().append(scrim, wrap);
  requestAnimationFrame(() => {
    const first = box.querySelector("[data-autofocus], input, textarea, select") || buttons[buttons.length - 1];
    if (first) first.focus({ preventScroll: true });
  });
  return { close, el: box, body: bodyEl };
}

export function confirm({ title, text, ok = "Подтвердить", cancel = "Отмена", danger = false } = {}) {
  return new Promise((resolve) => {
    let accepted = false;
    modal({
      title,
      width: 440,
      body: h("p", { class: "muted", text }),
      onClose: () => resolve(accepted),
      actions: [
        { label: cancel, kind: "ghost", autofocus: danger },
        { label: ok, kind: danger ? "danger" : "primary", run: () => { accepted = true; } },
      ],
    });
  });
}

// Выезжающая панель справа. Содержимое (шапку и тело) собирает вызывающий код.
export function drawer({ label, onClose } = {}) {
  const opener = document.activeElement;
  const scrim = h("div", { class: "scrim" });
  const el = h("aside", { class: "drawer glass-panel", role: "dialog", "aria-modal": "true", "aria-label": label || "Подробности", tabindex: "-1" });
  let closed = false;
  const unregister = register({ close: () => close() });
  async function close() {
    if (closed) return;
    closed = true;
    unregister();
    animateOut(scrim);
    await animateOut(el);
    restoreFocus(opener);
    if (onClose) onClose();
  }
  scrim.addEventListener("click", () => close());
  el.addEventListener("keydown", (event) => trapTab(event, el));
  raiseLayers(scrim,el);
  layerRoot().append(scrim, el);
  requestAnimationFrame(() => el.focus({ preventScroll: true }));
  return { el, close, isOpen: () => !closed };
}

export function popover(anchor, { width = 360, build } = {}) {
  const el=h('div',{class:'popover glass-panel',role:'dialog'});let closed=false,frame=0;
  const unregister=register({close:()=>close()});
  const place=()=>{
    frame=0;if(closed)return;if(!anchor.isConnected){close();return;}
    const vv=window.visualViewport,left=vv?.offsetLeft||0,top=vv?.offsetTop||0,w=vv?.width||innerWidth,h=vv?.height||innerHeight,edge=12,gap=8,r=anchor.getBoundingClientRect();
    const below=Math.max(0,top+h-edge-r.bottom-gap),above=Math.max(0,r.top-gap-top-edge),up=below<220&&above>below;
    const available=Math.max(24,up?above:below),panelWidth=Math.min(width,w-2*edge);
    Object.assign(el.style,{width:panelWidth+'px',maxHeight:available+'px',right:'auto',left:Math.max(left+edge,Math.min(r.right-panelWidth,left+w-edge-panelWidth))+'px',overflowY:'auto'});
    const height=Math.min(el.scrollHeight,available);
    el.style.top=Math.max(top+edge,Math.min(up?r.top-gap-height:r.bottom+gap,top+h-edge-height))+'px';
  };
  const schedule=()=>{if(!frame&&!closed)frame=requestAnimationFrame(place);};
  const outside=event=>{if(!el.contains(event.target)&&!anchor.contains(event.target))close();};
  const observer=new ResizeObserver(schedule);
  function close(){
    if(closed)return;closed=true;unregister();cancelAnimationFrame(frame);observer.disconnect();
    document.removeEventListener('pointerdown',outside,true);window.removeEventListener('resize',schedule);window.removeEventListener('scroll',schedule,true);window.visualViewport?.removeEventListener('resize',schedule);
    anchor.setAttribute('aria-expanded','false');animateOut(el);
  }
  document.addEventListener('pointerdown',outside,true);window.addEventListener('resize',schedule);window.addEventListener('scroll',schedule,true);window.visualViewport?.addEventListener('resize',schedule);
  anchor.setAttribute('aria-expanded','true');raiseLayers(el);layerRoot().append(el);if(build)build(el,{close});observer.observe(el);observer.observe(anchor);place();
  return {el,close,isOpen:()=>!closed};
}

// Сегменты с плавающей подложкой. Значения всегда строки.
export function segmented(options, value, onChange, { size, label } = {}) {
  const thumb = h("span", { class: "thumb", "aria-hidden": "true" });
  const root = h("div", { class: ["seg", size], role: "radiogroup", "aria-label": label }, thumb);
  let current = String(value);
  const buttons = options.map((option) => {
    const counter = h("span", { class: "count", hidden: option.count === undefined || option.count === null, text: number(option.count ?? 0) });
    const button = h("button", { type: "button", role: "radio", title: option.title, "aria-label": option.label ? undefined : option.title, dataset: { value: String(option.value) } },
      option.icon ? icon(option.icon) : null,
      option.label ? h("span", { text: option.label }) : null,
      counter);
    button.counter = counter;
    button.addEventListener("click", () => select(String(option.value), true));
    root.append(button);
    return button;
  });
  function paint(animate) {
    const active = buttons.find((button) => button.dataset.value === current);
    for (const button of buttons) {
      button.classList.toggle("on", button === active);
      button.setAttribute("aria-checked", String(button === active));
    }
    if (!active || !active.offsetWidth) return;
    if (!animate) thumb.style.setProperty("transition", "none");
    thumb.style.setProperty("--x", `${active.offsetLeft}px`);
    thumb.style.setProperty("--w", `${active.offsetWidth}px`);
    if (!animate) {
      void thumb.offsetWidth;
      thumb.style.removeProperty("transition");
    }
  }
  function select(next, fromUser) {
    if (next === current) return;
    current = next;
    paint(true);
    if (fromUser && onChange) onChange(next);
  }
  new ResizeObserver(() => paint(false)).observe(root);
  root.setValue = (next) => select(String(next), false);
  root.setCount = (optionValue, n) => {
    const button = buttons.find((item) => item.dataset.value === String(optionValue));
    if (!button) return;
    button.counter.hidden = n === null || n === undefined;
    button.counter.textContent = number(n ?? 0);
  };
  paint(false);
  return root;
}

export function switchEl(checked, onChange, label) {
  const el = h("button", { type: "button", class: "switch", role: "switch", "aria-checked": String(Boolean(checked)), "aria-label": label });
  el.addEventListener("click", () => {
    const next = el.getAttribute("aria-checked") !== "true";
    el.setAttribute("aria-checked", String(next));
    if (onChange) onChange(next);
  });
  el.setValue = (next) => el.setAttribute("aria-checked", String(Boolean(next)));
  el.getValue = () => el.getAttribute("aria-checked") === "true";
  return el;
}

export function ring(fraction, { size = 46, stroke = 4, label } = {}) {
  const radius = (size - stroke) / 2;
  const length = 2 * Math.PI * radius;
  const id = uid("rg");
  const share = clamp(Number(fraction) || 0, 0, 1);
  const value = svg("circle", { class: "val", cx: size / 2, cy: size / 2, r: radius, fill: "none", "stroke-width": stroke, stroke: `url(#${id})`, "stroke-linecap": "round", "stroke-dasharray": length.toFixed(2) });
  value.style.setProperty("stroke-dashoffset", length.toFixed(2));
  const el = h("div", { class: "ring" },
    svg("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, "aria-hidden": "true" },
      svg("defs", null, svg("linearGradient", { id, x1: "0", y1: "0", x2: "1", y2: "1" },
        svg("stop", { offset: "0", "stop-color": "#a78bfa" }), svg("stop", { offset: "1", "stop-color": "#22d3ee" }))),
      svg("circle", { class: "track", cx: size / 2, cy: size / 2, r: radius, fill: "none", "stroke-width": stroke }),
      value),
    h("b", { text: label ?? `${Math.round(share * 100)}%` }));
  const offset = (length * (1 - share)).toFixed(2);
  requestAnimationFrame(() => requestAnimationFrame(() => value.style.setProperty("stroke-dashoffset", offset)));
  return el;
}

export function empty({ icon: name = "box", title, text, action, secondary } = {}) {
  const button = (item, cls) => h("button", { class: cls, type: "button", onClick: item.run }, item.icon ? icon(item.icon) : null, item.label);
  return h("div", { class: "empty" },
    h("div", { class: "empty-icon" }, icon(name)),
    h("h3", { text: title }),
    text ? h("p", { text }) : null,
    action || secondary ? h("div", { class: "row wrap center" }, action ? button(action, "btn primary") : null, secondary ? button(secondary, "btn") : null) : null);
}

export function sourceBadge(key) {
  return h("span", { class: "src", style: { "--c": sourceColor(key) }, text: sourceLabel(key) });
}

export function availabilityBadge(value) {
  const meta = AVAILABILITY[value] || AVAILABILITY.UNKNOWN;
  return h("span", { class: ["badge", meta.cls] }, icon(meta.icon), meta.text);
}

export function changeChip(change) {
  if (change === null || change === undefined || Number.isNaN(Number(change))) return null;
  const value = Number(change);
  const kind = value < 0 ? "down" : value > 0 ? "up" : "flat";
  const sign = value < 0 ? "\u2212" : value > 0 ? "+" : "";
  return h("span", { class: ["chg", kind], title: "Изменение к прошлому наблюдению" },
    icon(value < 0 ? "trendDown" : value > 0 ? "trendUp" : "minus"), `${sign}${number(Math.abs(value), 1)} %`);
}

export function field(label, control, { hint, wide } = {}) {
  const error = h("span", { class: "error", hidden: true });
  const el = h("label", { class: ["field", wide && "wide"] },
    h("span", { class: "label", text: label }), control, hint ? h('details',{class:'help-tip'},h('summary',{'aria-label':'Информация: '+label,text:'?'}),h('p',{text:hint})) : null, error);
  el.setError = (text) => {
    error.textContent = text || "";
    error.hidden = !text;
    if (control.classList) control.classList.toggle("invalid", Boolean(text));
  };
  return el;
}

export function selectEl(options, value, attrs = {}) {
  return h("select", { class: "select", ...attrs, value }, options.map(([optionValue, text]) => h("option", { value: optionValue, text })));
}

export function skel(height, width) {
  return h("div", { class: "skel", style: { height: `${height}px`, width: width || "100%" } });
}

export async function busy(button, task) {
  if (!button) return task();
  button.classList.add("busy");
  try {
    return await task();
  } finally {
    button.classList.remove("busy");
  }
}
