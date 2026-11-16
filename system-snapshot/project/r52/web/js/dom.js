// R-52: построение DOM, иконки и форматирование значений.
// Данные всегда попадают в страницу как текст. Разметка берётся только из статичного набора иконок.
import { ICONS } from "./icons.js";

const SVG_NS = "http://www.w3.org/2000/svg";

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) setProps(el, props);
  append(el, children);
  if (props && props.value !== undefined && props.value !== null) el.value = props.value;
  return el;
}

export function svg(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value !== undefined && value !== null && value !== false) el.setAttribute(key, String(value));
    }
  }
  return append(el, children);
}

function setProps(el, props) {
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false || key === "value") continue;
    if (key === "class") el.className = Array.isArray(value) ? value.filter(Boolean).join(" ") : String(value);
    else if (key === "text") el.textContent = String(value);
    else if (key === "style") setStyle(el, value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
}

// Стили только через CSSOM: атрибут style запрещён политикой CSP.
export function setStyle(el, styles) {
  for (const [key, value] of Object.entries(styles)) {
    if (value === undefined || value === null) el.style.removeProperty(key);
    else el.style.setProperty(key, String(value));
  }
  return el;
}

export function append(parent, children) {
  for (const child of [children].flat(Infinity)) {
    if (child === undefined || child === null || child === false || child === true) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function icon(name, cls) {
  const el = document.createElementNS(SVG_NS, "svg");
  el.setAttribute("viewBox", "0 0 24 24");
  el.setAttribute("fill", "none");
  el.setAttribute("stroke", "currentColor");
  el.setAttribute("stroke-width", "2");
  el.setAttribute("stroke-linecap", "round");
  el.setAttribute("stroke-linejoin", "round");
  el.setAttribute("aria-hidden", "true");
  el.setAttribute("class", cls ? `i ${cls}` : "i");
  el.innerHTML = ICONS[name] || ICONS.dot;
  return el;
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
export function clear(el) { el.replaceChildren(); return el; }
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));

let uidCounter = 0;
export const uid = (prefix = "u") => `${prefix}${++uidCounter}`;

export function hash(value) {
  let result = 2166136261;
  for (const ch of String(value)) {
    result ^= ch.charCodeAt(0);
    result = Math.imul(result, 16777619);
  }
  return result >>> 0;
}

export function debounce(fn, ms) {
  let timer = 0;
  const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

export function randomKey() {
  if (globalThis.crypto && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

// Числа и деньги. Суммы приходят в копейках.
const numberCache = new Map();
function numberFormat(digits) {
  let fmt = numberCache.get(digits);
  if (!fmt) {
    fmt = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits });
    numberCache.set(digits, fmt);
  }
  return fmt;
}
export function number(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return numberFormat(digits).format(Number(value));
}

const moneyCache = new Map();
export function money(minor, currency = "RUB") {
  if (minor === null || minor === undefined || Number.isNaN(Number(minor))) return "-";
  const value = Number(minor) / 100;
  const whole = Number.isInteger(value);
  const key = `${currency}:${whole}`;
  let fmt = moneyCache.get(key);
  if (!fmt) {
    const digits = whole ? 0 : 2;
    try {
      fmt = new Intl.NumberFormat("ru-RU", { style: "currency", currency: currency || "RUB", minimumFractionDigits: digits, maximumFractionDigits: digits });
    } catch {
      fmt = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    }
    moneyCache.set(key, fmt);
  }
  return fmt.format(value);
}

const compactFmt = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 });
export function compactMoney(minor, currency = "RUB") {
  if (minor === null || minor === undefined) return "-";
  return `${compactFmt.format(Number(minor) / 100)}${currency === "RUB" ? " \u20bd" : ""}`;
}

// Сумма для поля ввода и обратно: принимаем пробелы и запятую.
export function rublesText(minor) {
  if (minor === null || minor === undefined) return "";
  return String(Math.round(Number(minor)) / 100);
}
export function parseRubles(text) {
  const cleaned = String(text ?? "").replace(/[\s\u00a0\u202f\u20bd]/g, "").replace(",", ".");
  if (!cleaned) return null;
  const value = Number(cleaned);
  return Number.isFinite(value) && value > 0 ? Math.round(value * 100) / 100 : NaN;
}

export function percent(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${number(Math.abs(Number(value)), digits)} %`;
}

export function bytes(value) {
  if (value === null || value === undefined) return "-";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let size = Number(value);
  let index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${number(size, index ? 1 : 0)} ${units[index]}`;
}

const pluralRules = new Intl.PluralRules("ru");
export function plural(n, one, few, many) {
  const form = pluralRules.select(Number(n));
  return form === "one" ? one : form === "few" ? few : many;
}
export function count(n, one, few, many) {
  return `${number(n)} ${plural(n, one, few, many)}`;
}

// Даты показываем в часовом поясе ядра, чтобы окно и ядро говорили об одном времени.
let timeZone;
const dateCache = new Map();
export function setTimeZone(zone) {
  try {
    new Intl.DateTimeFormat("ru-RU", { timeZone: zone }).format(0);
    timeZone = zone;
  } catch {
    timeZone = undefined;
  }
  dateCache.clear();
}
function dateFormat(key, options) {
  let fmt = dateCache.get(key);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("ru-RU", { ...options, timeZone });
    dateCache.set(key, fmt);
  }
  return fmt;
}
export function toDate(value) {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}
const formatted = (key, options) => (value) => {
  const date = toDate(value);
  return date ? dateFormat(key, options).format(date) : "-";
};
export const dateTime = formatted("dt", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
export const dateShort = formatted("ds", { day: "numeric", month: "short" });
export const dateLong = formatted("dl", { weekday: "long", day: "numeric", month: "long" });
export const dateFull = formatted("df", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" });
export const timeOnly = formatted("to", { hour: "2-digit", minute: "2-digit" });
export function hourNow() {
  const text = dateFormat("hr", { hour: "numeric", hourCycle: "h23" }).format(new Date());
  return Number.parseInt(text, 10) || 0;
}

const relative = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
const UNITS = [["minute", 60], ["hour", 3600], ["day", 86400], ["week", 604800], ["month", 2629800], ["year", 31557600]];
export function ago(value) {
  const date = toDate(value);
  if (!date) return "-";
  const diff = (date.getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return diff > 0 ? "сейчас" : "только что";
  let unit = "minute";
  let size = 60;
  for (const [name, seconds] of UNITS) {
    if (abs >= seconds) {
      unit = name;
      size = seconds;
    }
  }
  return relative.format(Math.round(diff / size), unit);
}

export const capitalize = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);
