import { activateObject } from "./object-interactions.js";
// R-52: общие действия с товарами. Карточки, избранное, ручные цены, файлы Eye и сервисные команды.
import { productImage } from "./media.js";
import {
  h,
  icon,
  money,
  ago,
  count,
  bytes,
  parseRubles,
  randomKey,
} from "./dom.js";
import { get, post, del, query, reasonText } from "./api.js";
import {
  toast,
  fail,
  modal,
  field,
  selectEl,
  sourceBadge,
  availabilityBadge,
} from "./ui.js";
import {
  categoryIcon,
  eventMeta,
  taskKind,
  AVAILABILITY_CHOICES,
  MANUAL_ROLES,
  ROLE_LABELS,
} from "./meta.js";
import { sparkline } from "./chart.js";
import { emit } from "./state.js";
import { pop } from "./motion.js";

export function go(hash) {
  if (location.hash !== hash) location.hash = hash;
}

// Сумма из поля ввода для ядра: строка в рублях, null для пустого поля, NaN для непонятной записи.
export function amountInput(text) {
  const value = parseRubles(text);
  if (value === null) return null;
  return Number.isNaN(value) ? NaN : String(value);
}

export function cardHead({ icon: name, title, sub, tools } = {}) {
  const route = tools?.dataset?.objectRoute;
  const el = h(
    "div",
    { class: "card-head" },
    name ? icon(name) : null,
    h("h3", { text: title }),
    sub ? h("span", { class: "sub", text: sub }) : null,
    !route && tools ? h("div", { class: "tools" }, tools) : null,
  );
  if (route)
    activateObject(el, () => go(route), {
      label: `${title}. Перейти в раздел`,
    });
  return el;
}

export function priceParts(p) {
  const currency = p.currency || "RUB";
  if (p.lowest_minor === null || p.lowest_minor === undefined)
    return { main: "Цена неизвестна", extra: [], known: false };
  const extra = [];
  if (p.lowest_role && p.lowest_role !== "regular")
    extra.push(
      h("small", { text: ROLE_LABELS[p.lowest_role] || "с условием" }),
    );
  if (p.regular_minor && p.regular_minor !== p.lowest_minor)
    extra.push(
      h("small", { text: `обычная ${money(p.regular_minor, currency)}` }),
    );
  return { main: money(p.lowest_minor, currency), extra, known: true };
}

export function productCard(
  p,
  { spark, selected = false, onSelect, note } = {},
) {
  const star = h(
    "button",
    {
      class: ["icon-btn sm bare star", p.favorite && "on"],
      type: "button",
      title: "Избранное",
      "aria-pressed": String(Boolean(p.favorite)),
      "aria-label": p.favorite
        ? "Убрать из избранного"
        : "Добавить в избранное",
    },
    icon("star"),
  );
  star.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleFavorite(p, star);
  });
  const dot = onSelect
    ? h(
        "button",
        {
          class: "sel-dot",
          type: "button",
          title: "Выбрать для сравнения",
          "aria-pressed": String(selected),
          "aria-label": "Выбрать для сравнения",
        },
        icon("check"),
      )
    : null;
  const sparkBox = h("div", { class: "p-spark" });
  const price = priceParts(p);
  const el = h(
    "article",
    {
      class: [
        "card lift spot pcard",
        `market-${p.source}`,
        selected && "selected",
      ],
      tabindex: "0",
      dataset: { id: String(p.id) },
      "aria-label": p.title,
    },
    productImage(p),
    h("div", { class: "p-actions" }, star, dot),
    h(
      "div",
      { class: "p-main" },
      h("h4", { text: p.title }),
      h(
        "div",
        { class: "p-tags" },
        sourceBadge(p.source),
        availabilityBadge(p.availability),
        p.watched
          ? h("span", { class: "badge violet" }, icon("target"), "слежение")
          : null,
      ),
      note ? h("small", { class: "muted", text: note }) : null,
    ),
    h(
      "div",
      { class: "p-price" },
      h("b", { class: "num", text: price.main }),
      price.extra,
    ),
    sparkBox,
    h(
      "div",
      { class: "p-foot" },
      icon("clock", "i-sm"),
      h("span", {
        text: p.last_seen_at ? ago(p.last_seen_at) : "ещё без наблюдений",
      }),
      h("span", { class: "spacer" }),
      h("span", {
        text: count(
          p.observation_count || 0,
          "наблюдение",
          "наблюдения",
          "наблюдений",
        ),
      }),
    ),
  );
  if (dot) {
    dot.addEventListener("click", (event) => {
      event.stopPropagation();
      const next = !el.classList.contains("selected");
      if (onSelect(p, next) === false) return;
      el.classList.toggle("selected", next);
      dot.setAttribute("aria-pressed", String(next));
    });
  }
  activateObject(el, () => go(`#/products/${p.id}`), { label: p.title });
  el.setSpark = (points) =>
    sparkBox.replaceChildren(
      sparkline(points || [], { width: 240, height: 38 }),
    );
  if (spark) el.setSpark(spark);
  return el;
}

export async function loadSparks(ids, points = 24) {
  const list = Array.from(new Set(ids)).slice(0, 100);
  if (!list.length) return {};
  try {
    return (
      (await get(`/api/sparklines${query({ ids: list.join(","), points })}`))
        .items || {}
    );
  } catch {
    return {};
  }
}

export async function toggleFavorite(product, button) {
  const next = !product.favorite;
  try {
    if (next) await post(`/api/favorites/${product.id}`, {});
    else await del(`/api/favorites/${product.id}`);
  } catch (error) {
    fail(error);
    return product.favorite;
  }
  product.favorite = next;
  if (button) {
    button.classList.toggle("on", next);
    button.setAttribute("aria-pressed", String(next));
    button.setAttribute(
      "aria-label",
      next ? "Убрать из избранного" : "Добавить в избранное",
    );
    pop(button);
  }
  toast({
    kind: "ok",
    title: next ? "Добавлено в избранное" : "Убрано из избранного",
    text: product.title,
    timeout: 2600,
  });
  emit("data", { kind: "favorites", id: product.id });
  return next;
}

export async function openExternal(url) {
  if (!url) return;
  try {
    await post("/api/system/open-url", { url });
    toast({ title: "Открываю в браузере", text: url, timeout: 2600 });
  } catch {
    window.open(url, "_blank", "noopener");
  }
}

export function addProduct({ title = "" } = {}) {
  const titleInput = h("input", {
    class: "input",
    maxlength: "300",
    placeholder: "Например, Наушники Sony WH-1000XM5",
    value: title,
  });
  const urlInput = h("input", {
    class: "input",
    maxlength: "2000",
    placeholder: "https://www.ozon.ru/product/…",
    inputmode: "url",
    spellcheck: "false",
  });
  const priceInput = h("input", {
    class: "input num",
    inputmode: "decimal",
    placeholder: "29 990",
  });
  const availability = selectEl(AVAILABILITY_CHOICES, "UNKNOWN");
  const fTitle = field("Название", titleInput, { wide: true });
  const fUrl = field("Ссылка на карточку", urlInput, {
    wide: true,
    hint: "Необязательно. По ссылке R-52 узнает магазин и номер товара.",
  });
  const fPrice = field("Цена сейчас, ₽", priceInput, {
    hint: "Можно добавить позже",
  });
  const fAvailability = field("Наличие", availability);
  modal({
    title: "Новый товар",
    width: 580,
    body: h("div", { class: "form-grid" }, fTitle, fUrl, fPrice, fAvailability),
    actions: [
      { label: "Отмена", kind: "ghost" },
      {
        label: "Добавить",
        kind: "primary",
        icon: "plus",
        run: async () => {
          for (const item of [fTitle, fUrl, fPrice]) item.setError("");
          const name = titleInput.value.trim();
          if (!name) {
            fTitle.setError("Введите название");
            titleInput.focus();
            return false;
          }
          const amount = amountInput(priceInput.value);
          if (Number.isNaN(amount)) {
            fPrice.setError("Нужна сумма, например 29 990");
            priceInput.focus();
            return false;
          }
          let detail;
          try {
            detail = await post("/api/products/manual", {
              title: name,
              url: urlInput.value.trim() || undefined,
            });
          } catch (error) {
            const name = error.detail && error.detail.field;
            const target =
              name === "url" ? fUrl : name === "title" ? fTitle : null;
            if (target) {
              target.setError(reasonText(error.detail));
              return false;
            }
            throw error;
          }
          const id = detail.product.id;
          if (amount !== null) {
            try {
              await post(`/api/products/${id}/observations/manual`, {
                amount,
                availability: availability.value,
              });
            } catch (error) {
              fail(error, "Товар добавлен, но цену сохранить не удалось");
            }
          }
          toast({
            kind: "ok",
            title: "Товар добавлен",
            text: name,
            timeout: 3200,
          });
          emit("data", { kind: "products", id });
          go(`#/products/${id}`);
          return true;
        },
      },
    ],
  });
}

export function addObservation(product, { onDone } = {}) {
  const amountEl = h("input", {
    class: "input num",
    inputmode: "decimal",
    placeholder: "29 990",
  });
  const role = selectEl(
    MANUAL_ROLES.map((key) => [key, ROLE_LABELS[key]]),
    "regular",
  );
  const availability = selectEl(
    AVAILABILITY_CHOICES,
    product.availability || "UNKNOWN",
  );
  const when = h("input", { class: "input", type: "datetime-local" });
  const note = h("input", {
    class: "input",
    maxlength: "500",
    placeholder: "Например, цена по карте банка",
  });
  const fAmount = field("Цена, ₽", amountEl);
  const fWhen = field("Когда видели", when, { hint: "Пусто: прямо сейчас" });
  modal({
    title: "Новая цена",
    width: 580,
    body: h(
      "div",
      { class: "stack" },
      h("p", { class: "muted", text: product.title }),
      h(
        "div",
        { class: "form-grid" },
        fAmount,
        field("Какая это цена", role),
        field("Наличие", availability),
        fWhen,
        field("Заметка", note, { wide: true }),
      ),
    ),
    actions: [
      { label: "Отмена", kind: "ghost" },
      {
        label: "Сохранить",
        kind: "primary",
        icon: "save",
        run: async () => {
          fAmount.setError("");
          fWhen.setError("");
          const amount = amountInput(amountEl.value);
          if (amount === null || Number.isNaN(amount)) {
            fAmount.setError("Нужна сумма, например 29 990");
            amountEl.focus();
            return false;
          }
          const body = {
            amount,
            role: role.value,
            availability: availability.value,
            note: note.value.trim() || undefined,
          };
          if (when.value) {
            const date = new Date(when.value);
            if (
              Number.isNaN(date.getTime()) ||
              date.getTime() > Date.now() + 300000
            ) {
              fWhen.setError("Дата не может быть в будущем");
              return false;
            }
            body.observed_at = date.toISOString();
          }
          let result;
          try {
            result = await post(
              `/api/products/${product.id}/observations/manual`,
              body,
            );
          } catch (error) {
            const name = error.detail && error.detail.field;
            if (name === "amount") {
              fAmount.setError(reasonText(error.detail));
              return false;
            }
            if (name === "observed_at") {
              fWhen.setError(reasonText(error.detail));
              return false;
            }
            throw error;
          }
          toast({
            kind: "ok",
            title: "Цена сохранена",
            text: `${product.title}: ${money(Math.round(Number(amount) * 100))}`,
            timeout: 3200,
          });
          const events = Array.isArray(result.events) ? result.events : [];
          const kinds = events
            .map((event) =>
              event && typeof event === "object" ? event.kind : null,
            )
            .filter(Boolean);
          for (const kind of kinds)
            toast({
              kind: "ok",
              title: eventMeta(kind).text,
              text: product.title,
              timeout: 6000,
            });
          if (!kinds.length && events.length)
            toast({
              kind: "ok",
              title: "Есть новые события",
              text: "Подробности в уведомлениях",
              timeout: 5000,
            });
          emit("data", { kind: "observation", id: product.id });
          if (onDone) onDone(result.product);
          return true;
        },
      },
    ],
  });
}

export function pickFiles(onPicked) {
  const input = h("input", {
    type: "file",
    accept: ".json,application/json",
    multiple: true,
    hidden: true,
  });
  input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    input.remove();
    if (files.length) onPicked(files);
  });
  document.body.append(input);
  input.click();
}

export const pickAndImport = () => pickFiles((files) => importFiles(files));

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () =>
      reject(reader.error || new Error("Файл не прочитался"));
    reader.readAsDataURL(file);
  });
}

export async function importFiles(files) {
  const results = [];
  for (const file of files) {
    if (file.size > 16 * 1024 * 1024) {
      toast({ kind: "warn", title: "Файл слишком большой", text: file.name });
      continue;
    }
    try {
      const data = await readBase64(file);
      results.push(
        await post("/api/inbox/import", {
          filename: file.name.slice(0, 200),
          data_base64: data,
        }),
      );
    } catch (error) {
      fail(error, `Не удалось загрузить ${file.name}`);
    }
  }
  if (!results.length) return results;
  const repeated = results.filter((result) => result.created === false).length;
  const invalid = results.filter(
    (result) =>
      result.created !== false &&
      result.item &&
      result.item.status === "INVALID",
  ).length;
  const fresh = results.length - repeated - invalid;
  const parts = [];
  if (fresh) parts.push(`на проверке: ${fresh}`);
  if (invalid) parts.push(`с ошибкой: ${invalid}`);
  if (repeated) parts.push(`уже были загружены: ${repeated}`);
  toast({
    kind: invalid ? "warn" : "ok",
    title: `Загружено ${count(results.length, "файл", "файла", "файлов")}`,
    text: parts.join(", "),
    timeout: 4800,
  });
  emit("data", { kind: "inbox" });
  return results;
}

export async function runTask(kind, params = {}) {
  // Новое: показываем уведомление "Отправка...", чтобы было видно, что клик сработал
  let pendingToast = toast({
    kind: "info",
    title: "Отправка задачи...",
    text: "Ожидаю ответа от ядра R-52",
    timeout: 0, // не закрывать автоматически
  });

  try {
    const result = await post("/api/tasks", {
      kind,
      params,
      idempotency_key: randomKey(),
    });
    
    pendingToast.close();
    
    toast({
      kind: "ok",
      title:
        result.created === false
          ? "Задача уже в работе"
          : "Задача создана!",
      text: taskKind(kind).title,
      timeout: 3200,
      action: { label: "К задачам", run: () => go("#/tasks") },
    });
    emit("data", { kind: "tasks" });
    return result.task;
  } catch (error) {
    pendingToast.close();
    fail(error, "Ядро не ответило"); // Покажет красную ошибку, если задача не создалась
    return null;
  }
}

export async function backupNow() {
  try {
    const result = await post("/api/backups", {});
    toast({
      kind: "ok",
      title: "Резервная копия создаётся",
      text: "Готовая копия появится в разделе «Сервис».",
      timeout: 3400,
    });
    emit("data", { kind: "tasks" });
    return result.task;
  } catch (error) {
    fail(error);
    return null;
  }
}

export async function exportNow() {
  try {
    const result = await post("/api/export", {});
    toast({
      kind: "ok",
      title: "Данные выгружены",
      text: `${count(result.products || 0, "товар", "товара", "товаров")}, ${count(result.prices || 0, "цена", "цены", "цен")} · ${bytes(result.size)}`,
      timeout: 6000,
      action: { label: "Открыть папку", run: () => openFolder("exports") },
    });
    return result;
  } catch (error) {
    fail(error);
    return null;
  }
}

export async function openFolder(which) {
  try {
    const result = await post("/api/system/open-folder", { which });
    toast({ title: "Папка открыта", text: result.path, timeout: 2800 });
  } catch (error) {
    fail(error, "Папку открыть не удалось");
  }
}