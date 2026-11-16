// R-52: настройки. Уведомления, сводка, входящие, копии и хранение, помощник и интерфейс. Каждое изменение сохраняется сразу.
import { h } from "../dom.js";
import { get, patch, describe } from "../api.js";
import { state, emit } from "../state.js";
import { empty, skel, switchEl, selectEl } from "../ui.js";
import { enter, flash, applyMotion } from "../motion.js";
import { cardHead } from "../actions.js";

function groups(status) {
  const intake = status.intake || {};
  const folders = (intake.folders || []).map((folder) => (typeof folder === "string" ? folder : folder && (folder.path || folder.name))).filter(Boolean);
  return [
    {
      icon: "bell", title: "Уведомления", sub: "Когда и как R-52 сообщает о событиях",
      rows: [
        { key: "notifications.enabled", type: "switch", title: "Показывать уведомления", text: "О достижении цели, снижении цены и появлении в наличии" },
        { key: "notifications.quiet_hours", type: "text", title: "Тихие часы", text: "В это время уведомления копятся и показываются позже. Формат 23:00-08:00, пустое поле отключает тихие часы", placeholder: "23:00-08:00" },
        { key: "ui.sound", type: "switch", title: "Звук", text: "Короткий сигнал при новом уведомлении в открытом окне" },
      ],
    },
    {
      icon: "sunrise", title: "Ежедневная сводка", sub: "Короткий итог по ценам раз в день",
      rows: [
        { key: "digest.enabled", type: "switch", title: "Собирать сводку", text: "Что изменилось за сутки по товарам и целям" },
        { key: "digest.time", type: "time", title: "Время сводки", text: "По часовому поясу ядра" },
      ],
    },
    {
      icon: "inbox", title: "Входящие", sub: "Файлы из R-52 Eye",
      rows: [
        { key: "intake.enabled", type: "switch", title: "Следить за папкой входящих", text: folders.length ? `Папка: ${folders.join(", ")}` : "Новые файлы из папки попадают во «Входящие» сами" },
        { key: "intake.auto_accept", type: "switch", title: "Принимать без проверки", text: "Распознанные файлы сразу добавляются к товарам, без ручного решения" },
      ],
    },
    {
      icon: "archive", title: "Копии и хранение", sub: "Резервные копии базы и срок хранения истории",
      rows: [
        { key: "backup.time", type: "time", title: "Время ежедневной копии", text: "Копия делается, когда ядро запущено" },
        { key: "backup.keep_daily", type: "number", min: 1, max: 60, title: "Хранить ежедневных копий", text: "От 1 до 60" },
        { key: "backup.keep_weekly", type: "number", min: 0, max: 52, title: "Хранить недельных копий", text: "От 0 до 52" },
        { key: "retention.days", type: "number", min: 7, max: 3650, title: "Хранить историю задач, уведомлений и событий, дней", text: "Цены и наблюдения не удаляются" },
      ],
    },
    {
      icon: "assistant", title: "Помощник", sub: "Как отвечать на вопросы",
      rows: [
        { key: "assistant.llm", type: "select", options: [["off", "Только правила"], ["openrouter", "GPT‑5 Mini · OpenRouter"], ["ollama", "Локальная модель Ollama"]], title: "Режим ответов", text: "OpenRouter — платный режим: часть диалога и выбранные данные передаются внешнему провайдеру. Ollama работает локально. Правила не требуют сети." },
        { key: "assistant.openrouter_max_tokens", type: "number", min: 512, max: 3000, title: "Лимит выходных токенов", text: "Включает рассуждения и видимый ответ. Низкий лимит может привести к ответу по правилам." },
        { key: "assistant.openrouter_budget_cents", type: "number", min: 1, max: 800, title: "Локальный бюджет, центы USD", text: "200 = $2. Счётчик этой базы, не жёсткий лимит провайдера. Для неизвестных расходов резервируется $0.10 за вызов. Установите отдельный лимит ключа в OpenRouter." },
        { key: "assistant.ollama_model", type: "text", title: "Модель Ollama", text: "Например qwen2.5:7b-instruct", placeholder: "qwen2.5:7b-instruct" },
        { key: "assistant.ollama_port", type: "number", min: 1024, max: 65535, title: "Порт Ollama", text: "Обычно 11434" },
      ],
    },
    {
      icon: "sliders", title: "Интерфейс", sub: "Как выглядит окно R-52",
      rows: [
        { key: "ui.motion", type: "switch", title: "Анимации", text: "Плавные переходы и эффекты. Выключите, если они отвлекают" },
      ],
    },
  ];
}

export async function render(root, ctx) {
  ctx.setHeader("Настройки", "");
  root.append(h("div", { class: "settings" }, skel(230), skel(230), skel(230), skel(230)));
  let data;
  try {
    data = await get("/api/settings");
  } catch (error) {
    if (!ctx.alive()) return;
    root.replaceChildren(empty({
      icon: "warn",
      title: "Настройки не загрузились",
      text: describe(error),
      action: { label: "Повторить", icon: "refresh", run: () => { root.replaceChildren(); render(root, ctx); } },
    }));
    return;
  }
  if (!ctx.alive()) return;
  let values = { ...(data.values || {}) };
  const rows = new Map();
  const cloudStatus = h("p", {class: "muted", role: "status", text: "OpenRouter: проверка локальных настроек…"});
  get("/api/assistant/provider-status").then(s => {
    if (!ctx.alive()) return;
    cloudStatus.textContent = `OpenRouter · ${s.model} · ${s.key_present ? "Ключ задан" : "Ключ не задан"}. Учтено $${s.known_cost_usd.toFixed(4)}, неизвестных расходов: ${s.unknown_calls}. Лимит ключа задаётся отдельно в OpenRouter.`;
  }).catch(() => {cloudStatus.textContent = "Не удалось прочитать статус OpenRouter.";});

  const toggleOllama = () => {
    const cloud = values["assistant.llm"] === "openrouter";
    cloudStatus.hidden = !cloud;
    for (const key of ["assistant.openrouter_max_tokens", "assistant.openrouter_budget_cents"]) {
      const row = rows.get(key);
      if (row) row.hidden = !cloud;
    }
    const enabled = values["assistant.llm"] === "ollama";
    for (const key of ["assistant.ollama_model", "assistant.ollama_port"]) {
      const row = rows.get(key);
      if (row) row.hidden = !enabled;
    }
  };

  const showError = (row, text) => {
    const error = row.querySelector(".error");
    error.textContent = text || "";
    error.hidden = !text;
  };

  async function save(def, value, row, revert) {
    showError(row, "");
    let result;
    try {
      result = await patch("/api/settings", { [def.key]: value });
    } catch (error) {
      if (!ctx.alive()) return;
      showError(row, describe(error));
      if (revert) revert();
      return;
    }
    values = { ...values, ...((result && result.values) || { [def.key]: value }) };
    state.settings = values;
    emit("settings", values);
    if (!ctx.alive()) return;
    flash(row);
    if (def.key === "ui.motion") applyMotion(Boolean(values["ui.motion"]));
    if (def.key === "assistant.llm") toggleOllama();
  }

  function control(def, row) {
    const value = values[def.key];
    if (def.type === "switch") {
      const el = switchEl(Boolean(value), (next) => save(def, next, row, () => el.setValue(!next)), def.title);
      return el;
    }
    if (def.type === "select") {
      const el = selectEl(def.options, String(value ?? ""), { "aria-label": def.title });
      el.addEventListener("change", () => {
        const before = String(values[def.key] ?? "");
        save(def, el.value, row, () => { el.value = before; });
      });
      return el;
    }
    const input = h("input", {
      class: "input",
      // Поле времени текстовое: встроенный выбор времени в части систем показывает 12-часовой формат.
      type: def.type === "time" ? "text" : def.type,
      maxlength: def.type === "time" ? "5" : undefined,
      autocomplete: "off",
      value: value === null || value === undefined ? "" : String(value),
      min: def.min !== undefined ? String(def.min) : undefined,
      max: def.max !== undefined ? String(def.max) : undefined,
      step: def.type === "number" ? "1" : undefined,
      inputmode: def.type === "number" ? "numeric" : undefined,
      placeholder: def.placeholder || (def.type === "time" ? "09:00" : undefined),
      "aria-label": def.title,
    });
    input.addEventListener("change", () => {
      const before = values[def.key] === null || values[def.key] === undefined ? "" : String(values[def.key]);
      let next = input.value.trim();
      if (def.type === "number") {
        const n = Number(next);
        if (!next || !Number.isInteger(n) || n < def.min || n > def.max) {
          showError(row, `Введите целое число от ${def.min} до ${def.max}`);
          return;
        }
        next = n;
      }
      if (def.type === "time") {
        const match = /^(\d{1,2})[:.](\d{2})$/.exec(next);
        if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) {
          showError(row, "Введите время в формате ЧЧ:ММ, например 09:00");
          return;
        }
        next = `${match[1].padStart(2, "0")}:${match[2]}`;
        input.value = next;
      }
      if (next === values[def.key]) {
        showError(row, "");
        return;
      }
      save(def, next, row, () => { input.value = before; });
    });
    return input;
  }

  const cards = groups(state.status || {}).map((group) => {
    const body = group.rows.map((def) => {
      const row = h("div", { class: "set-row" },
        h("div", { class: "s-text" },
          h("b", { text: def.title }),
          def.text ? h('details',{class:'help-tip'},h('summary',{'aria-label':'Информация: '+def.title,text:'?'}),h('p',{text:def.text})) : null,
          h("small", { class: "error", hidden: true, role: "alert" })));
      row.append(h("div", { class: "s-ctl" }, control(def, row)));
      rows.set(def.key, row);
      return row;
    });
    return h("section", { class: "card" }, cardHead({ icon: group.icon, title: group.title, sub: "" }), body);
  });
  // Две независимые колонки: карточки разной высоты не оставляют провалов.
  root.replaceChildren(h("div", { class: "settings" },
    h("div", { class: "stack set-col" }, cards.filter((_, index) => index % 2 === 0)),
    h("div", { class: "stack set-col" }, cards.filter((_, index) => index % 2 === 1))));
  cards.find(card => card.querySelector('[aria-label="Режим ответов"]'))?.append(cloudStatus);
  toggleOllama();
  enter(cards, { y: 12, step: 50 });
}
