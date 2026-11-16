"""Bounded embedded raster data. No external requests, SVG or paths."""
import base64
import binascii
import re

def raster_data(value):
    if not isinstance(value,str) or len(value)>550000:
        return None
    m=re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)",value)
    if not m:
        return None
    try:
        raw=base64.b64decode(m[2],validate=True)
    except (ValueError,binascii.Error):
        return None
    ok={"jpeg":raw.startswith(b"\xff\xd8\xff"),"png":raw.startswith(b"\x89PNG\r\n\x1a\n"),"webp":raw[:4]==b"RIFF" and raw[8:12]==b"WEBP"}
    return value if ok[m[1]] else None


from urllib.parse import urlsplit
CDN_SUFFIXES = ('ozone.ru','ozon.ru','wbstatic.net','wbbasket.ru','wildberries.ru','alicdn.com','aliexpress-media.com','aliexpress.ru','avito.st','avito.ru','farpost.ru','farpost-ads.ru')
MAX_ITEMS = 40
MAX_GALLERY_CHARS = 12_000_000

def public_media_url(value):
    if not isinstance(value,str) or len(value)>3000:
        return None
    try:
        u=urlsplit(value)
        host=(u.hostname or '').lower()
        if u.scheme!='https' or u.username or u.password or u.port not in (None,443):
            return None
        if not any(host==s or host.endswith('.'+s) for s in CDN_SUFFIXES):
            return None
        return value.split('#')[0]
    except ValueError:
        return None

def normalize_media(media):
    if not isinstance(media,dict):
        return {}
    candidates=media.get('items') if isinstance(media.get('items'),list) else []
    if not candidates and media.get('image_data'):
        candidates=[{'type':'image','image_data':media['image_data'],'source_url':media.get('source_url')}]
    items=[];seen=set();size=0
    for raw in candidates[:MAX_ITEMS]:
        if not isinstance(raw,dict):
            continue
        kind=raw.get('type','image')
        url=public_media_url(raw.get('source_url'))
        image=raster_data(raw.get('image_data')) if kind=='image' else None
        if kind not in ('image','video') or not (image or url):
            continue
        if kind=='video' and (not url or not __import__('re').search(r'\.(mp4|webm)(?:[?]|$)',url,__import__('re').I)):
            continue
        key=(kind,url or image)
        if key in seen:
            continue
        if size+len(image or '')>MAX_GALLERY_CHARS:
            if not url:
                continue
            image=None
        size+=len(image or '')
        seen.add(key);items.append({'type':kind,'image_data':image,'source_url':url})
    if not items:
        return {}
    cover=next((i for i in items if i['type']=='image'),{})
    return {'items':items,'image_data':cover.get('image_data'),'source_url':cover.get('source_url'),'method':'USER_APPROVED_PRODUCT_GALLERY'}

def gallery_from_product(row):
    import json
    data=dict(row)
    gallery=json.loads(data.get('gallery_json') or '[]')
    if not gallery and data.get('image_data'):
        gallery=[{'type':'image','image_data':data['image_data'],'source_url':public_media_url(data.get('image_source_url'))}]
    return gallery

def media_metadata(items):
    return {'items':[{'index':i,'type':v['type'],'local':bool(v.get('image_data')),'source_url':v.get('source_url') if v['type']=='video' else None} for i,v in enumerate(items)]}
