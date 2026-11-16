"""diag_v7l — валидация observation, пагинация, структура для intake.
Стратегия: принять IT/EUR как source truth, проверить качество данных.
Минимум 3 запроса (RU p1, RU p2, COM p1) чтобы избежать rate-limit.
"""
import sys, os, time, re, json, logging
from urllib.parse import quote, unquote as uq
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7l")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def fetch_wholesale(domain, query, page, locale_set="ru"):
    """Fetch wholesale page. Returns (html, resp, latency)."""
    from curl_cffi import requests as cffi_req
    if locale_set == "ru":
        url = (f"https://ru.aliexpress.com/wholesale?SearchText={quote(query)}"
               f"&page={page}&_lang=ru_RU&_currency=RUB&gatewayAdapt=glo2rus")
        hdrs = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://ru.aliexpress.com/",
            "Upgrade-Insecure-Requests": "1",
        }
        cookies = {
            "aep_usuc_f": "f_locale=ru-RU&f_region=RU&f_currency=RUB&ais=ru-RU&fre=0&c_b_locale=ru_RU",
            "aep_usuc": "f_region=RU&f_locale=ru-RU&f_currency=RUB",
        }
    else:
        url = (f"https://www.aliexpress.com/wholesale?SearchText={quote(query)}"
               f"&page={page}&_lang=en_US&_currency=USD&gatewayAdapt=glo2usa")
        hdrs = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.aliexpress.com/",
            "Upgrade-Insecure-Requests": "1",
        }
        cookies = {
            "aep_usuc_f": "f_locale=en-US&f_region=US&f_currency=USD&ais=en-US&fre=0&c_b_locale=en_US",
            "aep_usuc": "f_region=US&f_locale=en-US&f_currency=USD",
        }

    t0 = time.monotonic()
    resp = cffi_req.get(url, impersonate="chrome", timeout=(15, 30), headers=hdrs, cookies=cookies)
    lat = time.monotonic() - t0
    return resp.text, resp, lat


def parse_cards(html):
    """Minimal V20 parser: extract item_id, title, price, currency from card area."""
    products = []
    card_idx = html.find('id="card-list"')
    if card_idx < 0:
        return products
    window_end = min(card_idx + 700000, len(html))
    card_area = html[card_idx:window_end]

    anchor_pat = re.compile(
        r'<a[^>]*class="[^"]*search-card-item[^"]*"[^>]*href="([^"]+)"[^>]*>'
    )
    matches = list(anchor_pat.finditer(card_area))
    if not matches:
        anchor_pat2 = re.compile(r'<a[^>]*href="([^"]*/item/\d+\.html[^"]*)"[^>]*>')
        matches = list(anchor_pat2.finditer(card_area))

    for i, m in enumerate(matches):
        href = m.group(1)
        block_start = m.start()
        block_end = matches[i+1].start() if i+1 < len(matches) else min(block_start + 7000, len(card_area))
        card_html = card_area[block_start:block_end]

        p = {"item_id": None, "title": None, "price": None, "currency": None, "url": None}

        # ID
        id_m = re.search(r'/item/(\d+)\.html', href)
        if id_m:
            p["item_id"] = id_m.group(1)

        # URL
        if href.startswith("//"):
            p["url"] = "https:" + href
        else:
            p["url"] = href
        p["url"] = re.sub(r'\?.*$', '', p["url"])

        # Title
        t_m = re.search(r'<h3[^>]*>(.*?)</h3>', card_html, re.DOTALL)
        if t_m:
            p["title"] = re.sub(r'<[^>]+>', '', t_m.group(1)).strip()

        # Price from pdp_npi
        decoded = uq(href)
        pdp_m = re.search(r'pdp_npi=([^&]+)', decoded)
        if pdp_m:
            parts = pdp_m.group(1).split("!")
            if len(parts) >= 4:
                if re.match(r'^[A-Z]{3}$', parts[1]):
                    p["currency"] = parts[1]
                try:
                    p["price"] = float(parts[3])
                except (ValueError, IndexError):
                    pass

        products.append(p)

    # Dedup
    seen = set()
    unique = []
    for p in products:
        key = p.get("item_id") or p.get("url")
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def build_observation(prod, source, page):
    """Build a JARVIS_BR02_COLLECTOR_AUTONOMOUS observation from parsed product."""
    now = datetime.now(timezone(timedelta(hours=10))).isoformat()
    obs = {
        "fields": {},
        "price_candidates": [],
        "media": {"items": []},
        "availability": "IN_STOCK",
        "category_path": None,
    }

    if prod.get("title"):
        obs["fields"]["title"] = {
            "status": "KNOWN",
            "value": prod["title"][:300],
            "method": "WHOLESALE_HTML",
            "evidence": {"method": "WHOLESALE_HTML", "source": source},
        }

    if prod.get("price") and prod["price"] > 0:
        amount = float(prod["price"])
        currency = prod.get("currency", "EUR")
        obs["price_candidates"].append({
            "role": "current",
            "amount": f"{amount:.2f}",
            "amount_minor": int(round(amount * 100)),
            "currency": currency,
            "visible_label": f"{amount:,.2f} {currency}",
        })

    # Source metadata (NEW: tracks geo-locale reality)
    obs["source_locale"] = "it_IT"  # confirmed by diag_v7j/v7k
    obs["source_currency"] = prod.get("currency", "EUR")
    obs["source_page"] = page

    result = {
        "format": "JARVIS_BR02_COLLECTOR_AUTONOMOUS",
        "capture_action": "COLLECTOR_RUN",
        "fully_autonomous": True,
        "collector_source": source,
        "observation": {
            **obs,
            "source": source,
            "id": str(prod.get("item_id") or ""),
            "url": prod.get("url", ""),
            "observed_at": now,
            "status": "PARTIAL_SUCCESS",
            "product_content": True,
            "evidence_level": "COLLECTOR_AUTONOMOUS_HTML",
            "http_status": 200,
        },
    }
    return result


def validate_observation(obs, label):
    """Check observation structure for intake compatibility."""
    o = obs["observation"]
    issues = []

    # Required fields
    if not o.get("id"):
        issues.append("missing id")
    if not o.get("url"):
        issues.append("missing url")
    if not o.get("observed_at"):
        issues.append("missing observed_at")

    # Title
    title = o.get("fields", {}).get("title", {})
    if title.get("status") != "KNOWN" or not title.get("value"):
        issues.append("title not KNOWN")
    elif len(title["value"]) < 10:
        issues.append(f"title too short: {len(title['value'])}")

    # Price
    pc = o.get("price_candidates", [])
    if not pc:
        issues.append("no price_candidates")
    else:
        cur = pc[0]
        if not cur.get("amount") or not cur.get("currency"):
            issues.append("price missing amount/currency")
        if not re.match(r'^\d+\.\d{2}$', cur.get("amount", "")):
            issues.append(f"amount format: {cur.get('amount')}")
        if not re.match(r'^[A-Z]{3}$', cur.get("currency", "")):
            issues.append(f"currency format: {cur.get('currency')}")
        if cur.get("amount_minor") is None:
            issues.append("missing amount_minor")

    # Evidence
    ev = o.get("evidence_level", "")
    if not ev.startswith("COLLECTOR_AUTONOMOUS"):
        issues.append(f"evidence_level: {ev}")

    # Format
    if obs.get("format") != "JARVIS_BR02_COLLECTOR_AUTONOMOUS":
        issues.append("format mismatch")

    status = "OK" if not issues else f"ISSUES: {issues}"
    return status


# ─── Main ───
def main():
    print(f"=== diag_v7l — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    all_ids_p1 = set()
    all_ids_p2 = set()

    # ── [1] RU Page 1 ──
    log.info("── [1] RU wholesale page 1 ──")
    html1, resp1, lat1 = fetch_wholesale("ru", "наушники", 1, "ru")
    log.info("  HTTP %d, %dB, %.1fs", resp1.status_code, len(html1), lat1)

    prods1 = parse_cards(html1)
    log.info("  Parsed: %d products", len(prods1))

    # Validate first 5
    for i, p in enumerate(prods1[:5]):
        obs = build_observation(p, "ALIEXPRESS_RU", 1)
        status = validate_observation(obs, f"RU-p1-{i}")
        log.info("  [%d] id=%s price=%s %s | %s",
                 i, p.get("item_id"), p.get("price"), p.get("currency"), status)
        if i == 0:
            # Print full observation JSON for structure check
            log.info("  FULL OBS JSON (first 800 chars):")
            j = json.dumps(obs, ensure_ascii=False, indent=2)
            for line in j[:800].split("\n"):
                log.info("    %s", line)

    all_ids_p1 = {p["item_id"] for p in prods1 if p.get("item_id")}
    log.info("  Page 1 IDs: %d unique", len(all_ids_p1))

    time.sleep(2)

    # ── [2] RU Page 2 ──
    log.info("── [2] RU wholesale page 2 (pagination) ──")
    html2, resp2, lat2 = fetch_wholesale("ru", "наушники", 2, "ru")
    log.info("  HTTP %d, %dB, %.1fs", resp2.status_code, len(html2), lat2)

    prods2 = parse_cards(html2)
    log.info("  Parsed: %d products", len(prods2))

    for i, p in enumerate(prods2[:3]):
        log.info("  [%d] id=%s price=%s %s title=%s",
                 i, p.get("item_id"), p.get("price"), p.get("currency"),
                 (p.get("title") or "?")[:60])

    all_ids_p2 = {p["item_id"] for p in prods2 if p.get("item_id")}
    log.info("  Page 2 IDs: %d unique", len(all_ids_p2))

    overlap = all_ids_p1 & all_ids_p2
    log.info("  Overlap p1∩p2: %d IDs", len(overlap))
    if overlap:
        log.info("  Overlapping IDs: %s", list(overlap)[:5])
    if len(all_ids_p2) == 0:
        log.warning("  ⚠ Page 2 empty — pagination may not work or same page returned")
    elif len(overlap) == len(all_ids_p2):
        log.warning("  ⚠ ALL page 2 IDs overlap with page 1 — server may ignore page param")
    else:
        log.info("  ✓ Pagination working: %d new IDs on page 2", len(all_ids_p2 - all_ids_p1))

    time.sleep(2)

    # ── [3] COM Page 1 ──
    log.info("── [3] COM wholesale page 1 ──")
    html3, resp3, lat3 = fetch_wholesale("com", "headphones", 1, "com")
    log.info("  HTTP %d, %dB, %.1fs", resp3.status_code, len(html3), lat3)

    prods3 = parse_cards(html3)
    log.info("  Parsed: %d products", len(prods3))

    for i, p in enumerate(prods3[:5]):
        obs = build_observation(p, "ALIEXPRESS", 1)
        status = validate_observation(obs, f"COM-p1-{i}")
        log.info("  [%d] id=%s price=%s %s | %s",
                 i, p.get("item_id"), p.get("price"), p.get("currency"), status)

    # ── [4] Summary ──
    log.info("── [4] Summary ──")
    total_r = len(prods1) + len(prods2)
    total_c = len(prods3)
    log.info("  RU: %d (p1) + %d (p2) = %d products", len(prods1), len(prods2), total_r)
    log.info("  COM: %d products", total_c)

    # Currency distribution
    ru_currencies = {}
    for p in prods1 + prods2:
        c = p.get("currency", "?")
        ru_currencies[c] = ru_currencies.get(c, 0) + 1
    log.info("  RU currency distribution: %s", ru_currencies)

    com_currencies = {}
    for p in prods3:
        c = p.get("currency", "?")
        com_currencies[c] = com_currencies.get(c, 0) + 1
    log.info("  COM currency distribution: %s", com_currencies)

    # Price range
    ru_prices = [p["price"] for p in prods1 + prods2 if p.get("price") and p["price"] > 0]
    if ru_prices:
        log.info("  RU price range: %.2f – %.2f (n=%d)", min(ru_prices), max(ru_prices), len(ru_prices))

    com_prices = [p["price"] for p in prods3 if p.get("price") and p["price"] > 0]
    if com_prices:
        log.info("  COM price range: %.2f – %.2f (n=%d)", min(com_prices), max(com_prices), len(com_prices))

    # Title language check
    it_words = re.compile(r'\b(Cuffie|Auricolari|per|con|del|della|in|di|e|la|il|wireless|originali|versione)\b', re.I)
    en_words = re.compile(r'\b(headphone|earphone|for|with|the|and|in|of|to|a|wireless|original)\b', re.I)

    ru_it = sum(1 for p in prods1 + prods2 if p.get("title") and it_words.search(p["title"]))
    ru_en = sum(1 for p in prods1 + prods2 if p.get("title") and en_words.search(p["title"]))
    com_it = sum(1 for p in prods3 if p.get("title") and it_words.search(p["title"]))
    com_en = sum(1 for p in prods3 if p.get("title") and en_words.search(p["title"]))

    log.info("  RU titles: IT-markers=%d, EN-markers=%d (of %d)", ru_it, ru_en, total_r)
    log.info("  COM titles: IT-markers=%d, EN-markers=%d (of %d)", com_it, com_en, total_c)

    # ── [5] Intake compatibility check ──
    log.info("── [5] Intake compatibility ──")
    # Check that observation has all fields intake expects
    sample = build_observation(prods1[0], "ALIEXPRESS_RU", 1) if prods1 else None
    if sample:
        o = sample["observation"]
        required_keys = ["source", "id", "url", "observed_at", "status",
                         "product_content", "evidence_level", "http_status",
                         "fields", "price_candidates", "media", "availability"]
        missing = [k for k in required_keys if k not in o]
        log.info("  Required keys present: %d/%d", len(required_keys) - len(missing), len(required_keys))
        if missing:
            log.warning("  Missing: %s", missing)

        # Check _known() compatibility: status==KNOWN AND evidence AND value
        for field_name, field_val in o.get("fields", {}).items():
            is_known = (field_val.get("status") == "KNOWN"
                        and field_val.get("evidence")
                        and field_val.get("value"))
            log.info("  field '%s': _known=%s", field_name, is_known)

        # price_candidates structure
        for pc in o.get("price_candidates", []):
            log.info("  price_candidate: role=%s amount=%s currency=%s minor=%s",
                     pc.get("role"), pc.get("amount"), pc.get("currency"), pc.get("amount_minor"))

    print("=== DONE ===")
    log.info("diag_v7l complete")


if __name__ == "__main__":
    main()
