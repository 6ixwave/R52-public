// R-52: обращения к ядру. Каждый запрос несёт служебный заголовок окна, без него ядро откажет.
export class ApiError extends Error {
  constructor(status, code, message, detail) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail ?? null;
  }
}

const hooks = { authLost() {}, connection() {} };
export function configure(next) { Object.assign(hooks, next); }

export async function api(method, path, body, { timeout = 30000, keepalive = false } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = { "X-R52-UI": "1", Accept: "application/json" };
  const init = { method, headers, credentials: "same-origin", cache: "no-store", signal: controller.signal, keepalive };
  if (body !== undefined && method !== "GET" && method !== "HEAD") {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  let text = "";
  try {
    response = await fetch(path, init);
    text = await response.text();
  } catch (error) {
    clearTimeout(timer);
    if (error && error.name === "AbortError") throw new ApiError(0, "TIMEOUT", "Ядро не ответило вовремя");
    hooks.connection(false);
    throw new ApiError(0, "CORE_UNREACHABLE", "Нет связи с ядром");
  }
  clearTimeout(timer);
  hooks.connection(true);
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (!response.ok) {
    const error = (data && data.error) || {};
    if (response.status === 401) hooks.authLost();
    throw new ApiError(response.status, error.code || `HTTP_${response.status}`, error.message || `Ошибка ${response.status}`, error.detail);
  }
  return data;
}

export const get = (path, options) => api("GET", path, undefined, options);
export const post = (path, body = {}, options) => api("POST", path, body, options);
export const put = (path, body = {}, options) => api("PUT", path, body, options);
export const patch = (path, body = {}, options) => api("PATCH", path, body, options);
export const del = (path, options) => api("DELETE", path, undefined, options);

export function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const REASONS = {
  required: "нужно заполнить",
  invalid: "неверное значение",
  too_long: "слишком длинное значение",
  too_many: "слишком много элементов",
  range: "вне допустимого диапазона",
  choice: "недопустимый вариант",
  integer_required: "нужно целое число",
  boolean_required: "нужно да или нет",
  string_required: "нужен текст",
  object_required: "нужен набор значений",
  amount_required: "нужна сумма, например 29 990",
  http_url_required: "нужна ссылка http или https",
  path_required: "нужен путь к файлу",
  integer_list: "нужен список номеров",
  positive_integers_required: "нужны положительные номера",
  future: "дата из будущего",
  base64: "файл повреждён",
};
const FIELDS = {
  title: "название",
  url: "ссылка",
  amount: "сумма",
  target: "цель",
  drop_percent: "снижение",
  note: "заметка",
  name: "название",
  product_ids: "товары",
  text: "сообщение",
  d0: "файл D0",
  data01: "файл data01",
};

export function reasonText(detail) {
  if (!detail || typeof detail !== "object" || !detail.field) return "";
  const field = FIELDS[detail.field] || detail.field;
  const length = /^length_(\d+)_(\d+)$/.exec(detail.reason || "");
  const reason = REASONS[detail.reason] || (length ? `от ${length[1]} до ${length[2]} символов` : detail.reason || "");
  return reason ? `${field}: ${reason}` : field;
}

export function describe(error) {
  if (!(error instanceof ApiError)) return String((error && error.message) || error || "Неизвестная ошибка");
  const extra = reasonText(error.detail);
  return extra ? `${error.message}. ${extra[0].toUpperCase()}${extra.slice(1)}` : error.message;
}
