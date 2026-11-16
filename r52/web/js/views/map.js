import { h } from "../dom.js";
import {installCityLayout} from "../city-layout.js";
import { get, query } from "../api.js";
import { on } from "../state.js";
import {
  buildTopology,
  contextPrompt,
  contextIds,
  categoryFor,
} from "../city-model.js";
export { categoryFor };
export async function render(root, ctx) {
  let engine = null,
    model = null,
    items = [],
    current = null,
    searchValue = sessionStorage.getItem("r52-city-search") || "",
    sourceValue = sessionStorage.getItem("r52-city-source") || "",
    maxValue = sessionStorage.getItem("r52-city-max") || "",
    seq = 0,
    closed = false;
  const saved = "root"; // Every entry starts with a readable whole-city view.
  let chosen = saved,
    lastRoute = sessionStorage.getItem("r52-before-map") || "#/overview";
  if (lastRoute.includes("/map")) lastRoute = "#/overview";
  const stage = h("div", { class: "city-stage" }),
    message = h("div", {
      class: "city-loading",
      "aria-live": "polite",
      text: "Загрузка…",
    }),
    workspace = h("section", { class: "city-workspace" }),
    title = h("h1", { text: "Город" }),
    counter = h("span", { class: "city-stats", "aria-live": "polite" }),
    path = h("nav", {
      class: "city-breadcrumb",
      "aria-label": "Расположение на карте",
    }),
    areas = h("section", {
      class: "city-areas",
      hidden: true,
      "aria-label": "Навигатор областей",
    }),
    areaList = h("div", { class: "city-area-list" }),
    areaHeading = h("h2", { text: "Области" }),
    selectedTitle = h("strong", { text: "Карта вашей базы" }),
    selectedSub = h("span", { text: "Районы → кварталы → товары" }),
    actions = h("div", { class: "city-context-actions" });
  const search = h("input", {
      class: "input",
      type: "search",
      value: searchValue,
      placeholder: "Поиск",
      "aria-label": "Найти на карте",
    }),
    source = h(
      "select",
      { class: "input", value: sourceValue, "aria-label": "Площадка" },
      ...[
        ["", "Все сайты"],
        ["WB", "Wildberries"],
        ["OZON", "Ozon"],
        ["ALIEXPRESS", "AliExpress"],
        ["AVITO", "Авито"],
        ["FARPOST", "Фарпост"],
        ["MANUAL", "Вручную"],
      ].map(([v, t]) => h("option", { value: v, text: t })),
    );
  const maxPrice = h("input", {
    class: "input city-max",
    type: "number",
    min: 0,
    step: 1,
    value: maxValue,
    placeholder: "До, ₽",
    "aria-label": "Максимальная цена в рублях",
  });
  const close = () => (location.hash = lastRoute),
    go = (entity) => select(entity),
    ask = (entity) => {
      if (!entity?.products?.length && !entity?.product) return;
      sessionStorage.setItem("r52-city-selection", entity.key);
      location.hash =
        "#/assistant/new?ask=" +
        encodeURIComponent(
          contextPrompt(entity, { query: searchValue, source: sourceValue }),
        ) +
        "&scope=" +
        encodeURIComponent(contextIds(entity).join(","));
    };
  function toggleAreas() {
    areas.hidden = !areas.hidden;
    areaButton.setAttribute("aria-expanded", String(!areas.hidden));
    if (!areas.hidden){workspace.dispatchEvent(new CustomEvent("city-panel-request",{detail:{kind:"areas"}}));areas.querySelector("button")?.focus();}
  }
  const areaButton = h("button", {
      class: "btn sm",
      type: "button",
      text: "Области",
      "aria-expanded": "false",
      onClick: toggleAreas,
    }),
    home = h("button", {
      class: "btn sm",
      type: "button",
      text: "⌂ Весь город",
      onClick: () => model && select(model.root),
    });
  areas.append(
    h(
      "header",
      null,
      areaHeading,
      h("button", {
        class: "icon-btn",
        type: "button",
        text: "×",
        "aria-label": "Закрыть навигатор",
        onClick: toggleAreas,
      }),
    ),
    areaList,
  );
  const header = h(
    "header",
    { class: "city-header" },
    h("button", {
      class: "btn sm",
      type: "button",
      text: "← Назад",
      "aria-label": "Закрыть 3D-карту",
      onClick: close,
    }),
    h("div", { class: "city-brand" }, title),
    counter,
    h("div", { class: "city-header-tools" }, home, areaButton),
  );
  const toolbar = h(
    "div",
    { class: "city-toolbar" },
    path,
    h("div", { class: "city-filters" }, search, source, maxPrice),
  );
  const controls = h(
    "div",
    { class: "city-navigation", "aria-label": "Управление камерой: WASD — полёт, Shift — быстрее", title: "WASD — полёт · Shift — быстрее · Правая кнопка мыши — поворот" },
    h("button", {
      type: "button",
      "aria-label": "Приблизить",
      text: "+",
      onClick: () => engine?.zoom(0.78),
    }),
    h("button", {
      type: "button",
      "aria-label": "Отдалить",
      text: "−",
      onClick: () => engine?.zoom(1.28),
    }),
    h("span", { hidden: true, text: "" }),
  );
  const body = h(
    "div",
    { class: "city-space" },
    stage,
    message,
    areas,
    controls,
    h("div", {
      class: "city-compass",
      "aria-hidden": "true",
      text: "N\n◇\nR52",
    }),
  );
  const context = h(
    "footer",
    { class: "city-context" },
    h("div", { class: "city-context-title" }, selectedTitle, selectedSub),
    actions,
  );
  let quiet = false;
  const clean = h("button", {
    class: "city-clean-button",
    type: "button",
    "aria-label": "Только город",
    "aria-pressed": "false",
    title: "Скрыть интерфейс · Esc — вернуть",
    text: "◌ Только город",
    onClick: () => setQuiet(!quiet),
  });
  workspace.append(header, toolbar, body, context, clean);
  root.append(workspace);
  const panelRequested=event=>{if(event.detail?.kind!=='areas'){areas.hidden=true;areaButton.setAttribute('aria-expanded','false');}};
  workspace.addEventListener('city-panel-request',panelRequested);
  ctx.cleanup(()=>workspace.removeEventListener('city-panel-request',panelRequested));
  ctx.cleanup(installCityLayout(workspace));
  function setQuiet(value) {
    quiet = Boolean(value) && !graphicsFailure;
    workspace.classList.toggle("city-quiet", quiet);
    for (const panel of [header, toolbar, context, controls, areas])
      panel.inert = quiet;
    clean.textContent = quiet ? "◌ Показать интерфейс" : "◌ Только город";
    clean.setAttribute("aria-pressed", String(quiet));
    clean.setAttribute(
      "aria-label",
      quiet ? "Показать интерфейс" : "Только город",
    );
    if (quiet) {
      areas.hidden = true;
      areaButton.setAttribute("aria-expanded", "false");
    }
    engine?.setImmersive(quiet);
    sessionStorage.setItem("r52-city-quiet", String(quiet));
  }

  function select(entity) {
    if (!ctx.alive() || !model) return;
    current = entity;
    workspace.dataset.selection = entity.kind;
    chosen = entity.key;
    sessionStorage.setItem("r52-city-selection", chosen);
    drawContext();
    engine?.setSelection(entity);
    drawAreas();
  }
  function drawContext() {
    if (!current) return;
    const entity = current;
    path.replaceChildren(
      h("button", {
        type: "button",
        text: "Весь город",
        onClick: () => select(model.root),
      }),
    );
    let chain = [],
      n = entity.kind === "product" ? entity.parent : entity;
    while (n && n.kind !== "root") {
      chain.unshift(n);
      n = n.parent;
    }
    for (const node of chain)
      path.append(
        h("button", {
          type: "button",
          text: "› " + node.name,
          onClick: () => select(node),
        }),
      );
    if (entity.kind === "product") path.append(h("span", { text: "› Товар" }));
    selectedTitle.textContent =
      entity.kind === "root"
        ? "Город"
        : entity.name +
          (entity.kind === "area" && !entity.products.length
            ? " · пока пусто"
            : "");
    selectedSub.textContent =
      entity.kind === "product" ? "" : String(entity.products.length);
    const buttons = [];
    if (entity.kind === "product")
      buttons.push(
        h("button", {
          class: "btn sm",
          type: "button",
          text: "Открыть товар ↗",
          onClick: () => (location.hash = `#/products/${entity.product.id}`),
        }),
      );
    if (entity.product || entity.products?.length)
      buttons.push(
        h("button", {
          class: "btn sm primary",
          type: "button",
          text: "Спросить об этом",
          onClick: () => ask(entity),
        }),
      );
    actions.replaceChildren(...buttons);
  }
  function drawAreas() {
    if (!current) return;
    const scope = current.kind === "product" ? current.parent : current;
    areaHeading.textContent = scope.kind === "root" ? "Области" : scope.name;
    const parentButton=scope.parent?h('button',{class:'city-area-option',type:'button',text:'← '+(scope.parent.kind==='root'?'Весь город':scope.parent.name),onClick:()=>select(scope.parent)}):null;
    const entries = scope.children?.length
      ? scope.children
      : scope.products
          ?.slice(0, 200)
          .map(
            (p) =>
              model.byKey.get("product:" + p.id) || {
                kind: "unmapped",
                name: p.title,
                product: p,
                key: "unmapped:" + p.id,
              },
          ) || [];
    areaList.replaceChildren(
      ...(parentButton?[parentButton]:[]),
      ...entries.map((entity) =>
        h(
          "button",
          {
            class: "city-area-option",
            type: "button",
            onClick: () => {
              if (entity.kind === "unmapped") {
                location.hash = `#/products/${entity.product.id}`;
                return;
              }
              select(entity);
              if(entity.kind!=='product'){
                areas.hidden=false;areaButton.setAttribute('aria-expanded','true');workspace.dispatchEvent(new CustomEvent('city-panel-request',{detail:{kind:'areas'}}));
              }
            },
          },
          h("i", { style: { "--area-color": entity.color || "#85d7e4" } }),
          h("span", { text: entity.name }),
          h("small", {
            text: entity.kind === "area" ? String(entity.products.length) : "↗",
          }),
        ),
      ),
    );
    if (!entries.length)
      areaList.append(
        h("p", { class: "muted", text: "Нет записей по этим фильтрам." }),
      );
    if (scope.products?.length > 200 && !scope.children.length)
      areaList.append(
        h("a", { href: "#/products", text: "Все товары — в каталоге ↗" }),
      );
  }
  async function show() {
    if (!ctx.alive()) return;
    const filtered = items.filter(
      (p) =>
        (!sourceValue || p.source === sourceValue) &&
        (!maxValue ||
          (p.currency === "RUB" &&
            p.lowest_minor != null &&
            p.lowest_minor <= Number(maxValue) * 100)) &&
        (!searchValue ||
          (p.title || "").toLocaleLowerCase().includes(searchValue)),
    );
    model = buildTopology(filtered, { includeEmpty: true });
    counter.textContent = `${filtered.length} товаров · ${model.root.children.length} районов`;
    engine?.setData(model);
    current = model.byKey.get(chosen) || model.root;
    chosen = current.key;
    workspace.dataset.selection = current.kind;
    sessionStorage.setItem("r52-city-selection", chosen);
    drawContext();
    engine?.setSelection(current);
    drawAreas();
    message.hidden = Boolean(engine && !graphicsFailure);
    message.classList.toggle("city-empty", !filtered.length);
    if (!filtered.length)
      message.replaceChildren(
        h("strong", {
          text: items.length ? "Нет товаров по этим фильтрам" : "Нет товаров",
        }),
        h("p", {
          text: items.length
            ? "Измените поиск или площадку."
            : "Добавьте товары через Eye или вручную.",
        }),
        h("a", { class: "btn", href: "#/inbox", text: "Открыть входящие" }),
      );
  }
  let filterTimer = 0;
  search.addEventListener("input", () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      searchValue = search.value.trim().toLocaleLowerCase();
      sessionStorage.setItem("r52-city-search", searchValue);
      chosen = "root";
      show();
    }, 220);
  });
  source.addEventListener("change", () => {
    sourceValue = source.value;
    sessionStorage.setItem("r52-city-source", sourceValue);
    chosen = "root";
    show();
  });
  maxPrice.addEventListener("input", () => {
    maxValue = maxPrice.value;
    sessionStorage.setItem("r52-city-max", maxValue);
    chosen = "root";
    show();
  });
  function graphicsError(text) {
    graphicsFailure = text;
    setQuiet(false);
    clean.disabled = true;
    message.hidden = false;
    message.classList.add("city-fallback");
    message.replaceChildren(
      h("strong", { text: "3D-сцена недоступна" }),
      h("p", { text }),
      h("button", {
        class: "btn",
        type: "button",
        text: "Открыть список областей",
        onClick: () => {
          workspace.dispatchEvent(new CustomEvent("city-panel-request",{detail:{kind:"areas"}}));areas.hidden = false;
          areaButton.setAttribute("aria-expanded", "true");
        },
      }),
      h("a", { class: "btn", href: "#/products", text: "Обычный каталог ↗" }),
    );
  }
  async function load() {
    const mine = ++seq;
    let all = [],
      offset = 0;
    try {
      while (ctx.alive()) {
        const d = await get(
          `/api/products${query({ limit: 200, offset, sort: "title" })}`,
        );
        if (mine !== seq || !ctx.alive()) return;
        const page = d.items || [];
        all.push(...page);
        offset += page.length;
        if (!page.length || offset >= d.total) break;
      }
      items = all;
      await show();
    } catch (error) {
      if (ctx.alive()) {
        message.hidden = false;
        message.replaceChildren(
          h("p", { text: "Не удалось загрузить базу" }),
          h("button", {
            class: "btn",
            type: "button",
            text: "Повторить",
            onClick: load,
          }),
        );
      }
    }
  }
  let graphicsFailure = null;
  try {
    const { createCity } = await import("../city-scene.js");
    if (!ctx.alive()) return;
    engine = createCity(stage, {
      onSelect: go,
      onOpen: (p) => (location.hash = `#/products/${p.id}`),
      onAsk: ask,
      onReady: (info) => {
        workspace.dataset.engine = "three-" + info.revision;
        workspace.dataset.renderedProducts = String(info.objects);
      },
      onError: graphicsError,
    });
  } catch (error) {
    graphicsFailure = error.message;
  }
  const escape = (e) => {
    if (e.key !== "Escape" || document.querySelector("dialog[open]")) return;
    if (quiet) {
      e.preventDefault();
      setQuiet(false);
      clean.focus();
      return;
    }
    if (!areas.hidden) {
      areas.hidden = true;
      areaButton.setAttribute("aria-expanded", "false");
    } else if (current && current.kind !== "root") select(current.parent);
    else close();
  };
  document.addEventListener("keydown", escape);
  ctx.cleanup(() => {
    closed = true;
    seq++;
    clearTimeout(filterTimer);
    document.removeEventListener("keydown", escape);
    engine?.destroy();
  });
  ctx.cleanup(on("data", load));
  await load();
  if (graphicsFailure && ctx.alive()) graphicsError(graphicsFailure);
  else if (ctx.alive())
    setQuiet(sessionStorage.getItem("r52-city-quiet") === "true");
  // Diagnostic event contains only visual state, never sessions, authentication or private payloads.
  const inspect = () => {
    workspace.dataset.camera = JSON.stringify(engine?.snapshot() || {});
  };
  workspace.addEventListener("city-inspect", inspect);
  ctx.cleanup(() => workspace.removeEventListener("city-inspect", inspect));
}
