// R-52: палитра команд. Переходы по разделам, частые действия и быстрый поиск товаров.
import { h, icon, clamp, debounce, number } from "./dom.js";
import { get, query } from "./api.js";
import { register, animateOut } from "./ui.js";
import { categoryIcon, sourceLabel } from "./meta.js";
import { go, addProduct, pickAndImport, backupNow, exportNow, openFolder } from "./actions.js";

export const NAV = [
  { key: "overview", label: "Обзор", icon: "overview", sub: "", keywords: "главная сводка дом" },
  { key: "products", label: "Товары", icon: "products", sub: "", keywords: "каталог список цены" },
  { key: "inbox", label: "Входящие", icon: "inbox", sub: "", keywords: "eye файлы импорт загрузка" },
  { key: "watch", label: "Слежение", icon: "watch", sub: "", keywords: "цель избранное сравнение следить" },
  { key: "assistant", label: "Помощник", icon: "assistant", sub: "", keywords: "чат бот спросить" },
  { key: "tasks", label: "Задачи", icon: "tasks", sub: "", keywords: "очередь расписание фон" },
  { key: "settings", label: "Настройки", icon: "settings", sub: "", keywords: "параметры тихие часы звук анимации" },
  { key: "autocollect", label: "Автосбор", icon: "refresh", sub: "", keywords: "источники поиск расписание" },
  { key: "service", label: "Сервис", icon: "service", sub: "", keywords: "журнал лог копии ядро выгрузка" },
  {key:"map",label:"Город",icon:"overview",sub:"",keywords:"карта топография"},
];

const ACTIONS = [
  { label: "Добавить товар вручную", icon: "plus", meta: "товары", keywords: "новый создать ручная цена", run: () => addProduct() },
  { label: "Загрузить файлы Eye", icon: "upload", meta: "входящие", keywords: "импорт json файл выгрузка", run: () => { go("#/inbox"); pickAndImport(); } },
  { label: "Новый диалог с помощником", icon: "assistant", meta: "помощник", keywords: "чат вопрос спросить", run: () => go("#/assistant/new") },
  { label: "Создать резервную копию", icon: "archive", meta: "сервис", keywords: "бэкап копия сохранить backup", run: () => backupNow() },
  { label: "Выгрузить данные в ZIP", icon: "download", meta: "сервис", keywords: "экспорт архив export", run: () => exportNow() },
  { label: "Открыть папку выгрузок", icon: "folder", meta: "папка", keywords: "exports экспорт", run: () => openFolder("exports") },
  { label: "Открыть папку R-52", icon: "folder", meta: "папка", keywords: "данные home домашняя", run: () => openFolder("home") },
];

let open = null;

export function openPalette(initial = "") {
  if (open) {
    open.input.select();
    return;
  }
  const opener = document.activeElement;
  const input = h("input", { type: "text", placeholder: "Найти товар, раздел или команду", "aria-label": "Поиск и команды", autocomplete: "off", spellcheck: "false", "aria-controls": "palette-list", value: initial });
  const list = h("div", { class: "palette-list", role: "listbox", id: "palette-list" });
  const box = h("div", { class: "palette glass-panel", role: "dialog", "aria-modal": "true", "aria-label": "Поиск и команды" },
    h("label", { class: "palette-input" }, icon("search"), input, h("kbd", { text: "Esc" })),
    list,
    h("div", { class: "palette-foot" },
      h("span", null, h("kbd", { text: "↑" }), h("kbd", { text: "↓" }), " выбрать"),
      h("span", null, h("kbd", { text: "Enter" }), " открыть"),
      h("span", null, h("kbd", { text: "Esc" }), " закрыть")));
  const scrim = h("div", { class: "scrim" });
  const wrap = h("div", { class: "palette-wrap" }, box);
  let items = [];
  let active = 0;
  let products = [];
  let searchSeq = 0;
  let closed = false;
  const unregister = register({ close: () => close() });

  async function close() {
    if (closed) return;
    closed = true;
    open = null;
    unregister();
    animateOut(scrim);
    await animateOut(box);
    wrap.remove();
    if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus({ preventScroll: true });
  }

  function run(item) {
    close();
    setTimeout(() => item.run(), 0);
  }

  function groups() {
    const needle = input.value.trim().toLowerCase();
    const match = (text) => !needle || text.toLowerCase().includes(needle);
    const result = [];
    if (products.length) result.push({ title: "Товары", items: products });
    const nav = NAV.filter((item) => match(`${item.label} ${item.sub} ${item.keywords}`))
      .map((item) => ({ label: item.label, icon: item.icon, meta: item.sub, run: () => go(`#/${item.key}`) }));
    if (nav.length) result.push({ title: "Разделы", items: nav });
    const actions = ACTIONS.filter((item) => match(`${item.label} ${item.meta} ${item.keywords}`));
    if (actions.length) result.push({ title: "Действия", items: actions });
    if (needle.length >= 2) {
      const text = input.value.trim();
      result.push({ title: "Помощник", items: [{ label: `Спросить: «${text}»`, icon: "assistant", meta: "новый диалог", run: () => go(`#/assistant/new?ask=${encodeURIComponent(text)}`) }] });
    }
    return result;
  }

  function render() {
    const result = groups();
    items = result.flatMap((group) => group.items);
    active = clamp(active, 0, Math.max(0, items.length - 1));
    list.replaceChildren();
    if (!items.length) {
      list.append(h("div", { class: "palette-empty", text: "Ничего не нашлось" }));
      input.removeAttribute("aria-activedescendant");
      return;
    }
    let index = 0;
    for (const group of result) {
      list.append(h("div", { class: "palette-group", role: "presentation", text: group.title }));
      for (const item of group.items) {
        const position = index;
        index += 1;
        const el = h("button", { class: ["palette-item", position === active && "on"], type: "button", role: "option", id: `pi-${position}`, "aria-selected": String(position === active), tabindex: "-1" },
          h("span", { class: "p-icon" }, icon(item.icon)),
          h("span", { class: "p-text", text: item.label }),
          item.meta ? h("span", { class: "p-meta", text: item.meta }) : null);
        el.addEventListener("mousemove", () => { if (active !== position) setActive(position, false); });
        el.addEventListener("click", () => run(item));
        list.append(el);
      }
    }
    input.setAttribute("aria-activedescendant", `pi-${active}`);
  }

  function setActive(next, scroll = true) {
    if (!items.length) return;
    active = (next + items.length) % items.length;
    for (const el of list.querySelectorAll(".palette-item")) {
      const on = el.id === `pi-${active}`;
      el.classList.toggle("on", on);
      el.setAttribute("aria-selected", String(on));
      if (on && scroll) el.scrollIntoView({ block: "nearest" });
    }
    input.setAttribute("aria-activedescendant", `pi-${active}`);
  }

  const search = debounce(async () => {
    const text = input.value.trim();
    const seq = ++searchSeq;
    if (text.length < 2) {
      if (products.length) {
        products = [];
        render();
      }
      return;
    }
    try {
      const data = await get(`/api/products${query({ q: text, limit: 6 })}`);
      if (seq !== searchSeq || closed) return;
      products = (data.items || []).map((p) => ({
        label: p.title,
        icon: categoryIcon(p.title),
        meta: `${p.price_text} · ${sourceLabel(p.source)}`,
        run: () => go(`#/products/${p.id}`),
      }));
      if ((data.total || 0) > products.length) {
        products.push({ label: `Все найденные товары: ${number(data.total)}`, icon: "search", meta: "товары", run: () => go(`#/products?q=${encodeURIComponent(text)}`) });
      }
      render();
    } catch {
      /* поиск в палитре не мешает командам */
    }
  }, 160);

  input.addEventListener("input", () => {
    active = 0;
    render();
    search();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || (event.key === "Tab" && !event.shiftKey)) {
      event.preventDefault();
      setActive(active + 1);
    } else if (event.key === "ArrowUp" || (event.key === "Tab" && event.shiftKey)) {
      event.preventDefault();
      setActive(active - 1);
    } else if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      const item = items[active];
      if (item) run(item);
    }
  });
  scrim.addEventListener("click", () => close());
  wrap.addEventListener("click", (event) => { if (event.target === wrap) close(); });
  document.getElementById("layer").append(scrim, wrap);
  render();
  if (initial) search();
  requestAnimationFrame(() => {
    input.focus({ preventScroll: true });
    input.select();
  });
  open = { input, close };
}
