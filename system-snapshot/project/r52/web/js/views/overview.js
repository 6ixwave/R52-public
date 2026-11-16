// R-52: обзор. Приветствие с радаром, ключевые числа, события, избранное, цели, расписание и состояние ядра.
import { productImage } from "../media.js";
import {
  h,
  icon,
  number,
  count,
  money,
  ago,
  dateLong,
  dateTime,
  hourNow,
  bytes,
  capitalize,
} from "../dom.js";
import { get, query } from "../api.js";
import { state, on } from "../state.js";
import { empty, skel } from "../ui.js";
import {
  greeting,
  eventMeta,
  taskKind,
  sourceColor,
  sourceLabel,
} from "../meta.js";
import { enter, countUp, motionOn } from "../motion.js";
import { sparkline } from "../chart.js";
import {
  go,
  cardHead,
  addProduct,
  pickAndImport,
  loadSparks,
  priceParts,
  productCard,
} from "../actions.js";

const COLORS = {
  violet: "#a78bfa",
  cyan: "#22d3ee",
  green: "#34d399",
  amber: "#fbbf24",
};

export async function render(root, ctx) {
  const status = state.status || {};
  // R52 2.12: the widget is React Bits Topography with the Sonar preset
  // (vanilla port in vendor/topography.js). Same box as before; the canvas
  // answers the pointer and the caption opens the city.
  const topoHost = h("div", { class: "topography-host" });
  const radar = h(
    "div",
    {
      class: "radar terrain-preview",
      role: "group",
      "aria-label": "Топография · Sonar",
    },
    topoHost,
    h("button", {
      class: "terrain-caption",
      type: "button",
      text: "Открыть город ↗",
      "aria-label": "Открыть карту предложений",
      onClick: () => go("#/map"),
    }),
  );
  let topo = null;
  ctx.cleanup(() => {
    topo?.destroy();
    topo = null;
  });
  import("../vendor/topography.js")
    .then(({ createTopography, topographyPreset }) => {
      if (!ctx.alive()) return;
      topo = createTopography(topoHost, {
        ...topographyPreset("sonar"),
        reducedMotion: !motionOn(),
      });
      if (!topo.active) {
        topo.destroy();
        topo = null;
        radar.classList.add("topography-fallback");
      }
    })
    .catch(() => radar.classList.add("topography-fallback"));
  const lead = h("p", {
    class: "lead",
    text: leadText(status, { total: 0, reached: 0 }),
  });
  const hero = h(
    "section",
    { class: "card hero" },
    h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "eyebrow" },
        icon("sunrise"),
        capitalize(dateLong(new Date())),
      ),
      h("h2", null, `${greeting(hourNow())}.`),
      h(
        "div",
        { class: "actions row wrap" },
        h(
          "button",
          { class: "btn primary", type: "button", onClick: () => addProduct() },
          icon("plus"),
          "Добавить товар",
        ),
        h(
          "button",
          { class: "btn", type: "button", onClick: () => pickAndImport() },
          icon("upload"),
          "Загрузить файл Eye",
        ),
        h(
          "button",
          {
            class: "btn ghost",
            type: "button",
            onClick: () => go("#/assistant"),
          },
          icon("assistant"),
          "Спросить помощника",
        ),
      ),
    ),
    radar,
  );
  const kpiRow = h("div", { class: "kpis" });
  const left = h("div", { class: "stack" }, skel(260), skel(210));
  const right = h("div", { class: "stack" }, skel(190), skel(230));
  root.append(hero, kpiRow, h("div", { class: "ov-grid" }, left, right));
  enter([hero], { y: 14 });

  const kpis = {
    products: kpi({
      icon: "products",
      color: COLORS.violet,
      label: "товаров в базе",
      href: "#/products",
    }),
    observations: kpi({
      icon: "activity",
      color: COLORS.cyan,
      label: "наблюдений цен",
      href: "#/products",
    }),
    reached: kpi({
      icon: "target",
      color: COLORS.green,
      label: "целей достигнуто",
      href: "#/watch",
    }),
    inbox: kpi({
      icon: "inbox",
      color: COLORS.amber,
      label: "файлов ждут проверки",
      href: "#/inbox",
    }),
  };
  const kpiEls = Object.values(kpis).map((item) => item.el);
  kpiRow.append(...kpiEls);
  enter(kpiEls, { y: 12, step: 60, delay: 80 });
  const applyStatus = (value) => {
    const db = (value && value.database) || {};
    kpis.products.set(db.products || 0);
    kpis.observations.set(db.observations || 0);
    kpis.inbox.set((value && value.inbox_pending) || 0);
  };
  applyStatus(status);

  let first = true;
  let timer = 0;
  const fill = async () => {
    const data = await load();
    if (!ctx.alive()) return;
    const watchInfo = {
      total: data.watches.length,
      reached: data.watches.filter((item) => item.target_reached).length,
    };
    kpis.reached.set(
      watchInfo.reached,
      watchInfo.total
        ? `целей достигнуто из ${number(watchInfo.total)}`
        : "целей пока нет",
    );
    lead.textContent = leadText(state.status || status, watchInfo);

    const byId = new Map(data.products.map((p) => [p.id, p]));
    const sparks = await loadSparks(data.favorites.map((p) => p.id));
    if (!ctx.alive()) return;
    left.replaceChildren(
      feedCard(data.events, byId),
      favoritesCard(data.favorites, sparks),
    );
    right.replaceChildren(
      watchesCard(data.watches),
      scheduleCard(data.schedules),
      healthCard(state.status || status),
    );
    if (first) {
      first = false;
      enter([...left.children, ...right.children], { y: 16, step: 70 });
    }
  };
  ctx.cleanup(on("status", applyStatus));
  ctx.cleanup(
    on("data", () => {
      clearTimeout(timer);
      timer = setTimeout(fill, 350);
    }),
  );
  ctx.cleanup(() => clearTimeout(timer));
  await fill();
}

async function load() {
  const safe = (promise, fallback) => promise.catch(() => fallback);
  const [products, events, favorites, watches, schedules] = await Promise.all([
    safe(get(`/api/products${query({ limit: 200, sort: "recent" })}`), {
      items: [],
    }),
    safe(get("/api/events?limit=8"), { items: [] }),
    safe(get("/api/favorites"), { items: [] }),
    safe(get("/api/watches"), { items: [] }),
    safe(get("/api/schedules"), { items: [] }),
  ]);
  return {
    products: products.items || [],
    events: events.items || [],
    favorites: (favorites.items || []).slice(0, 6),
    watches: watches.items || [],
    schedules: schedules.items || [],
  };
}

function leadText(status, watchInfo) {
  const db = status.database || {};
  const parts = [
    `В базе ${count(db.products || 0, "товар", "товара", "товаров")} и ${count(db.observations || 0, "наблюдение", "наблюдения", "наблюдений")} цен.`,
  ];
  if (watchInfo.total) {
    parts.push(
      watchInfo.reached
        ? `Достигнуто целей: ${number(watchInfo.reached)} из ${number(watchInfo.total)}.`
        : `Под слежением ${count(watchInfo.total, "товар", "товара", "товаров")}, цели пока не достигнуты.`,
    );
  }
  if (status.inbox_pending)
    parts.push(
      `Во входящих ждут проверки ${count(status.inbox_pending, "файл", "файла", "файлов")}.`,
    );
  return parts.join(" ");
}

function kpi({ icon: name, color, label, href }) {
  const value = h("div", { class: "k-val num", text: "0" });
  const text = h("div", { class: "k-label", text: label });
  const el = h(
    "button",
    {
      class: "card kpi spot",
      type: "button",
      style: { "--c": color },
      onClick: () => go(href),
    },
    h("div", { class: "k-icon", style: { "--c": color } }, icon(name)),
    value,
    text,
  );
  let current = null;
  return {
    el,
    set(next, labelText) {
      if (labelText) text.textContent = labelText;
      if (next === current) return;
      current = next;
      countUp(value, next, { format: (v) => number(v) });
    },
  };
}

// Отметки на радаре: место зависит только от товара, поэтому между обновлениями они не прыгают.
function drawBlips(radar, products) {
  for (const old of radar.querySelectorAll(".blip")) old.remove();
  products.slice(0, 10).forEach((p, index) => {
    const seed = spread(`${p.id}:${p.title}`);
    const angle = (seed % 360) * (Math.PI / 180);
    const radius = 14 + ((seed >>> 9) % 30);
    radar.append(
      h("span", {
        class: "blip",
        style: {
          "--bx": `${(50 + radius * Math.cos(angle)).toFixed(1)}%`,
          "--by": `${(50 + radius * Math.sin(angle)).toFixed(1)}%`,
          "--c": sourceColor(p.source),
          "animation-delay": `${(index * 0.37).toFixed(2)}s`,
        },
      }),
    );
  });
}

function spread(text) {
  let value = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    value ^= text.charCodeAt(i);
    value = Math.imul(value, 16777619);
  }
  return value >>> 0;
}

function moreButton(label, hash) {
  return h("span", { hidden: true, dataset: { objectRoute: hash } });
}

function feedCard(events, byId) {
  const head = cardHead({
    icon: "activity",
    title: "Последние события",
    tools: moreButton("К слежению", "#/watch"),
  });
  if (!events.length) {
    return h(
      "section",
      { class: "card" },
      head,
      empty({
        icon: "activity",
        title: "Событий пока нет",
        text: "Они появятся, когда цена дойдёт до цели, снизится или товар появится в наличии.",
      }),
    );
  }
  const rows = events.map((event) => {
    const meta = eventMeta(event.kind);
    const product = byId.get(event.product_id);
    const payload = event.payload || {};
    const currency = payload.currency || "RUB";
    const detail =
      payload.value_minor !== undefined && payload.value_minor !== null
        ? `${money(payload.value_minor, currency)}${payload.target_minor ? ` при цели ${money(payload.target_minor, currency)}` : ""}`
        : "";
    return h(
      "button",
      {
        class: "feed-item",
        type: "button",
        onClick: () => {
          if (event.product_id) go(`#/products/${event.product_id}`);
        },
      },
      h(
        "span",
        { class: "f-icon", style: { "--c": meta.color } },
        icon(meta.icon),
      ),
      h(
        "div",
        null,
        h("b", { text: meta.text }),
        h("small", {
          class: "ellipsis",
          text: [product ? product.title : `товар №${event.product_id}`, detail]
            .filter(Boolean)
            .join(" · "),
        }),
      ),
      h("small", { class: "faint", text: ago(event.created_at) }),
    );
  });
  return h(
    "section",
    { class: "card" },
    head,
    h("div", { class: "feed" }, rows),
  );
}

function favoritesCard(favorites, sparks) {
  const head = cardHead({
    icon: "star",
    title: "Избранное",
    tools: moreButton("Все", "#/watch/favorites"),
  });
  if (!favorites.length) {
    return h(
      "section",
      { class: "card" },
      head,
      empty({
        icon: "star",
        title: "В избранном пусто",
        text: "Отмечайте товары звёздочкой, чтобы держать их под рукой.",
      }),
    );
  }
  const rows = favorites.map((p) =>
    productCard(p, { spark: sparks[p.id] || [] }),
  );
  return h(
    "section",
    { class: "card overview-favorites" },
    head,
    h("div", { class: "overview-market-grid" }, rows),
  );
}

function progress(item) {
  if (item.target_reached) return 1;
  const target = item.watch && item.watch.target_minor;
  const now = item.current_minor;
  if (!target || !now) return 0;
  return Math.max(0, Math.min(1, target / now));
}

function watchesCard(items) {
  const head = cardHead({
    icon: "target",
    title: "Цели слежения",
    tools: moreButton("Все", "#/watch"),
  });
  if (!items.length) {
    return h(
      "section",
      { class: "card" },
      head,
      empty({
        icon: "target",
        title: "Слежения пока нет",
        text: "Откройте товар и нажмите «Следить за ценой».",
        action: {
          label: "К товарам",
          icon: "products",
          run: () => go("#/products"),
        },
      }),
    );
  }
  const rows = [...items]
    .sort((a, b) => progress(b) - progress(a))
    .slice(0, 4)
    .map((item) => {
      const watch = item.watch || {};
      const product = item.product || {};
      const currency = watch.currency || product.currency || "RUB";
      const fraction = progress(item);
      const gap =
        item.current_minor && watch.target_minor
          ? item.current_minor - watch.target_minor
          : null;
      const goal = watch.target_minor
        ? money(watch.target_minor, currency)
        : watch.drop_percent
          ? `−${watch.drop_percent} %`
          : "-";
      return h(
        "button",
        {
          class: "feed-item",
          type: "button",
          onClick: () => go(`#/products/${product.id}`),
        },
        h(
          "span",
          {
            class: "f-icon",
            style: {
              "--c": item.target_reached ? COLORS.green : COLORS.violet,
            },
          },
          icon(item.target_reached ? "check" : "target"),
        ),
        h(
          "div",
          null,
          h("b", { class: "ellipsis", text: product.title }),
          h("small", {
            text: `сейчас ${item.current_minor ? money(item.current_minor, currency) : "нет цены"} · цель ${goal}`,
          }),
          h(
            "div",
            {
              class: ["bar", item.target_reached && "ok"],
              style: { "--p": fraction.toFixed(3) },
            },
            h("i"),
          ),
        ),
        h("small", {
          class: "num",
          text: item.target_reached
            ? "достигнута"
            : gap && gap > 0
              ? `ещё ${money(gap, currency)}`
              : "",
        }),
      );
    });
  return h(
    "section",
    { class: "card" },
    head,
    h("div", { class: "feed" }, rows),
  );
}

function scheduleCard(items) {
  const head = cardHead({
    icon: "schedule",
    title: "Расписание",
    tools: moreButton("Задачи", "#/tasks"),
  });
  if (!items.length)
    return h(
      "section",
      { class: "card" },
      head,
      h("p", { class: "muted", text: "Регулярных задач нет." }),
    );
  const sorted = [...items].sort((a, b) =>
    String(a.next_due_at || "~").localeCompare(String(b.next_due_at || "~")),
  );
  return h(
    "section",
    { class: "card" },
    head,
    h(
      "div",
      { class: "timeline" },
      sorted.map((item) => {
        const enabled = Boolean(item.enabled);
        const rule =
          item.rule === "daily" && item.daily_time
            ? `каждый день в ${item.daily_time}`
            : item.interval_seconds
              ? `каждые ${number(item.interval_seconds / 3600, 1)} ч`
              : "";
        const next = item.next_due_at
          ? `следующий запуск ${dateTime(item.next_due_at)}`
          : "";
        return h(
          "div",
          { class: ["tl-item", !enabled && "off"] },
          h("b", { text: taskKind(item.kind).title }),
          h("small", {
            text: enabled
              ? [rule, next].filter(Boolean).join(" · ")
              : "выключено в настройках",
          }),
        );
      }),
    ),
  );
}

function componentText(value) {
  if (value === true || value === "ok" || value === "running")
    return "работает";
  if (value === false || value === null || value === undefined)
    return "остановлено";
  if (typeof value === "object")
    return value.ok === false ? "сбой" : "работает";
  return String(value);
}

function healthCard(status) {
  const components = (status.health && status.health.components) || {};
  const intake = status.intake || {};
  const backups = status.backups || {};
  const last =
    backups.last && typeof backups.last === "object"
      ? backups.last.created_at
      : backups.last;
  const cells = [
    [
      "Ядро",
      `${status.version || "-"}, запущено ${status.started_at ? ago(status.started_at) : "-"}`,
    ],
    ["Фоновые задачи", componentText(components.worker)],
    ["Расписание", componentText(components.scheduler)],
    [
      "Папка входящих",
      intake.enabled === false ? "выключена" : componentText(components.intake),
    ],
    ["База данных", bytes(status.database && status.database.size_bytes)],
    [
      "Резервные копии",
      backups.count
        ? `${number(backups.count)}, последняя ${last ? ago(last) : "-"}`
        : "ещё не было",
    ],
  ];
  const problems = (status.problems || []).map((problem) =>
    h(
      "div",
      { class: "callout warn" },
      icon("warn"),
      h("span", {
        text:
          typeof problem === "string"
            ? problem
            : problem.text || problem.message || problem.code || "Замечание",
      }),
    ),
  );
  return h(
    "section",
    { class: "card" },
    cardHead({
      icon: "pulse",
      title: "Состояние",
      tools: moreButton("Сервис", "#/service"),
    }),
    h(
      "div",
      { class: "health-grid" },
      cells.map(([label, value]) =>
        h(
          "div",
          { class: "health" },
          h("small", { text: label }),
          h("b", { text: value }),
        ),
      ),
    ),
    problems,
  );
}
