"""v2: FARPOST — парсит полный путь, AVITO — задержка + разные URL."""
import sys, os, re, time, json
from pathlib import Path
import requests

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
OUT = Path(__file__).resolve().parent / "find_ids_v2.log"

def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n"); f.flush(); os.fsync(f.fileno())

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8", "Accept": "text/html,application/xhtml+xml"}

def find_farpost():
    log("\n=== FARPOST ===")
    url = "https://www.farpost.ru/vladivostok/tech/communication/cellphones/"
    log(f"Fetch: {url}")
    r = requests.get(url, headers=H, timeout=(10, 30))
    log(f"HTTP {r.status_code}, {len(r.content)} bytes")
    html = r.text

    # Save first 5000 chars for inspection
    (Path(__file__).parent / "farpost_cat_sample.html").write_text(html[:5000], encoding="utf-8")

    # Try multiple patterns
    pats = [
        (r'href="(/[^"]*?/(\d{8,12})\.html)"', "full-path /.../ID.html"),
        (r'href="(/vladivostok/(\d{8,12})\.html)"', "/vladivostok/ID.html"),
        (r'href="(/(\d{8,12})\.html)"', "/ID.html"),
        (r'data-id="(\d{8,12})"', "data-id"),
        (r'item_id[=:]\s*(\d{8,12})', "item_id"),
        (r'"id"\s*:\s*"?(\d{8,12})"?', "json id"),
        (r'href="([^"]*?cellphones[^"]*?/(\d{8,12})[^"]*)"', "cellphones/ID"),
    ]
    best = []
    for pat, label in pats:
        m = re.findall(pat, html, re.I)
        # m could be tuples or strings
        ids = [x[1] if isinstance(x, tuple) else x for x in m]
        unique = list(dict.fromkeys(ids))
        log(f"  [{label}]: {len(unique)} IDs, first={unique[:5]}")
        if len(unique) > len(best):
            best = unique

    # Also: look for any href with 9-12 digit numbers
    all_hrefs = re.findall(r'href="([^"]+)"', html)
    id_hrefs = [h for h in all_hrefs if re.search(r'/\d{9,12}(\.html)?["/?]', h)]
    log(f"  [all hrefs with ID]: {len(id_hrefs)}, first={id_hrefs[:5]}")

    # Verify top candidates
    candidates = list(dict.fromkeys(best + [re.search(r'(\d{9,12})', h).group(1) for h in id_hrefs[:10] if re.search(r'(\d{9,12})', h)]))
    log(f"\n  Verifying top {min(5, len(candidates))} candidates:")
    alive = []
    for lid in candidates[:5]:
        lurl = f"https://www.farpost.ru/vladivostok/{lid}.html"
        try:
            lr = requests.get(lurl, headers=H, timeout=(5, 15))
            ok = lr.status_code == 200 and len(lr.content) > 10000
            log(f"    {lid} → HTTP {lr.status_code} ({len(lr.content)}B) {'ALIVE' if ok else 'DEAD'}")
            if ok:
                alive.append(lid)
        except Exception as e:
            log(f"    {lid} → ERR {e}")
        time.sleep(1)
    return alive

def find_avito():
    log("\n=== AVITO ===")
    # Try with longer delay and specific headers
    urls = [
        "https://www.avito.ru/moskva_i_mo/tech/phone/?f=AS",
        "https://www.avito.ru/all/telefony/?f=AS",
    ]
    for url in urls:
        log(f"Fetch: {url}")
        try:
            time.sleep(3)
            r = requests.get(url, headers=H, timeout=(10, 30))
            log(f"HTTP {r.status_code}, {len(r.content)} bytes")
            if r.status_code != 200:
                continue
            html = r.text
            # Avito: /ad/ID or /telefony/slug_ID
            ids = re.findall(r'/ad/(\d{9,12})', html)
            ids2 = re.findall(r'_(\d{9,12})["/?&]', html)
            all_ids = list(dict.fromkeys(ids + ids2))
            log(f"  IDs found: {len(all_ids)}, first={all_ids[:8]}")
            if all_ids:
                alive = []
                for lid in all_ids[:5]:
                    lurl = f"https://www.avito.ru/ad/{lid}"
                    try:
                        time.sleep(2)
                        lr = requests.get(lurl, headers=H, timeout=(5, 15))
                        ok = lr.status_code == 200 and len(lr.content) > 10000
                        log(f"    {lid} → HTTP {lr.status_code} ({len(lr.content)}B) {'ALIVE' if ok else 'DEAD'}")
                        if ok:
                            alive.append(lid)
                    except Exception as e:
                        log(f"    {lid} → ERR {e}")
                if alive:
                    return alive
        except Exception as e:
            log(f"  ERR: {e}")
    return []

def main():
    OUT.write_text("", encoding="utf-8")
    log(f"=== FIND IDS V2 at {time.strftime('%H:%M:%S')} ===")
    far = find_farpost()
    avi = find_avito()
    log(f"\n=== RESULT ===")
    log(f"FARPOST alive: {far}")
    log(f"AVITO alive: {avi}")
    log(f"=== END ===")
    print(f"FARPOST: {far}")
    print(f"AVITO: {avi}")

if __name__ == "__main__":
    main()
