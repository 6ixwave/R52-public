import { activateObject } from "../object-interactions.js";
// R-52: входящие. Файлы из R-52 Eye: загрузка, предпросмотр, проверки и решение по каждому файлу.
import { productImage, photoGallery, activateMedia } from "../media.js";
import { h, icon, money, ago, count, dateTime } from "../dom.js";
import { get, post, query, describe } from "../api.js";
import { state, on, emit } from "../state.js";
import {
  empty,
  skel,
  segmented,
  toast,
  fail,
  sourceBadge,
  availabilityBadge,
} from "../ui.js";
import { INBOX_STATUS, ROLE_LABELS, eventMeta } from "../meta.js";
import { enter, collapse } from "../motion.js";
import { go, importFiles, pickFiles, openExternal } from "../actions.js";

const STATUS_CLASS = {
  PENDING: "violet",
  ACCEPTED: "ok",
  REJECTED: "",
  INVALID: "bad",
};
let filter = null;

export async function render(root, ctx) {
  const status = state.status || {};
  if (filter === null) filter = status.inbox_pending ? "PENDING" : "";
  const intake = status.intake || {};
  const intakeOn = intake.enabled !== false;
  const folders = (intake.folders || [])
    .map((folder) =>
      typeof folder === "string"
        ? folder
        : folder && (folder.path || folder.name),
    )
    .filter(Boolean);

  const drop = h(
    "div",
    {
      class: "drop",
      role: "button",
      tabindex: "0",
      "aria-label": "Загрузить файлы Eye",
    },
    h("div", { class: "d-icon" }, icon("upload")),
    h("b", { text: "Загрузить Eye" }),
    h("small", { text: "" }),
  );
  const seg = segmented(
    [
      { value: "PENDING", label: "Ждут решения", icon: "clock" },
      { value: "", label: "Все" },
      { value: "ACCEPTED", label: "Принятые" },
      { value: "REJECTED", label: "Отклонённые" },
      { value: "INVALID", label: "С ошибкой" },
    ],
    filter,
    (value) => {
      filter = value;
      load(false);
    },
    { size: "sm", label: "Показать" },
  );
  const acceptAll = h(
    "button",
    { class: "btn sm", type: "button", hidden: true },
    icon("check"),
    "Принять все",
  );
  const list = h("div", { class: "inbox-list inbox-market-grid" });
  root.append(
    drop,
    h(
      "div",
      { class: "toolbar" },
      seg,
      h("span", { class: "spacer" }),
      acceptAll,
    ),
    list,
  );

  let seq = 0;
  let pendingItems = [];

  async function upload(files) {
    const results = await importFiles(files);
    if (results.length && filter !== "" && filter !== "PENDING") {
      filter = "";
      seg.setValue("");
      load(false);
    }
  }

  const pick = () => pickFiles((files) => upload(files));
  drop.addEventListener("click", pick);
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      pick();
    }
  });
  let depth = 0;
  drop.addEventListener("dragenter", (event) => {
    event.preventDefault();
    depth += 1;
    drop.classList.add("over");
  });
  drop.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  });
  drop.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) drop.classList.remove("over");
  });
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    depth = 0;
    drop.classList.remove("over");
    const files = Array.from(
      (event.dataTransfer && event.dataTransfer.files) || [],
    );
    if (files.length) upload(files);
  });
  // Файл, брошенный мимо поля, не должен открываться в окне вместо интерфейса.
  const guard = (event) => {
    if (
      event.dataTransfer &&
      Array.from(event.dataTransfer.types || []).includes("Files")
    )
      event.preventDefault();
  };
  window.addEventListener("dragover", guard);
  window.addEventListener("drop", guard);
  ctx.cleanup(() => {
    window.removeEventListener("dragover", guard);
    window.removeEventListener("drop", guard);
  });

  acceptAll.addEventListener("click", () =>
    withBusy(acceptAll, async () => {
      let done = 0;
      for (const item of pendingItems) {
        try {
          await post(`/api/inbox/${item.id}/accept`, {});
          done += 1;
        } catch (error) {
          fail(error, `Не удалось принять ${item.filename || "файл"}`);
        }
      }
      if (done) {
        toast({
          kind: "ok",
          title: `Принято ${count(done, "файл", "файла", "файлов")}`,
          timeout: 3200,
        });
        // --- НОВОЕ: Переключаем фильтр на "Ждут решения", чтобы очистить экран ---
        filter = "PENDING";
        seg.setValue("PENDING");
        load(false);
      }
      emit("data", { kind: "inbox" });
    }),
  );

  async function load(quiet) {
    const mine = ++seq;
    if (!quiet) list.replaceChildren(skel(150), skel(150));
    let data;
    try {
      data = await get(
        `/api/inbox${query({ status: filter || "ALL", limit: 100 })}`,
      );
    } catch (error) {
      if (mine !== seq || !ctx.alive()) return;
      list.replaceChildren(
        empty({
          icon: "warn",
          title: "Входящие не загрузились",
          text: describe(error),
          action: {
            label: "Повторить",
            icon: "refresh",
            run: () => load(false),
          },
        }),
      );
      return;
    }
    if (mine !== seq || !ctx.alive()) return;
    const items = data.items || [];
    const pending = data.pending || 0;
    seg.setCount("PENDING", pending);
    ctx.setHeader(
      "Входящие",
      pending
        ? `Ждут решения: ${count(pending, "файл", "файла", "файлов")}`
        : "Новых файлов нет",
    );
    pendingItems = items.filter((item) => item.status === "PENDING");
    acceptAll.hidden = pendingItems.length < 2;
    if (!items.length) {
      list.replaceChildren(emptyState());
      return;
    }
    const cards = items.map(card);
    list.replaceChildren(...cards);
    if (!quiet) enter(cards, { y: 12, step: 40, maxSteps: 10 });
  }

  function emptyState() {
    if (filter === "PENDING") {
      return empty({
        icon: "inbox",
        title: "Всё разобрано",
        text: intakeOn
          ? "Новые файлы из R-52 Eye появятся здесь сами. Можно и перетащить их в поле выше."
          : "Перетащите файлы в поле выше или включите папку входящих в настройках.",
        action: intakeOn
          ? null
          : {
              label: "К настройкам",
              icon: "settings",
              run: () => go("#/settings"),
            },
      });
    }
    return empty({
      icon: "inbox",
      title: "Здесь пусто",
      text: "Файлов с таким статусом нет.",
    });
  }

  function card(item) {
    const preview = item.preview;
    const title =
      (preview && preview.title) || item.filename || `Файл №${item.id}`;
    const statusText = INBOX_STATUS[item.status] || item.status;
    const main = h(
      "div",
      { class: "i-main" },
      h("h4", { text: title }),
      h(
        "div",
        { class: "row wrap" },
        preview ? sourceBadge(preview.source) : null,
        preview ? availabilityBadge(preview.availability) : null,
        h("span", {
          class: ["badge", STATUS_CLASS[item.status]],
          text: statusText,
        }),
        h("small", {
          class: "muted",
          text: [
            preview && preview.title ? item.filename : null,
            `получен ${ago(item.received_at)}`,
          ]
            .filter(Boolean)
            .join(" · "),
        }),
      ),
    );
    if (preview) {
      const details = h(
        "details",
        { class: "inbox-checks" },
        h("summary", { text: "Сведения и проверки" }),
        completeness(preview.completeness) || "",
        checksList(preview) || "",
      );
      main.append(pricesRow(preview), details);
      if (preview.observed_at)
        main.append(
          h("small", {
            class: "faint",
            text: `Наблюдение от ${dateTime(preview.observed_at)}`,
          }),
        );
    }
    if (item.status === "INVALID") {
      main.append(
        h(
          "div",
          { class: "callout bad" },
          icon("fail"),
          h("span", {
            text: item.error_message || item.error_code || "Файл не распознан",
          }),
        ),
      );
    }
    const side = h("div", { class: "i-side" });
    const media = {
      ...preview,
      title,
      inbox_id: item.id,
      media_url: `/api/inbox/${item.id}/media`,
    };
    const el = h(
      "article",
      {
        class: `card icard market-${preview?.source || "MANUAL"}`,
        dataset: { id: String(item.id) },
      },
      productImage(media),
      main,
      side,
    );
    if (preview) {
      let dialog = null;
      const disposePreview = (node) => {
        if (!node?.isConnected) return;
        node.querySelectorAll("video").forEach((v) => {
          v.pause();
          v.removeAttribute("src");
          v.load();
        });
        node.remove();
        if (dialog === node) {
          dialog = null;
          if (el.isConnected) el.focus({ preventScroll: true });
        }
      };
      const closePreview = () => {
        const node = dialog;
        if (!node) return;
        if (node.open) node.close();
        disposePreview(node);
      };
      const open = () => {
        if (dialog?.isConnected) return;
        dialog = h(
          "dialog",
          { class: "inbox-dialog", "aria-label": title },
          h("button", {
            class: "icon-btn inbox-close",
            type: "button",
            "aria-label": "Закрыть карточку",
            text: "×",
            onClick: closePreview,
          }),
          h(
            "div",
            { class: "product-showcase" },
            photoGallery(media),
            h(
              "div",
              { class: "product-purchase" },
              h("h2", { text: title }),
              sourceBadge(preview.source),
              pricesRow(preview),
              completeness(preview.completeness) || "",
              checksList(preview) || "",
            ),
          ),
        );
        const openedDialog = dialog;
        dialog.addEventListener("close", () => disposePreview(openedDialog));
        dialog.addEventListener("cancel", (e) => {
          e.preventDefault();
          closePreview();
        });
        dialog.addEventListener("click", (e) => {
          if (e.target === dialog) {
            const r = dialog.getBoundingClientRect();
            if (
              e.clientX < r.left ||
              e.clientX > r.right ||
              e.clientY < r.top ||
              e.clientY > r.bottom
            )
              closePreview();
          }
        });
        document.body.append(dialog);
        dialog.showModal();
        activateMedia(dialog);
      };
      activateObject(el, open, {
        label: `${title}. Просмотр входящего файла`,
        role: "button",
        dialog: true,
      });
      ctx.cleanup(() => {
        closePreview();
      });
    }
    if (item.status === "PENDING") {
      const accept = h(
        "button",
        { class: "btn primary", type: "button" },
        icon("check"),
        "Принять",
      );
      const reject = h(
        "button",
        { class: "btn ghost", type: "button" },
        icon("x"),
        "Отклонить",
      );
      accept.addEventListener("click", () =>
        withBusy(accept, () => decide(item, "accept", el)),
      );
      reject.addEventListener("click", () =>
        withBusy(reject, () => decide(item, "reject", el)),
      );
      side.append(accept, reject);
    } else if (item.decided_at) {
      side.append(
        h("small", {
          class: "muted",
          text: `${statusText} ${ago(item.decided_at)}`,
        }),
      );
    }
    if (preview && preview.url)
      side.append(
        h(
          "button",
          {
            class: "btn sm ghost",
            type: "button",
            onClick: () => openExternal(preview.url),
          },
          icon("external"),
          "Страница",
        ),
      );
    return el;
  }

  async function decide(item, action, el) {
    const name = (item.preview && item.preview.title) || item.filename || "";
    let result;
    try {
      result = await post(`/api/inbox/${item.id}/${action}`, {});
    } catch (error) {
      fail(
        error,
        action === "accept"
          ? "Не удалось принять файл"
          : "Не удалось отклонить файл",
      );
      return;
    }
    if (action === "accept") {
      toast({
        kind: "ok",
        title:
          result.observation_created === false
            ? "Такое наблюдение уже было"
            : "Файл принят",
        text: name,
        timeout: 4200,
        action: result.product_id
          ? {
              label: "Открыть товар",
              run: () => go(`#/products/${result.product_id}`),
            }
          : undefined,
      });
      for (const event of result.events || []) {
        const kind = event && typeof event === "object" ? event.kind : null;
        if (kind)
          toast({
            kind: "ok",
            title: eventMeta(kind).text,
            text: name,
            timeout: 6000,
          });
      }
    } else {
      toast({ title: "Файл отклонён", text: name, timeout: 2600 });
    }
    if (filter === "PENDING") await collapse(el);
    emit("data", { kind: "inbox" });
  }

  let timer = 0;
  let lastPending = status.inbox_pending || 0;
  const later = () => {
    clearTimeout(timer);
    timer = setTimeout(() => load(true), 300);
  };
  ctx.cleanup(
    on("data", (event) => {
      if (event && event.kind === "inbox") later();
    }),
  );
  ctx.cleanup(
    on("status", (value) => {
      const pending = (value && value.inbox_pending) || 0;
      if (pending !== lastPending) {
        lastPending = pending;
        later();
      }
    }),
  );
  ctx.cleanup(() => clearTimeout(timer));
  await load(false);
}

async function withBusy(button, task) {
  if (button.classList.contains("busy")) return;
  button.classList.add("busy");
  button.disabled = true;
  try {
    await task();
  } finally {
    button.classList.remove("busy");
    button.disabled = false;
  }
}

function pricesRow(preview) {
  const prices = preview.prices || [];
  if (!prices.length)
    return h("p", {
      class: "muted",
      text: preview.price_text || "Цена на странице не найдена",
    });
  let best = -1;
  prices.forEach((price, index) => {
    if (
      price.amount_minor !== null &&
      price.amount_minor !== undefined &&
      (best < 0 || price.amount_minor < prices[best].amount_minor)
    )
      best = index;
  });
  return h(
    "div",
    { class: "i-prices" },
    prices.map((price, index) =>
      h(
        "div",
        { class: ["ip", index === best && "best"] },
        h("b", {
          class: "num",
          text: money(
            price.amount_minor,
            price.currency || preview.currency || "RUB",
          ),
        }),
        h("small", {
          text:
            price.role_label || ROLE_LABELS[price.role] || price.label || "",
        }),
      ),
    ),
  );
}

function completeness(c) {
  if (!c || !c.total) return null;
  return h(
    "div",
    { class: "stack" },
    h("small", { class: "muted", text: `${c.known} / ${c.total}` }),
    h(
      "div",
      { class: "bar", style: { "--p": (c.known / c.total).toFixed(3) } },
      h("i"),
    ),
  );
}

function checksList(preview) {
  const items = [
    ...(preview.conflicts || []).map((item) => ({
      text:
        typeof item === "string"
          ? item
          : item.text || item.message || item.field || "Противоречие в данных",
      bad: true,
    })),
    ...(preview.checks || []).map((item) => ({
      text: item.text || item.code,
      bad: false,
    })),
  ];
  if (!items.length) return null;
  return h(
    "ul",
    { class: "checks" },
    items.map((item) =>
      h(
        "li",
        { class: item.bad ? "bad" : null },
        icon(item.bad ? "warn" : "info"),
        h("span", { text: item.text }),
      ),
    ),
  );
}