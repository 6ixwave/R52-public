import { activateObject } from "../object-interactions.js";
// R-52: слежение. Цели по цене, избранное и сохранённые сравнения.
import {
  productImage,
  attachPhoto,
  photoGallery,
  setCategory,
} from "../media.js";
import { h, icon, money, ago, count, number } from "../dom.js";
import { get, del, describe } from "../api.js";
import { on, emit } from "../state.js";
import { empty, skel, segmented, confirm, fail, toast } from "../ui.js";
import { BASIS_LABELS, categoryIcon, sourceLabel } from "../meta.js";
import { enter, collapse } from "../motion.js";
import { go, productCard, loadSparks } from "../actions.js";
import { editWatch, setWatchActive, showCompare } from "../flows.js";

const TABS = [
  { value: "goals", label: "Цели", icon: "target" },
  { value: "favorites", label: "Избранное", icon: "star" },
  { value: "compare", label: "Сравнения", icon: "compare" },
];
const SUB = "Цели, избранное и сравнения";
const TO_PRODUCTS = {
  label: "К товарам",
  icon: "products",
  run: () => go("#/products"),
};

export async function render(root, ctx) {
  const tab = ["favorites", "compare"].includes(ctx.params.id)
    ? ctx.params.id
    : "goals";
  const tabs = segmented(
    TABS,
    tab,
    (value) => go(value === "goals" ? "#/watch" : `#/watch/${value}`),
    { label: "Раздел" },
  );
  const body = h("div", { class: "stack" }, skel(170), skel(170));
  root.append(h("div", { class: "toolbar" }, tabs), body);
  const load = {
    goals: loadGoals,
    favorites: loadFavorites,
    compare: loadCompare,
  }[tab];
  let timer = 0;
  ctx.cleanup(
    on("data", (event) => {
      if (
        !event ||
        ![
          "watches",
          "favorites",
          "compare",
          "observation",
          "inbox",
          "products",
        ].includes(event.kind)
      )
        return;
      clearTimeout(timer);
      timer = setTimeout(() => load(body, ctx, true), 300);
    }),
  );
  ctx.cleanup(() => clearTimeout(timer));
  await load(body, ctx, false);
}

async function fetchOr(body, ctx, path, retry) {
  try {
    return await get(path);
  } catch (error) {
    if (ctx.alive())
      body.replaceChildren(
        empty({
          icon: "warn",
          title: "Не загрузилось",
          text: describe(error),
          action: { label: "Повторить", icon: "refresh", run: retry },
        }),
      );
    return null;
  }
}

function progress(item) {
  if (item.target_reached) return 1;
  const target = item.watch && item.watch.target_minor;
  if (!target || !item.current_minor) return 0;
  return Math.max(0, Math.min(1, target / item.current_minor));
}

async function loadGoals(body, ctx, quiet) {
  const data = await fetchOr(body, ctx, "/api/watches", () =>
    loadGoals(body, ctx, false),
  );
  if (!data || !ctx.alive()) return;
  const items = data.items || [];
  const reached = items.filter((item) => item.target_reached).length;
  ctx.setHeader(
    "Слежение",
    items.length
      ? `${count(items.length, "цель", "цели", "целей")}, достигнуто ${number(reached)}`
      : SUB,
  );
  if (!items.length) {
    body.replaceChildren(
      empty({
        icon: "target",
        title: "Целей пока нет",
        text: "Выберите товар для слежения.",
        action: TO_PRODUCTS,
      }),
    );
    return;
  }
  const sorted = [...items].sort(
    (a, b) =>
      Number(Boolean(b.target_reached)) - Number(Boolean(a.target_reached)) ||
      Number(b.watch.active !== false) - Number(a.watch.active !== false) ||
      progress(b) - progress(a),
  );
  const cards = sorted.map(watchCard);
  const parts = [];
  if (data.caveat)
    parts.push(
      h(
        "div",
        { class: "callout warn" },
        icon("info"),
        h("span", { text: data.caveat }),
      ),
    );
  parts.push(h("div", { class: "watch-grid" }, cards));
  body.replaceChildren(...parts);
  if (!quiet) enter(cards, { y: 12, step: 50, maxSteps: 10 });
}

function watchCard(item) {
  const watch = item.watch || {};
  const product = item.product || {};
  const currency = watch.currency || product.currency || "RUB";
  const reached = Boolean(item.target_reached);
  const active = watch.active !== false;
  const badge = reached
    ? h("span", { class: "badge ok glow-ok", text: "цель достигнута" })
    : h("span", {
        class: ["badge", active && "violet"],
        text: active ? "слежу" : "на паузе",
      });
  const pauseText = active ? "Поставить на паузу" : "Возобновить";
  const pause = h(
    "button",
    {
      class: "icon-btn sm",
      type: "button",
      title: pauseText,
      "aria-label": pauseText,
    },
    icon(active ? "pause" : "play"),
  );
  pause.addEventListener("click", () => setWatchActive(watch, !active));
  const remove = h(
    "button",
    {
      class: "icon-btn sm",
      type: "button",
      title: "Перестать следить",
      "aria-label": "Перестать следить",
    },
    icon("trash"),
  );
  const goal = watch.target_minor
    ? money(watch.target_minor, currency)
    : watch.drop_percent
      ? `−${watch.drop_percent} %`
      : "-";
  const el = h(
    "article",
    {
      class: [
        "card wcard",
        `market-${product.source}`,
        reached && "reached",
        !active && "paused",
      ],
    },
    productImage(product),
    h(
      "div",
      { class: "w-head" },
      h(
        "div",
        null,
        h("h4", { class: "ellipsis", text: product.title }),
        h("small", { class: "muted", text: sourceLabel(product.source) }),
      ),
      badge,
    ),
    h(
      "div",
      { class: "w-prices" },
      h(
        "div",
        null,
        h("small", { text: "Сейчас" }),
        h("b", {
          class: "num",
          text: item.current_minor
            ? money(item.current_minor, currency)
            : "нет цены",
        }),
      ),
      h(
        "div",
        { class: "w-target" },
        h("small", { text: "Цель" }),
        h("b", { class: "num", text: goal }),
      ),
    ),
    h(
      "div",
      {
        class: ["bar", reached && "ok"],
        style: { "--p": progress(item).toFixed(3) },
      },
      h("i"),
    ),
    h(
      "div",
      { class: "w-meta" },
      h("span", { text: BASIS_LABELS[watch.price_basis] || "" }),
      watch.drop_percent && watch.target_minor
        ? h("span", { text: `или снижение от ${watch.drop_percent} %` })
        : null,
      watch.notify_availability
        ? h("span", null, icon("package2"), "сообщу о наличии")
        : null,
      h("span", {
        class: "w-when",
        text: `изменено ${ago(watch.updated_at || watch.created_at)}`,
      }),
    ),
    h(
      "div",
      { class: "w-actions" },
      h(
        "button",
        {
          class: "btn sm ghost",
          type: "button",
          onClick: () => editWatch(product, watch),
        },
        icon("edit"),
        "Изменить",
      ),
      h("span", { class: "spacer" }),
      pause,
      remove,
    ),
  );
  remove.addEventListener("click", async () => {
    const ok = await confirm({
      title: "Перестать следить?",
      text: `Цель для «${product.title}» будет удалена. История цен останется.`,
      ok: "Перестать следить",
      danger: true,
    });
    if (!ok) return;
    try {
      await del(`/api/watches/${product.id}`);
    } catch (error) {
      fail(error, "Не удалось снять слежение");
      return;
    }
    toast({ title: "Слежение снято", text: product.title, timeout: 2600 });
    await collapse(el);
    emit("data", { kind: "watches", id: product.id });
  });
  activateObject(el, () => go(`#/products/${product.id}`), {
    label: product.title,
  });
  return el;
}

async function loadFavorites(body, ctx, quiet) {
  const data = await fetchOr(body, ctx, "/api/favorites", () =>
    loadFavorites(body, ctx, false),
  );
  if (!data || !ctx.alive()) return;
  const items = data.items || [];
  ctx.setHeader(
    "Слежение",
    items.length
      ? `В избранном ${count(items.length, "товар", "товара", "товаров")}`
      : SUB,
  );
  if (!items.length) {
    body.replaceChildren(
      empty({
        icon: "star",
        title: "В избранном пусто",
        text: "",
        action: TO_PRODUCTS,
      }),
    );
    return;
  }
  const cards = items.map((p) => productCard(p, { note: p.note }));
  body.replaceChildren(h("div", { class: "prod-grid" }, cards));
  if (!quiet) enter(cards, { y: 12, step: 30, maxSteps: 12 });
  const sparks = await loadSparks(items.map((p) => p.id));
  if (!ctx.alive()) return;
  for (const card of cards) card.setSpark(sparks[card.dataset.id] || []);
}

async function loadCompare(body, ctx, quiet) {
  const data = await fetchOr(body, ctx, "/api/compare", () =>
    loadCompare(body, ctx, false),
  );
  if (!data || !ctx.alive()) return;
  const items = data.items || [];
  ctx.setHeader(
    "Слежение",
    items.length
      ? `Сохранено ${count(items.length, "сравнение", "сравнения", "сравнений")}`
      : SUB,
  );
  if (!items.length) {
    body.replaceChildren(
      empty({
        icon: "compare",
        title: "Сравнений пока нет",
        text: "Выберите товары для сравнения.",
        action: TO_PRODUCTS,
      }),
    );
    return;
  }
  const cards = items.map(compareCard);
  body.replaceChildren(h("div", { class: "watch-grid" }, cards));
  if (!quiet) enter(cards, { y: 12, step: 50, maxSteps: 10 });
}

function compareCard(group) {
  const remove = h(
    "button",
    {
      class: "icon-btn sm",
      type: "button",
      title: "Удалить сравнение",
      "aria-label": "Удалить сравнение",
    },
    icon("trash"),
  );
  const el = h(
    "article",
    { class: "card wcard" },
    h(
      "div",
      { class: "w-head" },
      h("div", { class: "p-cat" }, icon("compare")),
      h(
        "div",
        null,
        h("h4", {
          class: "ellipsis",
          text: group.name || `Сравнение №${group.id}`,
        }),
        h("small", {
          class: "muted",
          text: `${count(group.size || 0, "товар", "товара", "товаров")} · создано ${ago(group.created_at)}`,
        }),
      ),
    ),
    h("div", { class: "w-actions" }, h("span", { class: "spacer" }), remove),
  );
  remove.addEventListener("click", async () => {
    const ok = await confirm({
      title: "Удалить сравнение?",
      text: `«${group.name}» исчезнет из списка. Сами товары и их цены останутся.`,
      ok: "Удалить",
      danger: true,
    });
    if (!ok) return;
    try {
      await del(`/api/compare/${group.id}`);
    } catch (error) {
      fail(error, "Не удалось удалить сравнение");
      return;
    }
    toast({ title: "Сравнение удалено", text: group.name, timeout: 2600 });
    await collapse(el);
    emit("data", { kind: "compare", id: group.id });
  });
  activateObject(el, () => showCompare({ groupId: group.id }), {
    label: group.name || `Сравнение №${group.id}`,
    role: "button",
    dialog: true,
  });
  return el;
}
