// R-52: карточка товара. Цена и её история, слежение, сведения со страницы, наблюдения и изменения.
import { analysisPanel } from "../analysis-panel.js";
import { productImage, attachPhoto, photoGallery, setCategory } from "../media.js";
import { h, icon, money, ago, count, number, dateTime } from "../dom.js";
import { get, describe } from "../api.js";
import { on } from "../state.js";
import { modal, toast, empty, skel, ring, segmented, sourceBadge, availabilityBadge, changeChip } from "../ui.js";
import { FIELDS, FIELD_STATES, ROLE_LABELS, BASIS_LABELS, originLabel, evidenceLabel, categoryIcon, sourceName } from "../meta.js";
import { priceChart } from "../chart.js";
import { enter } from "../motion.js";
import { go, cardHead, toggleFavorite, openExternal, addObservation } from "../actions.js";
import { editWatch, addToCompare, showCompare } from "../flows.js";

const HINT_CLASS = { BELOW_USUAL: "ok", ABOVE_USUAL: "warn" };

export async function render(root, ctx) {
  const id = Number.parseInt(ctx.params.id, 10);
  if (!Number.isInteger(id) || id <= 0) {
    root.append(notFound());
    return;
  }
  root.classList.add("product-view");ctx.cleanup(()=>root.classList.remove("product-view"));
  ctx.setHeader("Карточка товара", "");
  root.append(skel(72), h("div", { class: "grid-2" }, skel(260), skel(260)), skel(340));
  let basis = "lowest";
  let observer = null;
  let first = true;
  let timer = 0;

  async function load() {
    let detail;
    try {
      detail = await get(`/api/products/${id}`);
    } catch (error) {
      if (!ctx.alive()) return;
      root.replaceChildren(error.status === 404
        ? notFound()
        : empty({ icon: "warn", title: "Карточка не открылась", text: describe(error), action: { label: "Повторить", icon: "refresh", run: () => load() } }));
      return;
    }
    if (!ctx.alive()) return;
    draw(detail);
  }

  function draw(detail) {
    const p = detail.product;
    const currency = p.currency || "RUB";
    const stats = detail.stats || {};
    const watch = detail.watch;
    ctx.setHeader("Карточка товара", sourceName(p.source));
    const chartBox = h("div", { class: "chart-box" });
    const drawChart = (animate) => {
      const key = basis === "regular" ? "regular_minor" : "lowest_minor";
      const series = (detail.history || [])
        .filter((point) => point[key] !== null && point[key] !== undefined && point.observed_at)
        .map((point) => ({ t: Date.parse(point.observed_at), v: point[key] }));
      const st = basis === "regular" ? stats.regular : stats.lowest;
      priceChart(chartBox, { series, target: watch && watch.target_minor ? watch.target_minor : null, median: st ? st.median : null, currency, animate });
    };
    const cards = [
      h("section",{class:"product-showcase"},(p.photo_count||p.image_url?photoGallery(p):h("button",{class:"photo-placeholder",type:"button",onClick:()=>attachPhoto(p)},icon("plus"),h("span",{text:"Добавить фото"}))),h("div",{class:"product-purchase"},headRow(p),priceCard(p,stats,detail.hint),watchCard(p,watch,detail.groups||[]))),
      analysisPanel({ids:[id],kind:'card'}),
      (detail.history||[]).length>1?chartCard(detail, chartBox, basis, (value) => {basis=value;drawChart(true);}):h('details',{class:'card compact-history'},h('summary',{text:'История цены · '+(detail.history||[]).length+' наблюдений'}),chartCard(detail, chartBox, basis,(value)=>{basis=value;drawChart(true);})),
      h("div", { class: "grid-2" }, fieldsCard(p, detail.latest), observationsCard(detail)),
    ];
    root.replaceChildren(...cards);
    if (first) enter(cards, { y: 14, step: 60 });
    drawChart(first);
    first = false;
    if (observer) observer.disconnect();
    let frame = 0;
    let width = chartBox.clientWidth;
    observer = new ResizeObserver(() => {
      if (chartBox.clientWidth === width) return;
      width = chartBox.clientWidth;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => drawChart(false));
    });
    observer.observe(chartBox);
  }

  ctx.cleanup(() => {
    if (observer) observer.disconnect();
    clearTimeout(timer);
  });
  ctx.cleanup(on("data", (event) => {
    if (!event || !["observation", "watches", "compare", "inbox", "products"].includes(event.kind)) return;
    if (!["compare", "inbox"].includes(event.kind) && event.id !== undefined && event.id !== id) return;
    clearTimeout(timer);
    timer = setTimeout(load, 250);
  }));
  await load();
}

function notFound() {
  return empty({ icon: "search", title: "Товар не найден", text: "Возможно, ссылка устарела.", action: { label: "Ко всем товарам", icon: "products", run: () => go("#/products") } });
}

function headRow(p) {
  const star = h("button", {
    class: ["icon-btn star", p.favorite && "on"],
    type: "button",
    title: "Избранное",
    "aria-pressed": String(Boolean(p.favorite)),
    "aria-label": p.favorite ? "Убрать из избранного" : "Добавить в избранное",
  }, icon("star"));
  star.addEventListener("click", () => toggleFavorite(p, star));
  const sub = [p.listing_id ? `№ ${p.listing_id}` : null, originLabel(p.origin), p.last_seen_at ? `обновлено ${ago(p.last_seen_at)}` : "ещё без наблюдений"].filter(Boolean).join(" · ");
  return h("div", { class: "row wrap" },
    h("button", { class: "icon-btn", type: "button", title: "Ко всем товарам", "aria-label": "Ко всем товарам", onClick: () => go("#/products") }, icon("undo")),
    h("div", { class: "pd-title" },
      h("h2", { text: p.title }),
      h("div", { class: "row wrap" }, sourceBadge(p.source), availabilityBadge(p.availability), h("span", { class: "sub", text: sub }))),
    h("div", { class: "row wrap" },
      star,
      h('details',{class:'action-menu'},h('summary',{class:'btn',text:'Ещё'}),h('div',{class:'action-menu-body'},
        h("button",{class:"btn",type:"button",onClick:()=>attachPhoto(p)},icon("upload"),"Фото"),
        h("button",{class:"btn",type:"button",onClick:()=>setCategory(p)},"Категория"),
        p.url?h("button",{class:"btn",type:"button",onClick:()=>openExternal(p.url)},icon("external"),"Открыть источник"):null)),
      h("button", { class: "btn", type: "button", onClick: () => addToCompare(p) }, icon("compare"), "В сравнение"),
      h("button", { class: "btn primary", type: "button", onClick: () => addObservation(p) }, icon("plus"), "Добавить цену")));
}

function priceCard(p, stats, hint) {
  const currency = p.currency || "RUB";
  const low = stats.lowest;
  const known = p.lowest_minor !== null && p.lowest_minor !== undefined;
  const cap = !known
    ? "Цен пока нет: добавьте вручную или загрузите файл Eye"
    : p.lowest_role && p.lowest_role !== "regular"
      ? `Минимальная показанная цена, ${ROLE_LABELS[p.lowest_role] || "с условием"}`
      : "Обычная цена на странице";
  const stat = (label, value) => h("div", { class: "stat" }, h("small", { text: label }), h("b", { class: "num", text: value }));
  const hintClass = hint ? HINT_CLASS[hint.code] : null;
  return h("section", { class: "card price-hero" },
    h("div", { class: "big num", text: known ? money(p.lowest_minor, currency) : "Цена неизвестна" }),
    h("div", { class: "cap", text: cap }),
    h("div", { class: "row wrap" },
      low && low.change_percent !== null && low.change_percent !== undefined ? changeChip(low.change_percent) : null,
      p.regular_minor && p.regular_minor !== p.lowest_minor ? h("span", { class: "badge", text: `обычная ${money(p.regular_minor, currency)}` }) : null,
      low && low.previous ? h("span", { class: "muted", text: `в прошлый раз ${money(low.previous, currency)}` }) : null),
    hint && hint.text && hint.code!=="NOT_ENOUGH_HISTORY" ? h("div", { class: ["callout", hintClass] }, icon(hintClass === "ok" ? "trendDown" : "info"), h("span", { text: hint.text })) : null,
    low&&low.count>1?h("div", { class: "stat-grid" },
      stat("Минимум", low ? money(low.min, currency) : "-"),
      stat("Медиана", low ? money(low.median, currency) : "-"),
      stat("Максимум", low ? money(low.max, currency) : "-"),
      stat("Наблюдений", number(p.observation_count || 0))):null);
}

function watchCard(p, watch, groups) {
  const currency = p.currency || "RUB";
  const body = [];
  if (watch) {
    const current = watch.price_basis === "regular" ? p.regular_minor : p.lowest_minor;
    const reached = Boolean(watch.target_minor && current && current <= watch.target_minor);
    body.push(h("dl", { class: "kv" },
      h("dt", { text: "Цель" }), h("dd", { class: "num", text: watch.target_minor ? money(watch.target_minor, watch.currency || currency) : "не задана" }),
      h("dt", { text: "Снижение" }), h("dd", { text: watch.drop_percent ? `от ${watch.drop_percent} %` : "не задано" }),
      h("dt", { text: "Считать" }), h("dd", { text: BASIS_LABELS[watch.price_basis] || watch.price_basis }),
      h("dt", { text: "Наличие" }), h("dd", { text: watch.notify_availability ? "сообщу о появлении" : "не слежу" })));
    if (reached) body.push(h("div", { class: "callout ok glow-ok" }, icon("check"), h("span", { text: "Цена уже не выше цели. Перед покупкой сверьте условия на странице магазина." })));
    body.push(h("div", { class: "row wrap" }, h("button", { class: "btn", type: "button", onClick: () => editWatch(p, watch) }, icon("edit"), "Изменить")));
  } else {
    body.push(h("p", { class: "muted", text: "Цель не задана." }));
    body.push(h("div", { class: "row wrap" }, h("button", { class: "btn primary", type: "button", onClick: () => editWatch(p, null) }, icon("target"), "Следить за ценой")));
  }
  if (groups.length) {
    body.push(h("span", { class: "section-label", text: "В сравнениях" }));
    body.push(h("div", { class: "chips" }, groups.map((group) => h("button", { class: "chip", type: "button", onClick: () => showCompare({ groupId: group.id }) }, icon("compare"), group.name || `Список №${group.id}`))));
  }
  const tools = watch ? h("span", { class: ["badge", watch.active && "violet"], text: watch.active ? "включено" : "на паузе" }) : null;
  return h("section", { class: "card stack" }, cardHead({ icon: "target", title: "Слежение за ценой", tools }), body);
}

function chartCard(detail, chartBox, basis, onBasis) {
  const history = detail.history || [];
  const undated = Array.isArray(detail.history_undated) ? detail.history_undated.length : Number(detail.history_undated) || 0;
  const seg = segmented([{ value: "lowest", label: "Минимальная" }, { value: "regular", label: "Обычная" }], basis, onBasis, { size: "sm", label: "Какую цену показать" });
  const legend = h("div", { class: "legend" },
    detail.watch && detail.watch.target_minor ? h("span", null, h("i", { class: "target" }), "цель") : null,
    h("span", null, h("i", { class: "median" }), "медиана"));
  return h("section", { class: "card" },
    cardHead({ icon: "history", title: "История цены", sub: history.length ? count(history.length, "наблюдение", "наблюдения", "наблюдений") : "пока пусто", tools: seg }),
    h("div", { class: "chart-head" }, legend),
    chartBox,
    undated ? h("p", { class: "faint", text: `Ещё ${count(undated, "наблюдение", "наблюдения", "наблюдений")} без даты, на графике их нет.` }) : null);
}

function formatValue(value) {
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (value && typeof value === "object") return Object.values(value).filter((item) => item !== null && item !== undefined && item !== "").map(formatValue).join(", ");
  if (typeof value === "boolean") return value ? "да" : "нет";
  return String(value);
}

function fieldCell(key, label, field) {
  const state = (field && field.state) || "UNKNOWN";
  const meta = FIELD_STATES[state] || FIELD_STATES.UNKNOWN;
  const value = field ? field.value : null;
  let text = "нет данных";
  if (value !== null && value !== undefined && value !== "") text = key === "displayed_price" && typeof value === "number" ? money(value) : formatValue(value);
  return h("div", { class: ["fcell", meta.cls] }, h("small", { text: label }), h("span", { text }), h("em", { text: meta.text }));
}

function pricesBlock(prices, currency) {
  if (!prices.length) return null;
  let best = -1;
  prices.forEach((price, index) => {
    if (price.amount_minor !== null && price.amount_minor !== undefined && (best < 0 || price.amount_minor < prices[best].amount_minor)) best = index;
  });
  return h("div", { class: "stack" },
    h("span", { class: "section-label", text: "Цены на странице" }),
    h("div", { class: "i-prices" }, prices.map((price, index) => h("div", { class: ["ip", index === best && "best"] },
      h("b", { class: "num", text: money(price.amount_minor, price.currency || currency || "RUB") }),
      h("small", { text: price.role_label || ROLE_LABELS[price.role] || price.label || "" })))));
}

function checksBlock(checks, conflicts) {
  const items = [
    ...conflicts.map((item) => ({ text: typeof item === "string" ? item : item.text || item.message || item.field || "Противоречие в данных", bad: true })),
    ...checks.map((item) => ({ text: item.text || item.code, bad: false })),
  ];
  if (!items.length) return null;
  return h("div", { class: "stack" },
    h("span", { class: "section-label", text: "Перед покупкой проверьте" }),
    h("ul", { class: "checks" }, items.map((item) => h("li", { class: item.bad ? "bad" : null }, icon(item.bad ? "warn" : "info"), h("span", { text: item.text })))));
}

function fieldsCard(p, latest) {
  const fields=latest?.fields||{};
  const known=Object.entries(FIELDS).filter(([key])=>key!=='title'&&fields[key]&&fields[key].state!=='UNKNOWN'&&fields[key].value!=null&&fields[key].value!=='');
  const rows=known.map(([key,label])=>{
    const f=fields[key],value=formatValue(f.value),meta=FIELD_STATES[f.state]||FIELD_STATES.UNKNOWN;
    return h('button',{class:'property-row',type:'button',onClick:()=>modal({title:label,width:620,body:h('div',{class:'stack'},h('p',{class:'property-full',text:value}),h('span',{class:'badge',text:meta.text}),latest.observed_at?h('time',{class:'muted',text:dateTime(latest.observed_at)}):null),actions:[{label:'Копировать',run:async()=>{try{await navigator.clipboard.writeText(value);toast({title:'Скопировано'});}catch{toast({title:'Копирование недоступно',kind:'warn'});}return false;}}]})},h('span',{text:label}),h('b',{text:value}),icon('chevronRight'));
  });
  const missing=Object.entries(FIELDS).filter(([key])=>!fields[key]||fields[key].state==='UNKNOWN').map(([,label])=>label);
  return h('section',{class:'card product-properties'},cardHead({icon:'file',title:'О товаре',sub:known.length+' сведений'}),
    rows.length?h('div',{class:'property-list'},rows):h('p',{class:'muted',text:'Сведения не добавлены'}),
    h('details',{class:'compact-details'},h('summary',{text:'Неизвестно · '+missing.length}),h('div',{class:'chips'},missing.map(label=>h('span',{class:'badge',text:label})))),
    (latest?.checks?.length||latest?.conflicts?.length)?h('details',{class:'compact-details'},h('summary',{text:'Проверки и расхождения'}),checksBlock(latest.checks||[],latest.conflicts||[])):null);
}

function changeValue(change, value) {
  if (value === null || value === undefined || value === "") return "нет данных";
  if (typeof value === "number" && /price|minor/.test(`${change.field} ${change.kind}`)) return money(value);
  return formatValue(value);
}

function observationRow(o) {
  return h("div", { class: "list-row" },
    icon(o.origin === "MANUAL" ? "edit" : "file"),
    h("span", { class: "ellipsis" },
      h("b", { text: dateTime(o.observed_at || o.imported_at) }),
      h("small", { class: "muted", text: ` ${originLabel(o.origin)} · ${evidenceLabel(o.evidence_level)}` })),
    h("span", { class: "spacer" }),
    availabilityBadge(o.availability));
}

function observationsCard(detail) {
  const list = detail.observations || [];
  const changes = detail.changes && Array.isArray(detail.changes.changes) ? detail.changes.changes : [];
  const box = h("div", { class: "stack" });
  let showAll = false;
  const more = h("button", { class: "btn sm ghost", type: "button", hidden: list.length <= 8 }, `Показать все: ${list.length}`);
  const fill = () => {
    box.replaceChildren(...list.slice(0, showAll ? list.length : 8).map(observationRow));
    more.hidden = showAll || list.length <= 8;
  };
  more.addEventListener("click", () => {
    showAll = true;
    fill();
  });
  fill();
  return h("section", { class: "card stack" },
    cardHead({ icon: "activity", title: "Наблюдения", sub: count(list.length, "запись", "записи", "записей") }),
    changes.length
      ? h("div", { class: "stack" },
        h("span", { class: "section-label", text: "Что изменилось с прошлого раза" }),
        h("ul", { class: "checks" }, changes.map((change) => h("li", null, icon("activity"),
          h("span", { text: `${change.label || change.field}: ${changeValue(change, change.before)} → ${changeValue(change, change.after)}` })))),
        detail.changes.interpretation ? h("p", { class: "muted", text: detail.changes.interpretation }) : null)
      : null,
    list.length ? box : h("p", { class: "muted", text: "Наблюдений пока нет." }),
    more);
}
