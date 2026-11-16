"""Находит живые ID для FARPOST и AVITO через HTTP-запрос к категориям."""
import sys, os, re, time, json
from pathlib import Path
import requests

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

OUT = Path(__file__).resolve().parent / "find_ids.log"

def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
        f.flush()
        os.fsync(f.fileno())

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def find_farpost():
    log("\n=== FARPOST ===")
    # Категория: смартфоны Владивосток
    url = "https://www.farpost.ru/vladivostok/tech/communication/cellphones/"
    log(f"Fetch: {url}")
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}, timeout=(10, 30))
        log(f"HTTP {r.status_code}, {len(r.content)} bytes")
        html = r.text
        # Farpost listing IDs: /vladivostok/NNNNNNNNN.html or /NNNNNNNNN.html
        ids = re.findall(r'href="(?:/vladivostok)?/(\d{8,12})\.html"', html)
        # Deduplicate, keep order
        seen = set()
        unique = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                unique.append(i)
        log(f"Found {len(unique)} listing IDs")
        for lid in unique[:10]:
            # Quick ping
            lurl = f"https://www.farpost.ru/vladivostok/{lid}.html"
            try:
                lr = requests.get(lurl, headers={"User-Agent": UA}, timeout=(5, 15))
                status = "ALIVE" if lr.status_code == 200 else f"HTTP {lr.status_code}"
                log(f"  {lid} → {status} ({len(lr.content)}B)")
            except Exception as e:
                log(f"  {lid} → ERR {e}")
        return unique[:10]
    except Exception as e:
        log(f"FARPOST fetch error: {e}")
        return []

def find_avito():
    log("\n=== AVITO ===")
    # Avito: /moskva_i_mo/tech/phone/ or /all/telefony/
    urls = [
        "https://www.avito.ru/moskva_i_mo/tech/phone/",
        "https://www.avito.ru/all/telefony/",
    ]
    for url in urls:
        log(f"Fetch: {url}")
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}, timeout=(10, 30))
            log(f"HTTP {r.status_code}, {len(r.content)} bytes")
            if r.status_code != 200:
                continue
            html = r.text
            # Avito listing: /ad/NNNNNNN or /telefony/..._NNNNNNN
            ids = re.findall(r'/ad/(\d{9,12})', html)
            ids2 = re.findall(r'_(\d{9,12})["/?]', html)
            all_ids = list(dict.fromkeys(ids + ids2))
            log(f"Found {len(all_ids)} listing IDs")
            for lid in all_ids[:8]:
                lurl = f"https://www.avito.ru/ad/{lid}"
                try:
                    lr = requests.get(lurl, headers={"User-Agent": UA}, timeout=(5, 15), allow_redirects=True)
                    status = "ALIVE" if lr.status_code == 200 else f"HTTP {lr.status_code}"
                    log(f"  {lid} → {status} ({len(lr.content)}B)")
                except Exception as e:
                    log(f"  {lid} → ERR {e}")
            if all_ids:
                return all_ids[:10]
        except Exception as e:
            log(f"AVITO fetch error: {e}")
    return []

def main():
    OUT.write_text("", encoding="utf-8")
    log(f"=== FIND IDS at {time.strftime('%H:%M:%S')} ===")
    far = find_farpost()
    time.sleep(2)
    avi = find_avito()
    log(f"\n=== RESULT ===")
    log(f"FARPOST best: {far[0] if far else 'NONE'}")
    log(f"AVITO best: {avi[0] if avi else 'NONE'}")
    log(f"=== END ===")
    print(f"FARPOST: {far[:5]}")
    print(f"AVITO: {avi[:5]}")

if __name__ == "__main__":
    main()
