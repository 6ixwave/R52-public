import re, glob, os

base_logs = r'C:\Users\aleks\AppData\Local\R52\logs'
base_proj = r'C:\Users\aleks\Downloads\R52-2.12-Layout-Fix_LASTUI\R52-2.12'
files = (glob.glob(os.path.join(base_logs, 'wb_diag*.html'))
         + glob.glob(os.path.join(base_logs, 'wb_pw_diag.html'))
         + glob.glob(os.path.join(base_proj, '_diag_wb*.html')))

# regex ровно как в wb.py HTML fallback (стр 966) и PwFetcher (стр 1033/1039)
prod_pat  = re.compile(r'<a[^>]*href=["\'](?:https?://www\.wildberries\.ru)?/catalog/(\d+)/detail\.aspx[^"\']*["\'][^>]*>', re.I)
prod_pat2 = re.compile(r'/catalog/(\d{6,})/detail\.aspx', re.I)
cat6      = re.compile(r'/catalog/(\d{6,})', re.I)
# современные WB-маркеры
data_nm   = re.compile(r'data-(?:nm|id|product)[a-z-]*["\']?\s*[:=]\s*["\']?(\d{6,})', re.I)
json_nm   = re.compile(r'"(?:nmId|nm_id|id)"\s*:\s*(\d{6,})', re.I)

for f in sorted(files):
    h = open(f, encoding='utf-8', errors='ignore').read()
    m1 = prod_pat.findall(h)
    m2 = prod_pat2.findall(h)
    m3 = cat6.findall(h)
    m4 = data_nm.findall(h)
    m5 = json_nm.findall(h)
    uniq3 = sorted(set(m3))
    print('=' * 60)
    print(os.path.basename(f), 'len', len(h))
    print('  p1(<a detail.aspx)>', len(m1), 'uniq', len(set(m1)))
    print('  p2(/catalog/6+ detail)>', len(m2), 'uniq', len(set(m2)))
    print('  cat6(any /catalog/6+)>', len(m3), 'uniq', len(uniq3), 'sample', uniq3[:6])
    print('  data-nm', len(m4), 'json-nm', len(m5))
    # есть ли search-запрос в странице?
    for kw in ('видеокарт', 'noindex', 'searchResult', 'catalog/0', 'detail.aspx'):
        if kw in h:
            print('   has:', kw)
print('WBCHK_DONE')
