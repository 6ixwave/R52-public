// R-52: слежение за ценой и сравнение товаров: окна настройки, таблица сравнения и списки.
import { analysisPanel } from "./analysis-panel.js";
import { productImage } from "./media.js";
import { h, icon, count, dateTime, rublesText } from "./dom.js";
import { get, post, put, del, reasonText } from "./api.js";
import {
  toast,
  fail,
  modal,
  confirm,
  segmented,
  switchEl,
  field,
  sourceBadge,
} from "./ui.js";
import { BASIS_LABELS, FIELD_STATES } from "./meta.js";
import { emit } from "./state.js";
import { go, amountInput } from "./actions.js";

export const MAX_GROUP = 12;

// Поле без label: внутри кнопки, и щелчок по подписи не должен нажимать первую из них.
function plainField(label, control, hint, wide) {
  return h(
    "div",
    { class: ["field", wide && "wide"] },
    h("span", { class: "label", text: label }),
    control,
    hint || null,
  );
}

function switchRow(control, text) {
  const row = h("div", { class: "check" }, control, h("span", { text }));
  row.addEventListener("click", (event) => {
    if (!control.contains(event.target)) control.click();
  });
  return row;
}

export function editWatch(product, watch, { onDone } = {}) {
  const current = watch || {};
  const target = h("input", {
    class: "input num",
    inputmode: "decimal",
    placeholder: "25 000",
    value: current.target_minor ? rublesText(current.target_minor) : "",
  });
  const drop = h("input", {
    class: "input num",
    inputmode: "numeric",
    placeholder: "10",
    value: current.drop_percent ?? "",
  });
  let basis = current.price_basis || "lowest_shown";
  const basisHint = h("span", { class: "hint", text: BASIS_LABELS[basis] });
  const basisSeg = segmented(
    [
      { value: "lowest_shown", label: "Минимальная показанная" },
      { value: "regular", label: "Обычная" },
    ],
    basis,
    (next) => {
      basis = next;
      basisHint.textContent = BASIS_LABELS[next];
    },
    { size: "sm", label: "По какой цене считать" },
  );
  const notify = switchEl(
    Boolean(current.notify_availability),
    null,
    "Сообщить о появлении в наличии",
  );
  const active = switchEl(current.active !== false, null, "Слежение включено");
  const fTarget = field("Цель, ₽", target, {
    hint: "Сообщу, когда цена станет не выше",
  });
  const fDrop = field("Или снижение, %", drop, {
    hint: "От последней цены, от 1 до 90",
  });
  const body = h(
    "div",
    { class: "stack" },
    h("p", { class: "muted", text: product.title }),
    h(
      "div",
      { class: "form-grid" },
      fTarget,
      fDrop,
      plainField("Считать по цене", basisSeg, basisHint, true),
    ),
    switchRow(notify, "Сообщить, когда товар появится в наличии"),
    watch ? switchRow(active, "Слежение включено") : null,
    h("p", {
      class: "faint",
      text: "Условия цены, наличие и доставку во Владивосток R-52 не проверяет: перед покупкой сверьте их на странице магазина.",
    }),
  );

  async function save() {
    fTarget.setError("");
    fDrop.setError("");
    const amount = amountInput(target.value);
    if (Number.isNaN(amount)) {
      fTarget.setError("Нужна сумма, например 25 000");
      target.focus();
      return false;
    }
    const dropText = String(drop.value).trim();
    const dropValue = dropText ? Number(dropText.replace(",", ".")) : null;
    if (
      dropText &&
      !(Number.isInteger(dropValue) && dropValue >= 1 && dropValue <= 90)
    ) {
      fDrop.setError("Целое число от 1 до 90");
      drop.focus();
      return false;
    }
    if (amount === null && dropValue === null) {
      fTarget.setError("Укажите цель или процент снижения");
      target.focus();
      return false;
    }
    const payload = {
      price_basis: basis,
      notify_availability: notify.getValue(),
      active: watch ? active.getValue() : true,
    };
    if (amount !== null) payload.target = amount;
    if (dropValue !== null) payload.drop_percent = dropValue;
    let result;
    try {
      result = await put(`/api/watches/${product.id}`, payload);
    } catch (error) {
      const name = error.detail && error.detail.field;
      if (name === "target") {
        fTarget.setError(reasonText(error.detail));
        return false;
      }
      if (name === "drop_percent") {
        fDrop.setError(reasonText(error.detail));
        return false;
      }
      throw error;
    }
    toast({
      kind: "ok",
      title: watch ? "Слежение обновлено" : "Слежение включено",
      text: product.title,
      timeout: 3000,
    });
    emit("data", { kind: "watches", id: product.id });
    if (onDone) onDone(result.watch);
    return true;
  }

  async function remove() {
    const ok = await confirm({
      title: "Снять слежение?",
      text: product.title,
      ok: "Снять",
      danger: true,
    });
    if (!ok) return false;
    await del(`/api/watches/${product.id}`);
    toast({
      kind: "ok",
      title: "Слежение снято",
      text: product.title,
      timeout: 2600,
    });
    emit("data", { kind: "watches", id: product.id });
    if (onDone) onDone(null);
    return true;
  }

  modal({
    title: watch ? "Слежение за ценой" : "Следить за ценой",
    width: 580,
    body,
    actions: [
      watch
        ? { label: "Снять", kind: "ghost", icon: "trash", run: remove }
        : null,
      { label: "Отмена", kind: "ghost" },
      { label: "Сохранить", kind: "primary", icon: "target", run: save },
    ].filter(Boolean),
  });
}

// Пауза и возобновление: ядро заменяет настройки целиком, поэтому передаём и остальные поля.
export async function setWatchActive(watch, activeValue) {
  const payload = {
    price_basis: watch.price_basis,
    notify_availability: Boolean(watch.notify_availability),
    active: activeValue,
  };
  if (watch.target_minor) payload.target = rublesText(watch.target_minor);
  if (watch.drop_percent) payload.drop_percent = watch.drop_percent;
  const result = await put(`/api/watches/${watch.product_id}`, payload);
  emit("data", { kind: "watches", id: watch.product_id });
  return result.watch;
}

export function compareTable(table) {
  const columns = table.columns || [];
  const head = h(
    "tr",
    null,
    h("th", { scope: "col" }),
    columns.map((column) =>
      h(
        "th",
        { scope: "col" },
        h(
          "div",
          { class: "stack" },
          productImage(column, true),
          sourceBadge(column.source),
          h("a", {
            href: `#/products/${column.product_id}`,
            text: column.title,
          }),
        ),
      ),
    ),
  );
  const rows = (table.rows || []).map((row) =>
    h(
      "tr",
      null,
      h("th", { scope: "row", text: row.label }),
      (row.cells || []).map((cell) =>
        h("td", {
          class: [cell.best && "best", cell.state === "UNKNOWN" && "unknown"],
          title: (FIELD_STATES[cell.state] || {}).text,
          text: cell.time ? dateTime(cell.time) : cell.text,
        }),
      ),
    ),
  );
  return h(
    "div",
    { class: "stack" },
    h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        { class: "table" },
        h("thead", null, head),
        h("tbody", null, rows),
      ),
    ),
    (table.notes || []).map((note) =>
      h("div", { class: "callout" }, icon("info"), h("span", { text: note })),
    ),
  );
}

export async function showCompare({ ids = [], groupId = null } = {}) {
  let data;
  try {
    data = groupId
      ? await get(`/api/compare/${groupId}`)
      : await post("/api/compare/preview", { product_ids: ids });
  } catch (error) {
    fail(error, "Сравнение не открылось");
    return;
  }
  const nameInput = h("input", {
    class: "input",
    maxlength: "120",
    placeholder: "Например, Подарки к Новому году",
  });
  const fName = field("Сохранить как список", nameInput, {
    hint: "Список появится в разделе «Слежение»",
  });
  const size =
    (data.table && data.table.columns
      ? data.table.columns.length
      : ids.length) || 0;
  const actions = groupId
    ? [
        {
          label: "Удалить список",
          kind: "ghost",
          icon: "trash",
          run: async () => {
            const ok = await confirm({
              title: "Удалить список сравнения?",
              text: `«${data.group.name}». Товары останутся в базе.`,
              ok: "Удалить",
              danger: true,
            });
            if (!ok) return false;
            await del(`/api/compare/${groupId}`);
            toast({
              kind: "ok",
              title: "Список удалён",
              text: data.group.name,
              timeout: 2600,
            });
            emit("data", { kind: "compare" });
            return true;
          },
        },
      ]
    : [
        {
          label: "Сохранить",
          kind: "primary",
          icon: "save",
          run: async () => {
            fName.setError("");
            const name = nameInput.value.trim();
            if (!name) {
              fName.setError("Введите название списка");
              nameInput.focus();
              return false;
            }
            const result = await post("/api/compare", {
              name,
              product_ids: ids,
            });
            const gid = result.group && result.group.id;
            toast({
              kind: "ok",
              title: "Сравнение сохранено",
              text: name,
              timeout: 4000,
              action: { label: "К спискам", run: () => go("#/watch/compare") },
            });
            emit("data", { kind: "compare", id: gid });
            return true;
          },
        },
      ];
  modal({
    title: groupId
      ? data.group.name
      : `Сравнение: ${count(size, "товар", "товара", "товаров")}`,
    width: 960,
    body: h(
      "div",
      { class: "stack" },
      groupId ? null : fName,
      compareTable(data.table || {}),
      analysisPanel({ids:(data.table?.columns||[]).map(c=>c.product_id),kind:'compare'}),
    ),
    actions,
  });
}

export async function addToCompare(product) {
  let groups = [];
  try {
    groups = (await get("/api/compare")).items || [];
  } catch (error) {
    fail(error);
    return;
  }
  const nameInput = h("input", {
    class: "input",
    maxlength: "120",
    placeholder: "Название нового списка",
  });
  const fName = field("Новый список", nameInput, { wide: true });
  let dialog = null;
  const pick = async (group, button) => {
    if (button.classList.contains("busy")) return;
    button.classList.add("busy");
    try {
      await post(`/api/compare/${group.id}/items`, { product_id: product.id });
      toast({
        kind: "ok",
        title: "Добавлено в сравнение",
        text: group.name,
        timeout: 3200,
        action: {
          label: "Открыть",
          run: () => showCompare({ groupId: group.id }),
        },
      });
      emit("data", { kind: "compare", id: group.id });
      if (dialog) dialog.close();
    } catch (error) {
      fail(error);
    } finally {
      button.classList.remove("busy");
    }
  };
  const list = groups.length
    ? h(
        "div",
        { class: "stack" },
        h("span", { class: "section-label", text: "В существующий список" }),
        groups.map((group) => {
          const button = h(
            "button",
            { class: "list-row", type: "button" },
            icon("compare"),
            h("span", { class: "ellipsis", text: group.name }),
            h("span", { class: "spacer" }),
            h("small", {
              class: "muted",
              text: count(group.size || 0, "товар", "товара", "товаров"),
            }),
          );
          button.addEventListener("click", () => pick(group, button));
          return button;
        }),
      )
    : null;
  dialog = modal({
    title: "Добавить в сравнение",
    width: 520,
    body: h(
      "div",
      { class: "stack" },
      h("p", { class: "muted", text: product.title }),
      list,
      h("div", { class: "form-grid" }, fName),
    ),
    actions: [
      { label: "Отмена", kind: "ghost" },
      {
        label: "Создать список",
        kind: "primary",
        icon: "plus",
        run: async () => {
          fName.setError("");
          const name = nameInput.value.trim();
          if (!name) {
            fName.setError("Введите название");
            nameInput.focus();
            return false;
          }
          const result = await post("/api/compare", {
            name,
            product_ids: [product.id],
          });
          toast({
            kind: "ok",
            title: "Список создан",
            text: name,
            timeout: 3200,
          });
          emit("data", {
            kind: "compare",
            id: result.group && result.group.id,
          });
          return true;
        },
      },
    ],
  });
}
