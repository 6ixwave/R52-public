// R-52: товары. Поиск, фильтры, сортировка, плитка или список и выбор товаров для сравнения.
import { openAnalysis } from "../analysis-panel.js";
import { h, icon, count, debounce } from "../dom.js";
import { get, query, describe } from "../api.js";
import { state, on } from "../state.js";
import { empty, skel, segmented, selectEl, toast, animateOut } from "../ui.js";
import { sourceLabel } from "../meta.js";
import { enter } from "../motion.js";
import { productCard, loadSparks, addProduct, pickAndImport } from "../actions.js";
import { showCompare, MAX_GROUP } from "../flows.js";

const PAGE_SIZE = 48;
const prefs = { q: "", source: "", only: "", sort: "recent", layout: storedLayout() };
const selected = new Map();

function storedLayout() {
  try {
    return localStorage.getItem("r52.layout") === "list" ? "list" : "grid";
  } catch {
    return "grid";
  }
}

export async function render(root, ctx) {
  if (ctx.params.query.q !== undefined) prefs.q = ctx.params.query.q;
  if (ctx.params.query.only !== undefined) prefs.only = ctx.params.query.only;

  const search = h("input", { class: "input", type: "search", placeholder: "Название, модель или номер товара", "aria-label": "Поиск товаров", value: prefs.q });
  const sources = Array.from((state.sources || new Map()).values());
  const source = selectEl([["", "Все источники"], ...sources.map((item) => [item.key, item.name || sourceLabel(item.key)])], prefs.source, { "aria-label": "Источник" });
  const only = segmented([
    { value: "", label: "Все" },
    { value: "favorites", label: "Избранное", icon: "star" },
    { value: "watched", label: "Слежение", icon: "target" },
  ], prefs.only, (value) => {
    prefs.only = value;
    reload();
  }, { size: "sm", label: "Показать" });
  const sort = selectEl([
    ["recent", "Недавно обновлённые"],
    ["price", "Сначала дешевле"],
    ["title", "По названию"],
    ["added", "Недавно добавленные"],
  ], prefs.sort, { "aria-label": "Сортировка" });
  const grid = h("div", { class: ["prod-grid", prefs.layout === "list" && "list"] });
  const layout = segmented([
    { value: "grid", icon: "grid", title: "Плиткой" },
    { value: "list", icon: "list", title: "Списком" },
  ], prefs.layout, (value) => {
    prefs.layout = value;
    grid.classList.toggle("list", value === "list");
    try { localStorage.setItem("r52.layout", value); } catch { /* не страшно */ }
  }, { size: "sm", label: "Вид списка" });
  const more = h("button", { class: "btn load-more", type: "button", hidden: true, onClick: () => fetchPage(false) }, icon("plus"), "Показать ещё");
  root.append(
    h("div", { class: "stack" },
      h("div", { class: "toolbar" },
        h("label", { class: "input-wrap grow" }, icon("search"), search),
        h("button", { class: "btn primary", type: "button", onClick: () => addProduct({ title: search.value.trim() }) }, icon("plus"), "Добавить")),
      h("div", { class: "toolbar" }, source, only, sort, h("span", { class: "spacer" }), layout)),
    grid,
    more);

  let offset = 0;
  let total = 0;
  let seq = 0;
  let bar = null;
  const filtered = () => Boolean(prefs.q.trim() || prefs.source || prefs.only);

  async function fetchPage(reset, quiet = false) {
    const mine = ++seq;
    if (reset) {
      offset = 0;
      if (!quiet) grid.replaceChildren(...Array.from({ length: 6 }, () => skel(190)));
    }
    let data;
    try {
      data = await get(`/api/products${query({ q: prefs.q.trim(), source: prefs.source, only: prefs.only, sort: prefs.sort, limit: PAGE_SIZE, offset })}`);
    } catch (error) {
      if (mine !== seq || !ctx.alive()) return;
      grid.replaceChildren(empty({ icon: "warn", title: "Список не загрузился", text: describe(error), action: { label: "Повторить", icon: "refresh", run: () => fetchPage(true) } }));
      more.hidden = true;
      return;
    }
    if (mine !== seq || !ctx.alive()) return;
    total = data.total || 0;
    const word = count(total, "товар", "товара", "товаров");
    ctx.setHeader("Товары", filtered() ? `Найдено: ${word}` : total ? `В базе ${word}` : "");
    const items = data.items || [];
    if (reset) grid.replaceChildren();
    if (reset && !items.length) {
      grid.replaceChildren(emptyState());
      more.hidden = true;
      return;
    }
    const cards = items.map((p) => productCard(p, { selected: selected.has(p.id), onSelect }));
    grid.append(...cards);
    if (!quiet) enter(cards, { y: 12, step: 28, maxSteps: 12 });
    offset += items.length;
    more.hidden = offset >= total;
    const sparks = await loadSparks(items.map((p) => p.id));
    if (!ctx.alive()) return;
    for (const card of cards) card.setSpark(sparks[card.dataset.id] || []);
  }

  const reload = () => fetchPage(true);

  function emptyState() {
    if (filtered()) {
      return empty({ icon: "search", title: "Ничего не нашлось", text: "Измените запрос или сбросьте фильтры.", action: { label: "Сбросить фильтры", icon: "undo", run: resetFilters } });
    }
    return empty({
      icon: "products",
      title: "Товаров пока нет",
      text: "Добавьте товар или загрузите Eye.",
      action: { label: "Добавить вручную", icon: "plus", run: () => addProduct() },
      secondary: { label: "Загрузить файл Eye", icon: "upload", run: () => pickAndImport() },
    });
  }

  function resetFilters() {
    prefs.q = "";
    prefs.source = "";
    prefs.only = "";
    search.value = "";
    source.value = "";
    only.setValue("");
    reload();
  }

  function onSelect(p, next) {
    if (next && selected.size >= MAX_GROUP) {
      toast({ kind: "warn", title: `Сравнить можно до ${MAX_GROUP} товаров сразу`, timeout: 3000 });
      return false;
    }
    if (next) selected.set(p.id, p.title);
    else selected.delete(p.id);
    updateBar();
    return true;
  }

  function clearSelection() {
    selected.clear();
    for (const card of grid.querySelectorAll(".pcard.selected")) {
      card.classList.remove("selected");
      const dot = card.querySelector(".sel-dot");
      if (dot) dot.setAttribute("aria-pressed", "false");
    }
    updateBar();
  }

  function updateBar() {
    if (!selected.size) {
      if (bar) {
        const old = bar;
        bar = null;
        animateOut(old).then(() => old.remove());
      }
      return;
    }
    if (!bar) {
      const countEl = h("b", { class: "num" });
      const compare = h("button", { class: "btn primary sm", type: "button", onClick: () => showCompare({ ids: Array.from(selected.keys()) }) }, icon("compare"), "Сравнить");
      bar = h("div", { class: "floatbar glass-panel analysis-selected", role: "region", "aria-label": "Выбранные товары" },
        h("span", { class: "fb-text" }, "Выбрано ", countEl, ` из ${MAX_GROUP} возможных`),
        compare,
        h("button", {class:"btn sm",type:"button",onClick:()=>openAnalysis(Array.from(selected.keys()),'compare')},icon('assistant'),'ИИ-сравнение'),
        h("button", {class:"btn sm",type:"button",onClick:()=>openAnalysis(Array.from(selected.keys()),'quality')},icon('check'),'Качество'),
        h("button", { class: "btn ghost sm", type: "button", onClick: clearSelection }, icon("x"), "Снять выбор"));
      bar.update = () => {
        countEl.textContent = String(selected.size);
        compare.disabled = selected.size < 2;
        compare.title = selected.size < 2 ? "Выберите хотя бы два товара" : "";
      };
      root.append(bar);
    }
    bar.update();
  }

  search.addEventListener("input", debounce(() => {
    prefs.q = search.value;
    reload();
  }, 250));
  source.addEventListener("change", () => {
    prefs.source = source.value;
    reload();
  });
  sort.addEventListener("change", () => {
    prefs.sort = sort.value;
    reload();
  });

  let timer = 0;
  ctx.cleanup(on("data", (event) => {
    const kind = event && event.kind;
    if (kind === "favorites" && prefs.only !== "favorites") return;
    if (!["products", "favorites", "watches", "observation", "inbox"].includes(kind)) return;
    clearTimeout(timer);
    timer = setTimeout(() => fetchPage(true, true), 300);
  }));
  ctx.cleanup(() => clearTimeout(timer));
  updateBar();
  await fetchPage(true);
}
