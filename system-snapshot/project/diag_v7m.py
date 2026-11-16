"""diag_v7m — пагинация + валидация observation (минимум запросов).
Стратегия: 60s cooldown → RU p1 → 5s → RU p2 → 5s → COM p1.
Максимум 3 HTTP-запроса к AliExpress.
"""
import sys, os, time, re, json, logging
from urllib.parse import quote, unquote as uq
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diag_v7m")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def fetch_wholesale(query, page, locale_set="ru"):
    """Single wholesale fetch. Returns (html, status, latency)."""
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
    return resp.text, resp.status_code, lat


def parse_cards(html):
    """V20 parser: item_id, title, price, currency, url, images."""
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

        p = {"item_id": None, "title": None, "price": None, "currency": None,
             "original_price": None, "url": None, "images": []}

        # ID
        id_m = re.search(r'/item/(\d+)\.html', href)
        if id_m:
            p["item_id"] = id_m.group(1)
        if not p["item_id"]:
            id_m2 = re.search(r'(\d{12,})', href)
            if id_m2:
                p["item_id"] = id_m2.group(1)

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
                    p["original_price"] = float(parts[2])
                except (ValueError, IndexError):
                    pass
                try:
                    p["price"] = float(parts[3])
                except (ValueError, IndexError):
                    pass
                if p["price"] and p["original_price"] and p["price"] >= p["original_price"]:
                    p["price"], p["original_price"] = p["original_price"], p["price"]

        # Images (first 3)
        img_pat = re.compile(
            r'<img[^>]*class="[^"]*(?:us--img|product-img|images--item)[^"]*"[^>]*src="([^"]+)"', re.I
        )
        for im in img_pat.finditer(card_html):
            img_url = im.group(1)
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            if img_url not in p["images"] and len(p["images"]) < 3:
                p["images"].append(img_url)

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
    """Build JARVIS_BR02_COLLECTOR_AUTONOMOUS observation."""
    now = datetime.now(timezone(timedelta(hours=10))).isoformat()
    obs = {"fields": {}, "price_candidates": [], "media": {"items": []},
           "availability": "IN_STOCK", "category_path": None}

    if prod.get("title"):
        obs["fields"]["title"] = {
            "status": "KNOWN", "value": prod["title"][:300],
            "method": "WHOLESALE_HTML",
            "evidence": {"method": "WHOLESALE_HTML", "source": source},
        }

    if prod.get("price") and prod["price"] > 0:
        amount = float(prod["price"])
        currency = prod.get("currency", "EUR")
        obs["price_candidates"].append({
            "role": "current", "amount": f"{amount:.2f}",
            "amount_minor": int(round(amount * 100)),
            "currency": currency,
            "visible_label": f"{amount:,.2f} {currency}",
        })
        if prod.get("original_price") and prod["original_price"] > 0:
            obs["price_candidates"].append({
                "role": "original", "amount": f"{prod['original_price']:.2f}",
                "amount_minor": int(round(prod["original_price"] * 100)),
                "currency": currency,
                "visible_label": f"{prod['original_price']:,.2f} {currency}",
            })

    for img in prod.get("images", [])[:20]:
        obs["media"]["items"].append({"type": "image", "source_url": img, "image_data": None})

    return {
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


def validate_obs(obs):
    """Return list of issues (empty = OK)."""
    o = obs["observation"]
    issues = []
    if not o.get("id"):
        issues.append("no id")
    if not o.get("url"):
        issues.append("no url")
    if not o.get("observed_at"):
        issues.append("no observed_at")
    title = o.get("fields", {}).get("title", {})
    if title.get("status") != "KNOWN" or not title.get("value"):
        issues.append("title not KNOWN")
    elif len(title["value"]) < 10:
        issues.append(f"title short ({len(title['value'])})")
    pc = o.get("price_candidates", [])
    if not pc:
        issues.append("no price")
    else:
        c = pc[0]
        if not re.match(r'^\d+\.\d{2}$', c.get("amount", "")):
            issues.append(f"amount fmt: {c.get('amount')}")
        if not re.match(r'^[A-Z]{3}$', c.get("currency", "")):
            issues.append(f"cur fmt: {c.get('currency')}")
        if c.get("amount_minor") is None:
            issues.append("no minor")
    if obs.get("format") != "JARVIS_BR02_COLLECTOR_AUTONOMOUS":
        issues.append("format")
    return issues


def main():
    print(f"=== diag_v7m — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")

    # ── Cooldown ──
    log.info("Cooldown 60s...")
    time.sleep(60)

    # ── [1] RU Page 1 ──
    log.info("── [1] RU p1 ──")
    html1, st1, lat1 = fetch_wholesale("наушники", 1, "ru")
    log.info("  HTTP %d, %dB, %.1fs", st1, len(html1), lat1)

    if len(html1) < 50000:
        log.warning("  ⚠ Small response (%dB) — likely challenge. Aborting.", len(html1))
        log.info("  First 300: %s", html1[:300])
        print("=== ABORT (rate limit) ===")
        return

    prods1 = parse_cards(html1)
    log.info("  Parsed: %d products", len(prods1))

    # Validate first 3
    for i in range(min(3, len(prods1))):
        obs = build_observation(prods1[i], "ALIEXPRESS_RU", 1)
        issues = validate_obs(obs)
        p = prods1[i]
        log.info("  [%d] id=%s | %s %s | title=%s | %s",
                 i, p.get("item_id"), p.get("price"), p.get("currency"),
                 (p.get("title") or "?")[:50], "OK" if not issues else issues)

    # Full JSON dump of first observation
    if prods1:
        obs0 = build_observation(prods1[0], "ALIEXPRESS_RU", 1)
        j = json.dumps(obs0, ensure_ascii=False, indent=2)
        log.info("  FULL OBS[0] (%d chars):", len(j))
        for line in j.split("\n")[:40]:
            log.info("    %s", line)

    ids1 = {p["item_id"] for p in prods1 if p.get("item_id")}
    log.info("  P1 unique IDs: %d", len(ids1))

    time.sleep(5)

    # ── [2] RU Page 2 ──
    log.info("── [2] RU p2 (pagination) ──")
    html2, st2, lat2 = fetch_wholesale("наушники", 2, "ru")
    log.info("  HTTP %d, %dB, %.1fs", st2, len(html2), lat2)

    if len(html2) < 50000:
        log.warning("  ⚠ P2 small (%dB) — challenge", len(html2))
        prods2 = []
    else:
        prods2 = parse_cards(html2)
        log.info("  Parsed: %d products", len(prods2))
        for i in range(min(3, len(prods2))):
            p = prods2[i]
            log.info("  [%d] id=%s | %s %s | %s",
                     i, p.get("item_id"), p.get("price"), p.get("currency"),
                     (p.get("title") or "?")[:50])

    ids2 = {p["item_id"] for p in prods2 if p.get("item_id")}
    log.info("  P2 unique IDs: %d", len(ids2))
    overlap = ids1 & ids2
    log.info("  Overlap P1∩P2: %d", len(overlap))
    if len(ids2) == 0:
        log.warning("  ⚠ P2 empty")
    elif len(overlap) == len(ids2) and len(ids2) > 0:
        log.warning("  ⚠ ALL P2 IDs in P1 — server ignores page param")
    else:
        new_ids = ids2 - ids1
        log.info("  ✓ %d new IDs on P2", len(new_ids))

    time.sleep(5)

    # ── [3] COM Page 1 ──
    log.info("── [3] COM p1 ──")
    html3, st3, lat3 = fetch_wholesale("headphones", 1, "com")
    log.info("  HTTP %d, %dB, %.1fs", st3, len(html3), lat3)

    if len(html3) < 50000:
        log.warning("  ⚠ COM small (%dB) — challenge", len(html3))
        prods3 = []
    else:
        prods3 = parse_cards(html3)
        log.info("  Parsed: %d products", len(prods3))
        for i in range(min(3, len(prods3))):
            p = prods3[i]
            log.info("  [%d] id=%s | %s %s | %s",
                     i, p.get("item_id"), p.get("price"), p.get("currency"),
                     (p.get("title") or "?")[:50])

    # ── [4] Summary ──
    log.info("── [4] Summary ──")
    log.info("  RU: p1=%d, p2=%d, total=%d", len(prods1), len(prods2), len(prods1)+len(prods2))
    log.info("  COM: %d", len(prods3))

    # Currencies
    for label, prods in [("RU", prods1+prods2), ("COM", prods3)]:
        curs = {}
        for p in prods:
            c = p.get("currency") or "?"
            curs[c] = curs.get(c, 0) + 1
        log.info("  %s currencies: %s", label, curs)

    # Price ranges
    for label, prods in [("RU", prods1+prods2), ("COM", prods3)]:
        prices = [p["price"] for p in prods if p.get("price") and p["price"] > 0]
        if prices:
            log.info("  %s price: %.2f – %.2f (n=%d)", label, min(prices), max(prices), len(prices))

    # Title language
    it_re = re.compile(r'\b(Cuffie|Auricolari|per|con|del|della|wireless|originali|versione)\b', re.I)
    en_re = re.compile(r'\b(headphone|earphone|for|with|the|and|wireless|original)\b', re.I)
    for label, prods in [("RU", prods1+prods2), ("COM", prods3)]:
        it = sum(1 for p in prods if p.get("title") and it_re.search(p["title"]))
        en = sum(1 for p in prods if p.get("title") and en_re.search(p["title"]))
        log.info("  %s titles: IT=%d, EN=%d / %d", label, it, en, len(prods))

    # ── [5] Intake structure check ──
    log.info("── [5] Intake structure ──")
    if prods1:
        obs = build_observation(prods1[0], "ALIEXPRESS_RU", 1)
        o = obs["observation"]
        required = ["source", "id", "url", "observed_at", "status",
                    "product_content", "evidence_level", "http_status",
                    "fields", "price_candidates", "media", "availability"]
        missing = [k for k in required if k not in o]
        log.info("  Required keys: %d/%d present", len(required)-len(missing), len(required))
        if missing:
            log.warning("  Missing: %s", missing)

        # _known() check
        for fn, fv in o.get("fields", {}).items():
            known = (fv.get("status") == "KNOWN" and fv.get("evidence") and fv.get("value"))
            log.info("  field '%s': _known=%s", fn, known)

        # price_candidates detail
        for pc in o.get("price_candidates", []):
            log.info("  pc: role=%s amount=%s cur=%s minor=%s label=%s",
                     pc.get("role"), pc.get("amount"), pc.get("currency"),
                     pc.get("amount_minor"), pc.get("visible_label"))

        # media
        log.info("  media items: %d", len(o.get("media", {}).get("items", [])))

    print("=== DONE ===")
    log.info("diag_v7m complete")


if __name__ == "__main__":
    main()
