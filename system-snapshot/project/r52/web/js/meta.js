// R-52: справочники для отображения: источники, категории, статусы, подписи полей.
import { state } from "./state.js";

const SOURCE_COLORS = {
  OZON: "#3d7bff",
  WB: "#c13bff",
  AVITO: "#1fb8ff",
  YANDEX_MARKET: "#ffcc00",
  ALIEXPRESS: "#ff5a5f",
  MANUAL: "#a5a3c8",
};
const SOURCE_SHORT = { OZON: "Ozon", WB: "WB", AVITO: "Авито", YANDEX_MARKET: "Маркет", ALIEXPRESS: "AliExpress", MANUAL: "Вручную" };

export const sourceColor = (key) => SOURCE_COLORS[key] || "#a1a1c2";
export function sourceLabel(key) {
  if (SOURCE_SHORT[key]) return SOURCE_SHORT[key];
  const known = state.sources.get(key);
  return known ? known.name : (key || "-");
}
export function sourceName(key) {
  const known = state.sources.get(key);
  return known ? known.name : sourceLabel(key);
}

const CATEGORY_RULES = [
  [/rtx|gtx|geforce|radeon|\brx\s?\d{3,4}|видеокарт|\barc\s?a\d/i, "gpu"],
  [/ryzen|\bcore\s?i\d|процессор|xeon|athlon|\bcpu\b/i, "cpu"],
  [/монитор|\bmonitor\b|\bqhd\b|\buhd\b|\bips\b|\boled\b|\bmag\s?\d{3}q/i, "monitor"],
  [/\bssd\b|nvme|\bm\.2\b|\bhdd\b|ж[её]стк|накопител|\b9[89]0\s?(evo|pro)/i, "drive"],
  [/ddr\d|оперативн|памят|\bdimm\b|\bfury\b/i, "memory"],
  [/блок питания|\bбп\b|\b\d{3,4}\s?(w|вт)\b|\bpsu\b|be quiet/i, "zap"],
  [/материнск|motherboard|\b[bxzh]\d{3}[a-z]?\b/i, "board"],
  [/кулер|вентилятор|охлажд|cooler|\bak\d{3}\b|\bfan\b/i, "fan"],
];
export function categoryIcon(title) {
  const text = String(title || "");
  for (const [pattern, name] of CATEGORY_RULES) if (pattern.test(text)) return name;
  return "box";
}

export const AVAILABILITY = {
  IN_STOCK: { text: "в наличии", cls: "ok", icon: "check" },
  OUT_OF_STOCK: { text: "нет в наличии", cls: "bad", icon: "x" },
  UNKNOWN: { text: "наличие неизвестно", cls: "", icon: "info" },
};
export const AVAILABILITY_CHOICES = [["UNKNOWN", "неизвестно"], ["IN_STOCK", "в наличии"], ["OUT_OF_STOCK", "нет в наличии"]];

export const FIELDS = {
  title: "Название",
  seller: "Продавец",
  seller_rating: "Рейтинг продавца",
  item_rating: "Рейтинг товара",
  item_reviews: "Отзывы (кол-во)",
  model: "Модель",
  variant: "Модификация",
  displayed_price: "Отображаемая цена",
  price_conditions: "Условия цены",
  condition: "Состояние",
  bundle: "Комплект",
  seller_location: "Местонахождение продавца",
  availability: "Наличие",
  delivery: "Доставка",
  warranty: "Гарантия",
  returns: "Возврат",
  reviews: "Отзывы (текст)",
  questions: "Вопросы покупателей",
};
export const FIELD_STATES = {
  OBSERVED: { text: "видно на странице", cls: "seen" },
  USER_ENTERED: { text: "введено вручную", cls: "user" },
  SELLER_CLAIM: { text: "со слов продавца", cls: "claim" },
  UNKNOWN: { text: "нет данных", cls: "unknown" },
};
export const ROLE_LABELS = {
  regular: "обычная",
  card: "с картой банка",
  wallet: "с кошельком",
  coupon: "по купону",
  old: "старая, зачёркнутая",
  unknown: "условия не распознаны",
};
export const MANUAL_ROLES = ["regular", "card", "wallet", "coupon"];
export const BASIS_LABELS = { lowest_shown: "по минимальной показанной цене", regular: "по обычной цене" };

const EVENTS = {
  TARGET_REACHED: { text: "Цена достигла цели", icon: "target", color: "#34d399" },
  PRICE_DROPPED: { text: "Цена снизилась", icon: "trendDown", color: "#22d3ee" },
  BECAME_AVAILABLE: { text: "Появился в наличии", icon: "package2", color: "#a78bfa" },
  BECAME_UNAVAILABLE: { text: "Пропал из наличия", icon: "warn", color: "#fbbf24" },
};
export const eventMeta = (kind) => EVENTS[kind] || { text: String(kind || "Событие"), icon: "bell", color: "#a78bfa" };

export const TASK_STATUS = {
  QUEUED: { text: "в очереди", icon: "clock", cls: "violet" },
  RUNNING: { text: "выполняется", icon: "refresh", cls: "info" },
  COMPLETED: { text: "готово", icon: "check", cls: "ok" },
  PARTIAL: { text: "частично", icon: "warn", cls: "warn" },
  BLOCKED: { text: "не выполнено", icon: "lock", cls: "warn" },
  FAILED: { text: "ошибка", icon: "fail", cls: "bad" },
  CANCELLED: { text: "отменено", icon: "x", cls: "" },
  INTERRUPTED: { text: "прервано", icon: "pause", cls: "" },
};
export const taskStatus = (status) => TASK_STATUS[status] || { text: String(status || "-"), icon: "dot", cls: "" };

const TASK_KINDS = {
  backup: { title: "Резервная копия", icon: "archive", color: "#a78bfa" },
  maintenance: { title: "Обслуживание базы", icon: "wand", color: "#22d3ee" },
  digest: { title: "Сводка за сутки", icon: "file", color: "#f472b6" },
  import_legacy: { title: "Импорт старых баз R-52", icon: "upload", color: "#fbbf24" },
  collect: { title: "Сбор с источника", icon: "store", color: "#34d399" },
};
export const taskKind = (kind) => TASK_KINDS[kind] || { title: String(kind || "Задача"), icon: "tasks", color: "#a78bfa" };

export const INBOX_STATUS = { PENDING: "ожидает", ACCEPTED: "принято", REJECTED: "отклонено", INVALID: "с ошибкой" };
const ORIGINS = { MANUAL: "вручную", USER_FILE: "файл от пользователя", FOLDER: "папка входящих", LEGACY: "старые данные", USER: "пользователь", SCHEDULE: "расписание", ASSISTANT: "помощник" };
export const originLabel = (origin) => ORIGINS[origin] || String(origin || "-").toLowerCase().replaceAll("_", " ");
const EVIDENCE = { USER_ENTERED: "введено вручную", OBSERVED: "видно на странице", SELLER_CLAIM: "со слов продавца" };
export const evidenceLabel = (level) => EVIDENCE[level] || String(level || "-").toLowerCase().replaceAll("_", " ");

export function greeting(hour) {
  if (hour >= 5 && hour < 12) return "Доброе утро";
  if (hour >= 12 && hour < 18) return "Добрый день";
  if (hour >= 18 && hour < 23) return "Добрый вечер";
  return "Доброй ночи";
}