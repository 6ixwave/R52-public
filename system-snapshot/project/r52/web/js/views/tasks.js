import { activateObject } from "../object-interactions.js";
// R-52: задачи. Быстрый запуск фоновых работ ядра, ход выполнения, отмена и повтор, расписание.
import { h, icon, ago, count, dateTime, number } from "../dom.js";
import { get, post, describe } from "../api.js";
import { empty, skel, segmented, toast, fail, modal, switchEl } from "../ui.js";
import { taskKind, taskStatus } from "../meta.js";
import { enter } from "../motion.js";
import { cardHead, runTask, backupNow } from "../actions.js";

const DESCRIPTIONS = {
  backup: "Снимок базы, из которого можно восстановиться",
  maintenance:
    "Убирает старую историю задач, уведомлений и событий по сроку хранения",
  digest: "Собрать сводку по ценам и целям прямо сейчас",
  import_legacy: "Перенести товары и цены из прежних версий R-52",
  collect: "Пока не подключён: цены приходят через R-52 Eye",
};
const ORIGINS = {
  UI: "из окна",
  USER: "вручную",
  MANUAL: "вручную",
  SCHEDULE: "по расписанию",
  SCHEDULER: "по расписанию",
  CLI: "из командной строки",
  ASSISTANT: "через помощника",
  SYSTEM: "ядро",
  API: "через программу",
  RETRY: "повтор",
};
const RESULT_LABELS = {
  products: "товаров",
  observations: "наблюдений",
  prices: "цен",
  deleted: "удалено",
  tasks: "задач",
  notifications: "уведомлений",
  events: "событий",
  files: "файлов",
  skipped: "пропущено",
  errors: "ошибок",
};
const ACTIVE = new Set(["RUNNING", "QUEUED"]);
const RETRYABLE = new Set(["FAILED", "INTERRUPTED", "CANCELLED"]);
const GROUPS = {
  "": null,
  RUNNING: ["RUNNING"],
  QUEUED: ["QUEUED"],
  COMPLETED: ["COMPLETED"],
  FAILED: ["FAILED", "INTERRUPTED", "PARTIAL", "BLOCKED"],
};
let filter = "";

const originText = (value) =>
  ORIGINS[String(value || "").toUpperCase()] ||
  String(value || "").toLowerCase();
const clampPct = (value) => Math.max(0, Math.min(100, Number(value) || 0));

export async function render(root, ctx) {
  const quick = h("div", { class: "quick" });
  const seg = segmented(
    [
      { value: "", label: "Все" },
      { value: "RUNNING", label: "Выполняются", icon: "refresh" },
      { value: "QUEUED", label: "В очереди", icon: "clock" },
      { value: "COMPLETED", label: "Готово", icon: "check" },
      { value: "FAILED", label: "С ошибкой", icon: "fail" },
    ],
    filter,
    (value) => {
      filter = value;
      draw();
    },
    { size: "sm", label: "Показать" },
  );
  const list = h("div", { class: "stack" }, skel(64), skel(64), skel(64));
  const scheduleBox = h("div", null, skel(120));
  root.append(
    quick,
    h(
      "section",
      { class: "card stack" },
      cardHead({ icon: "tasks", title: "Журнал задач", tools: seg }),
      list,
    ),
    h(
      "section",
      { class: "card" },
      cardHead({
        icon: "schedule",
        title: "Расписание",
        sub: "Регулярные работы ядра",
      }),
      scheduleBox,
    ),
  );

  let data = { items: [], counts: {}, kinds: [] };
  let timer = 0;
  let quickDrawn = false;
  const rows = new Map();
  const retrySafe = (kind) =>
    Boolean(
      (data.kinds || []).find((item) => item.kind === kind && item.retry_safe),
    );

  async function load() {
    clearTimeout(timer);
    try {
      data = await get("/api/tasks");
    } catch (error) {
      if (!ctx.alive()) return;
      if (!rows.size)
        list.replaceChildren(
          empty({
            icon: "warn",
            title: "Задачи не загрузились",
            text: describe(error),
            action: { label: "Повторить", icon: "refresh", run: load },
          }),
        );
      timer = setTimeout(load, 15000);
      return;
    }
    if (!ctx.alive()) return;
    if (!quickDrawn) drawQuick();
    draw();
    const active = (data.items || []).some((task) => ACTIVE.has(task.status));
    timer = setTimeout(load, active ? 3000 : 15000);
  }

  async function loadSchedules() {
    let items;
    try {
      items = (await get("/api/schedules")).items || [];
    } catch {
      items = null;
    }
    if (!ctx.alive()) return;
    scheduleBox.replaceChildren(
      items
        ? scheduleList(items)
        : h("p", { class: "muted", text: "Расписание не загрузилось." }),
    );
  }

  function drawQuick() {
    quickDrawn = true;
    const kinds =
      data.kinds && data.kinds.length
        ? data.kinds
        : Object.keys(DESCRIPTIONS).map((kind) => ({ kind }));
    const cards = kinds.map((item) => {
      const meta = taskKind(item.kind);
      return h(
        "button",
        {
          class: "qa card lift spot",
          type: "button",
          onClick: () => quickRun(item.kind),
        },
        h(
          "div",
          { class: "q-icon", style: { "--c": meta.color } },
          icon(meta.icon),
        ),
        h("b", { text: item.title || meta.title }),
        h("small", { hidden: true, text: "" }),
      );
    });
    quick.replaceChildren(...cards);
    enter(cards, { y: 10, step: 40 });
  }

  async function quickRun(kind) {
    if (kind === "collect") {
      toast({
        title: "Сбор с маркетплейсов пока не подключён",
        text: "Цены приходят через R-52 Eye или вводятся вручную.",
        timeout: 4200,
      });
      return;
    }
    if (kind === "import_legacy") {
      importLegacyDialog({
        onStarted: () => {
          if (ctx.alive()) load();
        },
      });
      return;
    }
    try {
      if (kind === "backup") await backupNow();
      else await runTask(kind);
    } catch (error) {
      fail(error, "Задача не запустилась");
    }
    if (ctx.alive()) load();
  }

  function draw() {
    const items = data.items || [];
    const counts =
      data.counts && Object.keys(data.counts).length
        ? data.counts
        : tally(items);
    for (const [value, statuses] of Object.entries(GROUPS)) {
      if (!statuses) continue;
      const n = statuses.reduce(
        (sum, status) => sum + (Number(counts[status]) || 0),
        0,
      );
      seg.setCount(value, n || null);
    }
    const busy = (Number(counts.RUNNING) || 0) + (Number(counts.QUEUED) || 0);
    ctx.setHeader(
      "Задачи",
      busy ? `В работе: ${count(busy, "задача", "задачи", "задач")}` : "",
    );
    const allowed = GROUPS[filter];
    const shown = items
      .filter((task) => !allowed || allowed.includes(task.status))
      .slice(0, 60);
    if (!shown.length) {
      rows.clear();
      list.replaceChildren(
        empty({
          icon: "tasks",
          title: filter ? "Таких задач нет" : "Задач пока не было",
          text: filter ? "Выберите другой фильтр." : "",
        }),
      );
      return;
    }
    // Строки пересоздаются только при смене состояния, прогресс обновляется на месте.
    const next = shown.map((task) => {
      const base = [
        task.status,
        task.cancel_requested,
        task.error_code,
        task.finished_at,
        task.attempt,
      ].join("|");
      const known = rows.get(task.id);
      if (known && known.base === base) {
        if (known.bar && typeof task.progress === "number")
          known.bar.style.setProperty(
            "--p",
            (clampPct(task.progress) / 100).toFixed(3),
          );
        return known.el;
      }
      const el = taskRow(task);
      rows.set(task.id, { el, base, bar: el.bar || null });
      return el;
    });
    const same =
      next.length === list.children.length &&
      next.every((el, index) => list.children[index] === el);
    if (!same) list.replaceChildren(...next);
  }

  function taskRow(task) {
    const kind = taskKind(task.kind);
    const st = taskStatus(task.status);
    const detail = [
      originText(task.origin),
      `создана ${ago(task.created_at)}`,
      task.finished_at
        ? `завершена ${ago(task.finished_at)}`
        : task.started_at
          ? `начата ${ago(task.started_at)}`
          : null,
      task.attempt > 1 ? `попытка ${task.attempt}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const main = h(
      "div",
      { class: "task-main" },
      h("b", { text: kind.title }),
      h("small", { text: detail }),
    );
    let bar = null;
    if (task.status === "RUNNING") {
      const known = typeof task.progress === "number";
      bar = h(
        "div",
        {
          class: ["bar", known ? "live" : "indet"],
          style: known
            ? { "--p": (clampPct(task.progress) / 100).toFixed(3) }
            : undefined,
        },
        h("i"),
      );
      main.append(bar);
    }
    if (task.error_message || task.error_code)
      main.append(
        h("small", {
          class: "error",
          text: task.error_message || task.error_code,
        }),
      );
    const result =
      task.status === "COMPLETED" || task.status === "PARTIAL"
        ? summary(task.result)
        : "";
    if (result) main.append(h("small", { class: "muted", text: result }));
    const actions = h(
      "div",
      { class: "task-actions" },
      h("span", { class: ["badge", st.cls], text: st.text }),
    );
    if (ACTIVE.has(task.status)) {
      if (task.cancel_requested)
        actions.append(h("small", { class: "muted", text: "отменяется" }));
      else
        actions.append(
          actionButton(
            "Отменить",
            "x",
            "ghost",
            () => post(`/api/tasks/${task.id}/cancel`, {}),
            "Отмена запрошена",
            "Не удалось отменить",
            kind.title,
          ),
        );
    }
    if (RETRYABLE.has(task.status) && retrySafe(task.kind)) {
      actions.append(
        actionButton(
          "Повторить",
          "refresh",
          "",
          () => post(`/api/tasks/${task.id}/retry`, {}),
          "Задача запущена снова",
          "Не удалось повторить",
          kind.title,
        ),
      );
    }
    const el = h(
      "div",
      { class: "task" },
      h("div", { class: ["t-status", task.status] }, icon(st.icon)),
      main,
      actions,
    );
    activateObject(
      el,
      () =>
        modal({
          title: kind.title,
          width: 620,
          body: h(
            "div",
            { class: "stack" },
            h("p", { text: detail }),
            h("span", { class: ["badge", st.cls], text: st.text }),
            h("p", { text: DESCRIPTIONS[task.kind] || "Сведения о задаче" }),
            result ? h("p", { text: result }) : null,
            task.error_message
              ? h("p", { class: "error", text: task.error_message })
              : null,
          ),
          actions: [],
        }),
      {
        label: `${kind.title}. ${st.text}. Подробности`,
        role: "button",
        dialog: true,
      },
    );
    el.bar = bar;
    return el;
  }

  function actionButton(
    label,
    iconName,
    cls,
    request,
    okText,
    failText,
    title,
  ) {
    const button = h(
      "button",
      { class: ["btn sm", cls], type: "button" },
      icon(iconName),
      label,
    );
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await request();
        toast({ kind: "ok", title: okText, text: title, timeout: 2400 });
      } catch (error) {
        button.disabled = false;
        fail(error, failText);
      }
      if (ctx.alive()) load();
    });
    return button;
  }

  ctx.cleanup(() => clearTimeout(timer));
  loadSchedules();
  await load();
}

function tally(items) {
  const counts = {};
  for (const task of items)
    counts[task.status] = (counts[task.status] || 0) + 1;
  return counts;
}

function summary(result) {
  if (!result || typeof result !== "object") return "";
  if (typeof result.text === "string") return result.text;
  if (typeof result.name === "string") return result.name;
  return Object.entries(result)
    .filter(([key, value]) => RESULT_LABELS[key] && typeof value === "number")
    .slice(0, 4)
    .map(([key, value]) => `${RESULT_LABELS[key]}: ${number(value)}`)
    .join(" · ");
}

function scheduleList(items) {
  if (!items.length)
    return h("p", { class: "muted", text: "Регулярных задач нет." });
  const sorted = [...items].sort((a, b) =>
    String(a.next_due_at || "~").localeCompare(String(b.next_due_at || "~")),
  );
  return h(
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
      const last = item.last_run_at ? `прошлый ${ago(item.last_run_at)}` : "";
      return h(
        "div",
        { class: ["tl-item", !enabled && "off"] },
        h("b", { text: taskKind(item.kind).title }),
        h("small", {
          text: enabled
            ? [rule, next, last].filter(Boolean).join(" · ")
            : "выключено в настройках",
        }),
      );
    }),
  );
}

function plainField(label, control) {
  return h(
    "div",
    { class: "field" },
    h("span", { class: "label", text: label }),
    control,
  );
}

// Диалог импорта из прежних версий. Нужен и здесь, и на странице «Сервис».
export function importLegacyDialog({ onStarted } = {}) {
  const d0 = h("input", {
    class: "input",
    type: "text",
    placeholder: "Например D:\\R52\\d0",
    "aria-label": "Путь к данным d0",
    spellcheck: "false",
  });
  const data01 = h("input", {
    class: "input",
    type: "text",
    placeholder: "Например D:\\R52\\data01",
    "aria-label": "Путь к данным data01",
    spellcheck: "false",
  });
  let dry = true;
  const toggle = switchEl(
    true,
    (value) => {
      dry = value;
    },
    "Только проверить",
  );
  modal({
    title: "Импорт из старых версий",
    body: h(
      "div",
      { class: "stack" },
      h("p", {
        class: "muted",
        text: "Укажите папку прежней версии. Сначала выполните проверку.",
      }),
      plainField("Данные d0", d0),
      plainField("Данные data01", data01),
      h(
        "div",
        { class: "check" },
        toggle,
        h("span", { text: "Только проверить, ничего не записывать" }),
      ),
    ),
    actions: [
      { label: "Отмена", kind: "ghost" },
      {
        label: "Запустить",
        kind: "primary",
        icon: "upload",
        run: async () => {
          const params = { dry_run: dry };
          if (d0.value.trim()) params.d0 = d0.value.trim();
          if (data01.value.trim()) params.data01 = data01.value.trim();
          if (!params.d0 && !params.data01) {
            toast({
              kind: "warn",
              title: "Укажите хотя бы один путь",
              timeout: 3000,
            });
            d0.focus();
            return false;
          }
          const task = await runTask("import_legacy", params);
          if (!task) return false;
          if (onStarted) onStarted(task);
          return undefined;
        },
      },
    ],
  });
}
