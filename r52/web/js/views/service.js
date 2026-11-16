import { activateObject } from "../object-interactions.js";
// R-52: сервис. Состояние ядра и базы, резервные копии, источники, выгрузка, импорт из старых версий, журнал и завершение работы.
import { h, icon, number, bytes, ago, dateTime } from "../dom.js";
import { get, post, describe } from "../api.js";
import { state, on, emit } from "../state.js";
import {
  empty,
  skel,
  confirm,
  toast,
  fail,
  sourceBadge,
  modal,
} from "../ui.js";
import { enter } from "../motion.js";
import {
  cardHead,
  backupNow,
  exportNow,
  openFolder,
  runTask,
} from "../actions.js";
import { importLegacyDialog } from "./tasks.js";

const BACKUP_LABELS = {
  manual: "вручную",
  daily: "ежедневная",
  weekly: "недельная",
  scheduled: "по расписанию",
  auto: "автоматическая",
  pre_restore: "перед восстановлением",
  "pre-restore": "перед восстановлением",
  pre_migrate: "перед обновлением",
};
const KIND_LABELS = { marketplace: "маркетплейс", shop: "магазин" };
const RESTORE = "py -3 -m r52 restore";

export async function render(root, ctx) {
  root.append(
    h("div", { class: "svc-grid" }, skel(220), skel(220), skel(220), skel(220)),
  );
  let status = state.status;
  try {
    status = await get("/api/status");
  } catch (error) {
    if (!ctx.alive()) return;
    if (!status) {
      root.replaceChildren(
        empty({
          icon: "warn",
          title: "Состояние ядра не загрузилось",
          text: describe(error),
          action: {
            label: "Повторить",
            icon: "refresh",
            run: () => {
              root.replaceChildren();
              render(root, ctx);
            },
          },
        }),
      );
      return;
    }
  }
  if (!ctx.alive()) return;
  ctx.setHeader(
    "Сервис",
    `Ядро R-52 ${status.version || ""}, запущено ${status.started_at ? ago(status.started_at) : "-"}`,
  );
  const backups = backupsCard(ctx, status);
  // Две независимые колонки: слева ядро и сохранность данных, справа база и откуда приходят цены.
  const left = [coreCard(status), backups.el, exportCard()];
  const right = [databaseCard(status), sourcesCard(status), importCard(status)];
  const wide = [logCard(ctx), shutdownCard()];
  const cards = [
    left[0],
    right[0],
    left[1],
    right[1],
    left[2],
    right[2],
    ...wide,
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
  root.replaceChildren(
    ...problems,
    h(
      "div",
      { class: "svc-grid" },
      h("div", { class: "stack svc-col" }, left),
      h("div", { class: "stack svc-col" }, right),
      wide,
    ),
  );
  enter(cards, { y: 12, step: 45, maxSteps: 8 });
  let timer = 0;
  ctx.cleanup(
    on("data", (event) => {
      if (!event || event.kind !== "tasks") return;
      clearTimeout(timer);
      timer = setTimeout(backups.load, 1500);
    }),
  );
  ctx.cleanup(() => clearTimeout(timer));
}

function kv(pairs) {
  return h(
    "dl",
    { class: "kv" },
    pairs.flatMap(([key, value, cls]) => [
      h("dt", { text: key }),
      h("dd", { class: cls, text: value }),
    ]),
  );
}

function folderButton(which, label) {
  return h(
    "button",
    { class: "btn sm ghost", type: "button", onClick: () => openFolder(which) },
    icon("folder"),
    label,
  );
}

function componentText(value) {
  if (value === true) return "работает";
  if (value === false || value === null || value === undefined)
    return "остановлено";
  if (typeof value === "object")
    return value.ok === false ? "сбой" : "работает";
  return String(value);
}

function copyButton(text, title) {
  const button = h(
    "button",
    { class: "icon-btn sm", type: "button", title, "aria-label": title },
    icon("copy"),
  );
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast({ kind: "ok", title: "Скопировано", text, timeout: 2400 });
    } catch {
      toast({
        kind: "warn",
        title: "Не удалось скопировать, выделите текст вручную",
        text,
        timeout: 5000,
      });
    }
  });
  return button;
}

function copyLine(text) {
  return h(
    "div",
    { class: "copy-line" },
    h("code", { class: "mono", text }),
    copyButton(text, "Скопировать команду"),
  );
}

function coreCard(status) {
  const components = (status.health && status.health.components) || {};
  const ok = status.health ? status.health.ok !== false : true;
  return h(
    "section",
    { class: "card stack" },
    cardHead({
      icon: "cpu",
      title: "Ядро",
      sub: ok ? "работает нормально" : "есть замечания",
      tools: folderButton("home", "Папка R-52"),
    }),
    kv([
      ["Версия", status.version || "-"],
      ["Процесс", status.pid ? `PID ${status.pid}` : "-"],
      [
        "Запущено",
        status.started_at
          ? `${dateTime(status.started_at)}, ${ago(status.started_at)}`
          : "-",
      ],
      ["Часовой пояс", status.timezone || "-"],
      ["Домашняя папка", status.home || "-", "mono"],
      [
        "Защита сети",
        status.network_guard ? "включена: только этот компьютер" : "выключена",
      ],
      ["Фоновые задачи", componentText(components.worker)],
      ["Расписание", componentText(components.scheduler)],
      [
        "Папка входящих",
        status.intake && status.intake.enabled === false
          ? "выключена"
          : componentText(components.intake),
      ],
    ]),
    status.intake && status.intake.last_error
      ? h(
          "div",
          { class: "callout warn" },
          icon("warn"),
          h("span", { text: `Папка входящих: ${status.intake.last_error}` }),
        )
      : null,
  );
}

function databaseCard(status) {
  const db = status.database || {};
  return h(
    "section",
    { class: "card stack" },
    cardHead({
      icon: "database",
      title: "База данных",
      sub: bytes(db.size_bytes),
      tools: h(
        "div",
        { class: "row" },
        h(
          "button",
          {
            class: "btn sm",
            type: "button",
            onClick: () => runTask("maintenance"),
          },
          icon("wand"),
          "Обслужить",
        ),
        folderButton("data", "Папка"),
      ),
    }),
    kv([
      ["Файл", db.path || "-", "mono"],
      ["Версия схемы", String(db.schema_version ?? "-")],
      ["Товаров", number(db.products || 0)],
      ["Наблюдений", number(db.observations || 0)],
      ["Точек цены", number(db.price_points || 0)],
    ]),
    h("p", { class: "faint", text: "История цен сохраняется." }),
  );
}

function backupRow(item) {
  const label = BACKUP_LABELS[item.label] || item.label || "";
  const el = h(
    "div",
    { class: "list-row backup-object" },
    icon("archive"),
    h(
      "span",
      { class: "ellipsis" },
      h("b", { class: "mono", text: item.name }),
      h("small", {
        class: "muted",
        text: ` ${[label, bytes(item.size), ago(item.created_at)].filter(Boolean).join(" · ")}`,
      }),
    ),
  );
  activateObject(
    el,
    () =>
      modal({
        title: "Резервная копия",
        body: h(
          "div",
          { class: "stack" },
          h("b", { class: "mono", text: item.name }),
          h("p", {
            text: [label, bytes(item.size), dateTime(item.created_at)]
              .filter(Boolean)
              .join(" · "),
          }),
          h("p", {
            class: "muted",
            text: "Восстановление выполняется только при остановленном ядре. Просмотр копии ничего не восстанавливает.",
          }),
          copyLine(`${RESTORE} ${item.name} --yes`),
        ),
      }),
    {
      label: `Резервная копия ${item.name}. Подробности`,
      role: "button",
      dialog: true,
    },
  );
  return el;
}

function backupsCard(ctx, status) {
  const list = h("div", { class: "stack" }, skel(44), skel(44));
  const total = status.backups && status.backups.count;
  const el = h(
    "section",
    { class: "card stack" },
    cardHead({
      icon: "archive",
      title: "Резервные копии",
      sub: total ? `всего ${number(total)}` : "",
      tools: h(
        "div",
        { class: "row" },
        h(
          "button",
          {
            class: "btn sm primary",
            type: "button",
            onClick: () => backupNow(),
          },
          icon("archive"),
          "Создать",
        ),
        folderButton("backups", "Папка"),
      ),
    }),
    list,
    h("p", {
      class: "muted",
      text: "Восстановление делается при остановленном ядре. Закройте R-52, откройте командную строку в папке программы и выполните:",
    }),
    copyLine(`${RESTORE} ИМЯ_КОПИИ --yes`),
  );
  async function load() {
    let data;
    try {
      data = await get("/api/backups");
    } catch (error) {
      if (ctx.alive())
        list.replaceChildren(h("p", { class: "muted", text: describe(error) }));
      return;
    }
    if (!ctx.alive()) return;
    const items = data.items || [];
    list.replaceChildren(
      ...(items.length
        ? items.slice(0, 8).map(backupRow)
        : [h("p", { class: "muted", text: "Копий пока нет." })]),
    );
  }
  load();
  return { el, load };
}

function sourcesCard(status) {
  const sources = status.sources || [];
  return h(
    "section",
    { class: "card stack" },
    cardHead({
      icon: "store",
      title: "Источники",
      sub: "откуда приходят цены",
    }),
    h(
      "div",
      { class: "stack" },
      sources.map((source) =>
        h(
          "div",
          { class: "list-row" },
          sourceBadge(source.key),
          KIND_LABELS[source.kind]
            ? h("small", { class: "muted", text: KIND_LABELS[source.kind] })
            : null,
          h("span", { class: "spacer" }),
          source.kind === "manual"
            ? h("span", { class: "badge", text: "ввод вручную" })
            : source.collector === "READY"
              ? h("span", { class: "badge ok", text: "сбор подключён" })
              : h("span", { class: "badge violet", text: "через Eye" }),
        ),
      ),
    ),
    h("p", {
      class: "faint",
      text: "Автоматический сбор с маркетплейсов не подключён: цены приходят из файлов R-52 Eye или вводятся вручную.",
    }),
  );
}

function exportCard() {
  return h(
    "section",
    { class: "card stack" },
    cardHead({
      icon: "download",
      title: "Выгрузка данных",
      tools: folderButton("exports", "Папка"),
    }),
    h("p", {
      class: "muted",
      text: "ZIP-архив с товарами и историей цен. Архив появится в папке выгрузок.",
    }),
    h(
      "div",
      { class: "row wrap" },
      h(
        "button",
        { class: "btn", type: "button", onClick: () => exportNow() },
        icon("download"),
        "Выгрузить",
      ),
    ),
  );
}

function importCard(status) {
  const imports = status.imports || [];
  return h(
    "section",
    { class: "card stack" },
    cardHead({ icon: "upload", title: "Импорт из старых версий" }),
    h("p", {
      class: "muted",
      text: "Перенос товаров и цен из баз прежних версий R-52. Сначала можно запустить проверку без записи.",
    }),
    imports.length
      ? h("p", {
          class: "faint",
          text: `Уже выполнено импортов: ${number(imports.length)}`,
        })
      : null,
    h(
      "div",
      { class: "row wrap" },
      h(
        "button",
        { class: "btn", type: "button", onClick: () => importLegacyDialog() },
        icon("upload"),
        "Импортировать",
      ),
    ),
  );
}

function logCard(ctx) {
  const box = h("div", {
    class: "log",
    tabindex: "0",
    role: "log",
    "aria-label": "Журнал ядра",
  });
  async function load() {
    let data;
    try {
      data = await get("/api/log/tail?lines=200");
    } catch (error) {
      if (ctx.alive())
        box.replaceChildren(
          h("div", { class: "l-err", text: describe(error) }),
        );
      return;
    }
    if (!ctx.alive()) return;
    const lines =
      data.lines && data.lines.length ? data.lines : ["Журнал пока пуст."];
    box.replaceChildren(
      ...lines.map((line) =>
        h("div", {
          class: /\b(ERROR|CRITICAL)\b/.test(line)
            ? "l-err"
            : /\bWARNING\b/.test(line)
              ? "l-warn"
              : null,
          text: line,
        }),
      ),
    );
    box.scrollTop = box.scrollHeight;
  }
  load();
  return h(
    "section",
    { class: "card stack span-all" },
    cardHead({
      icon: "logs",
      title: "Журнал",
      sub: "последние 200 строк",
      tools: h(
        "div",
        { class: "row" },
        h(
          "button",
          { class: "btn sm ghost", type: "button", onClick: load },
          icon("refresh"),
          "Обновить",
        ),
        folderButton("logs", "Папка"),
      ),
    }),
    box,
  );
}

function shutdownCard() {
  const stop = h(
    "button",
    { class: "btn danger", type: "button" },
    icon("power"),
    "Завершить работу ядра",
  );
  stop.addEventListener("click", async () => {
    const ok = await confirm({
      title: "Завершить работу ядра?",
      text: "Окно потеряет связь, уведомления и задачи по расписанию остановятся. Чтобы продолжить, снова запустите R-52.",
      ok: "Завершить",
      danger: true,
    });
    if (!ok) return;
    stop.disabled = true;
    try {
      await post("/api/system/shutdown", {});
    } catch (error) {
      stop.disabled = false;
      fail(error, "Ядро не остановилось");
      return;
    }
    emit("core-stopped");
  });
  return h(
    "section",
    { class: "card stack span-all" },
    cardHead({
      icon: "power",
      title: "Завершение работы",
      sub: "остановить ядро R-52 на этом компьютере",
    }),
    h("p", {
      class: "muted",
      text: "Остановка нужна, например, перед восстановлением из резервной копии.",
    }),
    h("div", { class: "row wrap" }, stop),
  );
}
