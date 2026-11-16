"""Нормализация наблюдений из выгрузок Eye.

Правила перенесены из DATA-01: значение без доказательства остаётся UNKNOWN,
слова продавца остаются SELLER_CLAIM, ничего не считается независимо проверенным.
"""

import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from ..errors import AppError
from .media import raster_data, normalize_media
from ..money import CURRENCIES, format_minor, parse_amount, to_minor
from ..timeutil import iso, parse_aware

VERSION = "R52_NORMALIZED_3"
EYE_SCOPE = "USER_EYE_EXPORT"

FIELDS = {
    "title": "Название",
    "seller": "Продавец",
    "seller_rating": "Рейтинг продавца",
    "item_rating": "Рейтинг товара",
    "item_reviews": "Отзывы (кол-во)",
    "model": "Модель",
    "variant": "Модификация",
    "displayed_price": "Отображаемая цена",
    "price_conditions": "Условия цены",
    "condition": "Состояние",
    "bundle": "Комплект",
    "seller_location": "Местонахождение продавца",
    "availability": "Наличие",
    "delivery": "Доставка",
    "warranty": "Гарантия",
    "returns": "Возврат",
    "reviews": "Отзывы (текст)",
    "questions": "Вопросы покупателей",
}
SELLER_FIELDS = frozenset({
    "seller", "seller_rating", "item_rating", "item_reviews",
    "model", "variant", "condition", "bundle", "seller_location",
    "availability", "delivery", "warranty", "returns",
})
ALIASES = {
    "seller_location": ("seller_location", "city"),
    "reviews": ("reviews", "review_summary"),
    "questions": ("questions", "buyer_questions"),
    "seller": ("seller", "supplier"),
    "seller_rating": ("seller_rating", "supplier_rating"),
    "item_rating": ("item_rating", "review_rating"),
    "item_reviews": ("item_reviews", "feedbacks"),
}
HOSTS = {
    "FARPOST": frozenset({"farpost.ru", "www.farpost.ru"}),
    "AVITO": frozenset({"avito.ru", "www.avito.ru", "m.avito.ru"}),
    "OZON": frozenset({"ozon.ru", "www.ozon.ru"}),
    "WB": frozenset({"wildberries.ru", "www.wildberries.ru"}),
    "ALIEXPRESS": frozenset({"aliexpress.ru", "www.aliexpress.ru", "aliexpress.com", "www.aliexpress.com",
                             "de.aliexpress.com", "es.aliexpress.com", "fr.aliexpress.com",
                             "it.aliexpress.com", "pt.aliexpress.com", "pl.aliexpress.com",
                             "nl.aliexpress.com", "tr.aliexpress.com", "ar.aliexpress.com",
                             "he.aliexpress.com", "ja.aliexpress.com", "ko.aliexpress.com",
                             "ru.aliexpress.com", "en.aliexpress.com"}),
    "ALIEXPRESS_RU": frozenset({"ru.aliexpress.com", "www.ru.aliexpress.com", "aliexpress.ru", "www.aliexpress.ru"}),
}
ID_PATTERNS = {
    "FARPOST": re.compile(r"-?(?:g)?(\d{1,24})\.html/?$"),
    "AVITO": re.compile(r"(?:/ad[-/]|[_/-])(\d{1,24})/?$"),
    "OZON": re.compile(r"/(\d{1,24})/?$|-(\d{1,24})/?$"),
    "WB": re.compile(r"/catalog/(\d{1,24})/detail\.aspx/?$"),
    "ALIEXPRESS": re.compile(r"/item/(\d{1,24})(?:\.html)?/?$"),
    "ALIEXPRESS_RU": re.compile(r"/item/(\d{1,24})(?:\.html)?/?$"),
}
PRIVATE_PATH = re.compile(
    r"/(?:auth|login|register|checkout|basket|cart|order|payment|profile|account|cabinet|lk)(?:/|$)",
    re.IGNORECASE,
)
SOURCE_ALIASES = {"ALI": "ALIEXPRESS", "WILDBERRIES": "WB"}

ROLE_MAP = {
    "regular": "regular", "price": "regular", "base": "regular", "current": "regular",
    "catalog": "regular", "public_unit": "regular",
    "card": "card", "ozon_bank": "card", "ozon_card": "card", "bank_card": "card", "wb_card": "card",
    "wallet": "wallet", "wb_wallet": "wallet",
    "coupon": "coupon", "promo": "coupon", "promocode": "coupon",
    "old": "old", "crossed": "old", "strikethrough": "old", "before_discount": "old",
    "unknown": "unknown",
}
LABEL_ROLE_RULES = (
    (re.compile(r"ozon\s*банк|с\s+картой|картой\s+банка|по\s+карте", re.IGNORECASE), "card"),
    (re.compile(r"кошел[её]к", re.IGNORECASE), "wallet"),
    (re.compile(r"купон|промокод", re.IGNORECASE), "coupon"),
)
CHARACTERISTIC_RULES = (
    ("variant", re.compile(r"партномер|mpn|артикул производителя|код производителя|part number")),
    ("warranty", re.compile(r"гаранти(?:я(?:\s+(?:ozon|озон))?|йный срок)")),
    ("returns", re.compile(r"возврат|условия возврата")),
    ("bundle", re.compile(r"комплектация|комплект поставки")),
    ("availability", re.compile(r"наличие|доступность")),
    ("condition", re.compile(r"состояние(?: товара)?")),
    ("seller_location", re.compile(r"местонахождение|город продавца")),
)
DESCRIPTION_RULES = (
    ("warranty", re.compile(r"гаранти", re.IGNORECASE)),
    ("returns", re.compile(r"возврат|возвращ", re.IGNORECASE)),
    ("bundle", re.compile(r"комплект|коробк|упаковк|кабел|переходник", re.IGNORECASE)),
    ("delivery", re.compile(r"достав|самовывоз|отправ", re.IGNORECASE)),
)
PRIOR_USE = re.compile(r"установ|использовал|эксплуатац|обслуживан|майнинг|ремонт|вскрыт", re.IGNORECASE)
CONDITION_MAP = {
    "б/у": "USED", "бу": "USED", "used": "USED", "бывший в употреблении": "USED",
    "новое": "NEW", "новый": "NEW", "новая": "NEW", "new": "NEW",
}
OUT_OF_STOCK = re.compile(r"нет в наличии|законч|распродан|снят с продаж|недоступ|out of stock|sold out", re.IGNORECASE)
IN_STOCK = re.compile(r"в наличии|есть на складе|in stock|осталось\s+\d|\d+\s*шт", re.IGNORECASE)
FREE_DELIVERY = re.compile(r"бесплатн", re.IGNORECASE)
DELIVERY_COST = re.compile(r"(\d[\d \u00a0]{0,9})\s*(?:₽|руб)", re.IGNORECASE)
VLADIVOSTOK = re.compile(r"владивосток", re.IGNORECASE)

SECRET = re.compile(r"(?:sk-or-|sk-proj-|ghp_)[A-Za-z0-9_-]+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+7|\b8)[\s()-]*(?:\d[\s()-]*){10}\b")
DROP_KEYS = frozenset({"cookie", "cookies", "authorization", "password", "token", "api_key"})
MAX_TEXT = 6000
MAX_LIST = 100
MAX_KEYS = 200
MAX_DEPTH = 12

FULL_COST_BLOCKERS = (
    "price_applicability", "exact_physical_variant", "availability_confirmed",
    "delivery_to_Vladivostok", "delivery_cost", "mandatory_additional_costs",
)
CHECKS = {
    "VERIFY_TOTAL": "Уточнить применимую цену, наличие, доставку во Владивосток и все обязательные расходы.",
    "VERIFY_VARIANT": "Сверить точный MPN по фото или маркировке и фактическую модификацию.",
    "VERIFY_CONDITION_WARRANTY": "Уточнить состояние, историю обслуживания, комплект, гарантию и возможность проверки.",
}
CONFLICT_TEXT = {
    "NEW_VS_PRIOR_USE_CLAIM": (
        "Заявлено новое состояние, но описание упоминает установку, использование "
        "или обслуживание. Это повод уточнить, не доказанный обман."
    ),
}


def clean(value, depth=0):
    """Скрывает секреты и личные данные, ограничивает размер, убирает ключи с учётными данными."""
    if depth > MAX_DEPTH:
        raise AppError("UNSUPPORTED_FIELD_VALUE", detail="depth")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AppError("UNSUPPORTED_FIELD_VALUE", detail="number")
        return value
    if isinstance(value, str):
        text = SECRET.sub("[СЕКРЕТ СКРЫТ]", value)
        text = EMAIL.sub("[EMAIL СКРЫТ]", text)
        text = PHONE.sub("[ТЕЛЕФОН СКРЫТ]", text)
        return text[:MAX_TEXT]
    if isinstance(value, list):
        return [clean(item, depth + 1) for item in value[:MAX_LIST]]
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:MAX_KEYS]:
            name = str(key)[:100]
            if name.lower() in DROP_KEYS:
                continue
            out[name] = clean(item, depth + 1)
        return out
    raise AppError("UNSUPPORTED_FIELD_VALUE", detail=type(value).__name__)


def fingerprint(scope, observation):
    """Та же формула, что в DATA-01: повторный импорт того же файла не создаёт дубль."""
    text = json.dumps(observation, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256((scope + "\n" + text).encode("utf-8")).hexdigest()


def canonical_source(value):
    text = str(value or "").strip().upper()
    return SOURCE_ALIASES.get(text, text)


def _split(url):
    if not isinstance(url, str) or not url.strip() or len(url) > 2000:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    return parts, port


def card_identity(source, listing_id, url):
    split = _split(url)
    if source not in HOSTS or split is None:
        raise AppError("PUBLIC_SOURCE_URL_REQUIRED")
    parts, port = split
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or parts.username or parts.password
            or port not in (None, 443) or host not in HOSTS[source]):
        raise AppError("PUBLIC_SOURCE_URL_REQUIRED")
    if PRIVATE_PATH.search(parts.path):
        raise AppError("PRIVATE_PATH_BLOCKED")
    match = ID_PATTERNS[source].search(parts.path)
    found = next((group for group in match.groups() if group), None) if match else None
    if not found or str(listing_id) != found:
        raise AppError("CARD_IDENTITY_NOT_CONFIRMED")
    return "https://" + host + parts.path


def identity_from_url(url):
    """Определяет источник и номер карточки по ссылке. Для неизвестных сайтов None."""
    split = _split(url)
    if split is None:
        return None
    parts, _ = split
    host = (parts.hostname or "").lower()
    for source, hosts in HOSTS.items():
        if host in hosts:
            match = ID_PATTERNS[source].search(parts.path)
            found = next((group for group in match.groups() if group), None) if match else None
            if found:
                return source, found, card_identity(source, found, url)
    return None


def plain_url(url):
    """Проверка ссылки для ручного товара. Ядро её никогда не открывает."""
    split = _split(url)
    if split is None:
        raise AppError("INVALID_INPUT", detail={"field": "url", "reason": "url_required"})
    parts, _ = split
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise AppError("INVALID_INPUT", detail={"field": "url", "reason": "http_url_required"})
    return parts.geturl()


def blank_field():
    return {"state": "UNKNOWN", "value": None, "method": None, "evidence": None, "independently_verified": False}


def _known(fields, key):
    for name in ALIASES.get(key, (key,)):
        item = fields.get(name)
        if (isinstance(item, dict) and item.get("status") == "KNOWN" and item.get("evidence")
                and item.get("value") not in (None, "", [], {})):
            return item
    return None


def _state(key, item, method=None):
    if item is None:
        return blank_field()
    evidence = item.get("evidence")
    origin = evidence.get("method") if isinstance(evidence, dict) else None
    return {
        "state": "SELLER_CLAIM" if key in SELLER_FIELDS else "OBSERVED",
        "value": item.get("value"),
        "method": method or origin or "EYE_FIELD",
        "evidence": evidence,
        "independently_verified": False,
    }


def resolve_role(source_role, label):
    role = ROLE_MAP.get(source_role, "unknown")
    if role != "unknown":
        return role, "SOURCE_ROLE"
    for pattern, candidate in LABEL_ROLE_RULES:
        if pattern.search(label or ""):
            return candidate, "LOCAL_LABEL_RULE"
    return "unknown", "UNRESOLVED"


def _prices(observation, fields):
    out, seen = [], set()
    candidates = observation.get("price_candidates")
    for candidate in (candidates if isinstance(candidates, list) else [])[:30]:
        if not isinstance(candidate, dict):
            continue
        amount = parse_amount(candidate.get("amount"))
        if amount is None and candidate.get("amount_minor") is not None:
            try:
                minor = int(candidate["amount_minor"])
                amount = parse_amount(f"{minor // 100}.{minor % 100:02d}")
            except (ValueError, TypeError):
                amount = None
        currency = candidate.get("currency")
        if amount is None or currency not in CURRENCIES:
            continue
        source_role = str(candidate.get("source_role") or candidate.get("role")
                          or candidate.get("condition_type") or "unknown").strip().lower()[:40]
        label = str(candidate.get("visible_label") or candidate.get("conditions") or "")[:300]
        role, origin = resolve_role(source_role, label)
        key = (role, to_minor(amount), currency)
        if key in seen:
            continue
        seen.add(key)
        evidence = candidate.get("evidence")
        out.append({
            "role": role,
            "role_origin": origin,
            "source_role": source_role,
            "amount_minor": key[1],
            "currency": currency,
            "label": label or None,
            "variant_association": str(candidate.get("variant_association") or "UNKNOWN")[:40],
            "evidence": evidence if isinstance(evidence, dict) else None,
            "applicability_verified": False,
        })
    if out:
        return out
    item = _known(fields, "displayed_price")
    currency_item = _known(fields, "currency")
    if item is None or currency_item is None:
        return out
    amount = parse_amount(item.get("value"))
    currency = currency_item.get("value")
    if amount is None or currency not in CURRENCIES:
        return out
    role_item = _known(fields, "price_role")
    source_role = str(role_item.get("value")).strip().lower()[:40] if role_item else "unknown"
    role, _ = resolve_role(source_role, str(item.get("value")))
    out.append({
        "role": role,
        "role_origin": "DISPLAYED_PRICE_FIELD",
        "source_role": source_role,
        "amount_minor": to_minor(amount),
        "currency": currency,
        "label": str(item.get("value"))[:300],
        "variant_association": "UNKNOWN",
        "evidence": item.get("evidence") if isinstance(item.get("evidence"), dict) else None,
        "applicability_verified": False,
    })
    return out


def _characteristics(fields):
    item = fields.get("characteristics")
    if not isinstance(item, dict) or item.get("status") != "KNOWN" or not isinstance(item.get("value"), list):
        return []
    out = []
    for row in item["value"][:MAX_LIST]:
        if not isinstance(row, dict):
            continue
        label, value = row.get("label"), row.get("value")
        if not isinstance(label, str) or not label.strip() or value in (None, "", [], {}):
            continue
        out.append({"label": label.strip()[:200], "value": value, "evidence": row.get("evidence") or item.get("evidence")})
    return out


def _description(fields):
    item = _known(fields, "description")
    if item is None or not isinstance(item.get("value"), str):
        return [], ""
    text = item["value"]
    hints = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        for key, pattern in DESCRIPTION_RULES:
            if pattern.search(sentence):
                hints.append({"field": key, "label": FIELDS[key], "text": sentence[:400], "state": "SELLER_CLAIM"})
                break
        if len(hints) >= 5:
            break
    return hints, text


def availability_from(value):
    if isinstance(value, bool):
        return "IN_STOCK" if value else "OUT_OF_STOCK"
    if isinstance(value, str):
        v = value.strip()
        if v in ("IN_STOCK", "OUT_OF_STOCK"):
            return v
        if OUT_OF_STOCK.search(v):
            return "OUT_OF_STOCK"
        if IN_STOCK.search(v):
            return "IN_STOCK"
    return "UNKNOWN"


def condition_from(value):
    code = "UNKNOWN"
    if isinstance(value, str):
        code = CONDITION_MAP.get(value.strip().lower().rstrip("."), "UNKNOWN")
    return {"code": code, "origin": "LOCAL_LITERAL_MAPPING" if code != "UNKNOWN" else None, "independently_verified": False}


def _delivery(state):
    value = state.get("value")
    if isinstance(value, str):
        text = value
    elif value is None:
        text = ""
    else:
        text = json.dumps(value, ensure_ascii=False)
    cost_minor, cost_state = None, "UNKNOWN"
    if text and FREE_DELIVERY.search(text):
        cost_minor, cost_state = 0, "SELLER_CLAIM"
    elif text:
        match = DELIVERY_COST.search(text)
        amount = parse_amount(match.group(1)) if match else None
        if amount is not None:
            cost_minor, cost_state = to_minor(amount), "SELLER_CLAIM"
    return {
        "requested_destination": "Владивосток",
        "destination_mentioned": bool(text and VLADIVOSTOK.search(text)),
        "destination_confirmed": False,
        "terms": value,
        "terms_state": state.get("state", "UNKNOWN"),
        "cost_minor": cost_minor,
        "cost_state": cost_state,
    }


def _short(value, limit=200):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit]


def _completeness(fields):
    known = [key for key, item in fields.items() if item["state"] != "UNKNOWN"]
    return {
        "total": len(FIELDS),
        "known": len(known),
        "unknown": [FIELDS[key] for key in FIELDS if key not in known],
        "independently_verified": 0,
    }


def normalize_eye(observation):
    """Возвращает (normalized, cleaned). cleaned хранится как исходное наблюдение."""
    if not isinstance(observation, dict):
        raise AppError("EXPORT_FORMAT_UNSUPPORTED")
    media = normalize_media(observation.get("media"))
    obs = clean({k:v for k,v in observation.items() if k != "media"})
    if media:
        obs["media"] = media
    source = canonical_source(obs.get("source"))
    listing_id = str(obs.get("id") or obs.get("listing_id") or "").strip()
    url = card_identity(source, listing_id, obs.get("url"))
    observed_at = iso(parse_aware(obs.get("observed_at"), "OBSERVATION_TIME_REQUIRED"))
    raw_fields = obs.get("fields") if isinstance(obs.get("fields"), dict) else {}
    title_item = _known(raw_fields, "title")
    if title_item is None or not isinstance(title_item.get("value"), str) or not title_item["value"].strip():
        raise AppError("TITLE_WITH_EVIDENCE_REQUIRED")
    fields = {key: _state(key, _known(raw_fields, key)) for key in FIELDS}
    characteristics = _characteristics(raw_fields)
    for row in characteristics:
        label = row["label"].lower()
        for key, pattern in CHARACTERISTIC_RULES:
            if pattern.fullmatch(label) and fields[key]["state"] == "UNKNOWN":
                fields[key] = {
                    "state": "SELLER_CLAIM" if key in SELLER_FIELDS else "OBSERVED",
                    "value": row["value"],
                    "method": "CHARACTERISTIC_LABEL_RULE",
                    "evidence": row["evidence"],
                    "independently_verified": False,
                }
                break
    hints, description = _description(raw_fields)
    prices = _prices(obs, raw_fields)
    if prices:
        fields["price_conditions"] = {
            "state": "OBSERVED",
            "value": [item["label"] or format_minor(item["amount_minor"], item["currency"]) for item in prices],
            "method": "PRICE_CANDIDATES",
            "evidence": prices[0]["evidence"],
            "independently_verified": False,
        }
    condition = condition_from(fields["condition"]["value"])
    conflicts = []
    claims = " ".join([description] + [row["value"] for row in characteristics if isinstance(row["value"], str)])
    if condition["code"] == "NEW" and PRIOR_USE.search(claims):
        conflicts.append({"code": "NEW_VS_PRIOR_USE_CLAIM", "text": CONFLICT_TEXT["NEW_VS_PRIOR_USE_CLAIM"]})
    availability = "UNKNOWN"
    if fields["availability"]["state"] != "UNKNOWN":
        availability = availability_from(fields["availability"]["value"])
    checks = ["VERIFY_TOTAL", "VERIFY_VARIANT"]
    if condition["code"] != "NEW" or conflicts or fields["warranty"]["state"] == "UNKNOWN":
        checks.append("VERIFY_CONDITION_WARRANTY")
    brand_item = _known(raw_fields, "brand")
    region_item = _known(raw_fields, "region")
    page_region = None
    if region_item is not None and source in ("WB", "OZON"):
        page_region = {"value": _short(region_item.get("value")), "meaning": "PAGE_REGION_CONTEXT_NOT_SELLER_LOCATION"}
    normalized = {
        "media": obs.get("media"),
        "category_path": _short(obs.get("category_path"), 400),
        "version": VERSION,
        "scope": EYE_SCOPE,
        "source": source,
        "listing_id": listing_id,
        "url": url,
        "observed_at": observed_at,
        "title": title_item["value"].strip()[:500],
        "brand": _short(brand_item.get("value")) if brand_item else None,
        "model": _short(fields["model"]["value"]) if fields["model"]["state"] != "UNKNOWN" else None,
        "fields": fields,
        "prices": prices,
        "characteristics": characteristics,
        "description_hints": hints,
        "condition": condition,
        "availability": availability,
        "delivery": _delivery(fields["delivery"]),
        "page_region": page_region,
        "conflicts": conflicts,
        "checks": [{"code": code, "text": CHECKS[code]} for code in checks],
        "full_cost_blockers": list(FULL_COST_BLOCKERS),
        "completeness": _completeness(fields),
        "eye": {
            "status": _short(obs.get("status"), 60),
            "evidence_level": _short(obs.get("evidence_level"), 80),
            "http_status": obs.get("http_status") if isinstance(obs.get("http_status"), int) else None,
        },
    }
    return normalized, obs


def manual_normalized(product, amount_minor, currency, role, availability, note, observed_at):
    """Наблюдение, введённое человеком. Помечается USER_ENTERED, доказательства нет."""
    fields = {key: blank_field() for key in FIELDS}
    fields["title"] = {"state": "OBSERVED", "value": product["title"], "method": "PRODUCT_RECORD",
                       "evidence": None, "independently_verified": False}
    price_text = format_minor(amount_minor, currency)
    fields["displayed_price"] = {"state": "USER_ENTERED", "value": price_text, "method": "MANUAL_ENTRY",
                                 "evidence": {"note": note} if note else None, "independently_verified": False}
    if availability != "UNKNOWN":
        fields["availability"] = {
            "state": "USER_ENTERED",
            "value": "в наличии" if availability == "IN_STOCK" else "нет в наличии",
            "method": "MANUAL_ENTRY",
            "evidence": None,
            "independently_verified": False,
        }
    prices = [{
        "role": role, "role_origin": "USER_ENTERED", "source_role": role, "amount_minor": amount_minor,
        "currency": currency, "label": note or None, "variant_association": "UNKNOWN", "evidence": None,
        "applicability_verified": False,
    }]
    return {
        "version": VERSION,
        "scope": "MANUAL_ENTRY",
        "source": product["source"],
        "listing_id": product["listing_id"],
        "url": product.get("url"),
        "observed_at": observed_at,
        "title": product["title"],
        "brand": product.get("brand"),
        "model": product.get("model"),
        "fields": fields,
        "prices": prices,
        "characteristics": [],
        "description_hints": [],
        "condition": condition_from(None),
        "availability": availability,
        "delivery": _delivery(blank_field()),
        "page_region": None,
        "conflicts": [],
        "checks": [{"code": "VERIFY_TOTAL", "text": CHECKS["VERIFY_TOTAL"]}],
        "full_cost_blockers": list(FULL_COST_BLOCKERS),
        "completeness": _completeness(fields),
        "note": note,
    }