import re
h = open(r'C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12\_diag_ozon_p2.html',
         encoding='utf-8', errors='ignore').read()
print('html_len', len(h))

# тот же паттерн, что в ozon.py (pattern 0 slug) — с query-группой
pat_slug = re.compile(
    r'href=["\'](?:/product/|https://www\.ozon\.ru/product/)[^"\'<>\s]+?-(\d{6,})/(\?[^"\']*)?["\']',
    re.I)
ms = list(pat_slug.finditer(h))
print('slug_matches', len(ms))
ids, seen = [], set()
for m in ms:
    v = m.group(1)
    if v not in seen:
        seen.add(v)
        ids.append(v)
print('unique_slug_ids', len(ids))
print('sample_ids', ids[:12])

# sanity: хвост slug (id должен быть в конце, перед /)
links = re.findall(r'/product/([^"\'<>\s]+?)/', h)
print('raw_links', len(links))
for x in links[:5]:
    print('  Ltail', x[-42:])
print('CHK2_DONE')
