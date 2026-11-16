import json
"""Маршруты API. Каждый обработчик проверяет набор полей, типы и диапазоны."""

import base64
import binascii
import re
from datetime import timedelta
from urllib.parse import urlsplit

from ..core import watches
from ..core.notify import counts as notification_counts
from ..core.notify import list_events, list_notifications
from ..db.backup import list_backups
from ..domain.pricing import ROLES
from ..errors import AppError
from ..exporter import export_all
from ..log import tail
from ..money import CURRENCIES
from ..store import lists
from ..store.products import (
    SORTS, add_manual_observation, create_manual_product, get_product_row, list_products, product_detail,
)
from ..store.products import sparklines as spark_series
from ..timeutil import iso, parse_aware
from ..validate import amount_minor, boolean, choice, fail, id_list, integer, only_keys, path_int, query_int, text
from .server import Request
from .uiauth import PRESENCE_TTL

AVAILABILITY = ("IN_STOCK", "OUT_OF_STOCK", "UNKNOWN")
INBOX_STATUSES = ("PENDING", "INVALID", "ACCEPTED", "REJECTED")
NOTIFICATION_STATUSES = ("unread", "PENDING", "DEFERRED_QUIET", "SHOWN", "READ", "DISMISSED")
TASK_STATUSES = ("QUEUED", "RUNNING", "COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED", "BLOCKED")
GROUP = r"/api/compare/(?P<gid>\d+)"
TASK = r"/api/tasks/(?P<id>[a-f0-9]{32})"


class Router:
    def __init__(self):
        self.routes = []

    def add(self, method, pattern, handler):
        self.routes.append((method, re.compile("^" + pattern + "$"), handler))

    def dispatch(self, method, path, query, body, via="token"):
        path_matched = False
        for route_method, regex, handler in self.routes:
            match = regex.match(path)
            if match is None:
                continue
            path_matched = True
            if route_method != method:
                continue
            result = handler(Request(query, body, match.groupdict(), via))
            return result if isinstance(result, tuple) else (200, result)
        if path_matched:
            raise AppError("METHOD_NOT_ALLOWED", status=405)
        raise AppError("NOT_FOUND", status=404)


def _q_text(query, key, max_len):
    value = (query.get(key) or "").strip()
    if not value:
        return None
    if len(value) > max_len:
        raise fail(key, "too_long")
    return value


def _q_choice(query, key, options):
    value = query.get(key) or None
    if value is not None and value not in options:
        raise fail(key, "choice")
    return value


def build_router(app):
    router = Router()
    db = app.db
    from .autocollect import register as register_autocollect
    add = router.add
    register_autocollect(app, add)

    def now():
        return iso(app.clock.now())

    add("GET", "/healthz", lambda req: app.health())
    add("GET", "/api/status", lambda req: app.status())
    add("GET", "/api/sources", lambda req: {"items": app.sources()})

    def products(req):
        query = req.query
        only = _q_choice(query, "only", ("favorites", "watched"))
        sort = _q_choice(query, "sort", tuple(SORTS)) or "recent"
        with db.read() as conn:
            return list_products(
                conn, query=_q_text(query, "q", 200), source=_q_text(query, "source", 40), only=only, sort=sort,
                limit=query_int(query, "limit", 50, 1, 200), offset=query_int(query, "offset", 0, 0, 1_000_000))

    def product(req):
        with db.read() as conn:
            return product_detail(conn, path_int(req.params["id"]))

    def image_get(req):
        with db.read() as conn:
            row=get_product_row(conn,path_int(req.params["id"]))
            return {"image_data":row["image_data"],"observed_at":row["image_observed_at"]}

    def image_set(req):
        from ..domain.media import raster_data
        only_keys(req.body,{"image_data"})
        image=raster_data(req.body.get("image_data"))
        if not image:
            raise fail("image_data","invalid_raster")
        with db.write() as conn:
            pid=path_int(req.params["id"])
            get_product_row(conn,pid)
            conn.execute("UPDATE product SET image_data=?,image_source_url=NULL,image_observed_at=? WHERE id=?",(image,now(),pid))
            import json
            conn.execute("UPDATE product SET gallery_json=? WHERE id=?",(json.dumps([{'type':'image','image_data':image,'source_url':None}]),pid))
        return {"saved":True}

    def gallery_items(req, inbox=False):
        from ..domain.media import gallery_from_product, normalize_media
        with db.read() as conn:
            if not inbox:
                return gallery_from_product(get_product_row(conn,path_int(req.params["id"])))
            row=conn.execute("SELECT payload_json FROM inbox_item WHERE id=?",(path_int(req.params['id']),)).fetchone()
            if row is None:
                raise AppError("INBOX_ITEM_NOT_FOUND",status=404)
            payload=json.loads(row['payload_json'] or '{}')
            return normalize_media(payload.get('observation',{}).get('media')).get('items',[])

    def gallery_meta(req,inbox=False):
        from ..domain.media import media_metadata
        return media_metadata(gallery_items(req,inbox))

    def gallery_set(req):
        from ..domain.media import normalize_media
        only_keys(req.body,{'items'})
        media=normalize_media({'items':req.body.get('items')})
        if not media.get('items'):
            raise fail('items','invalid_media')
        with db.write() as conn:
            pid=path_int(req.params['id'])
            row=get_product_row(conn,pid)
            from ..domain.media import gallery_from_product
            items=gallery_from_product(row)+media['items']
            merged=normalize_media({'items':items})
            conn.execute('UPDATE product SET gallery_json=?,image_data=?,image_source_url=?,image_observed_at=? WHERE id=?',(json.dumps(merged['items'],ensure_ascii=False),merged.get('image_data'),merged.get('source_url'),now(),pid))
        return {'saved':True,'count':len(merged['items'])}

    def gallery_item(req,inbox=False):
        items=gallery_items(req,inbox)
        index=int(req.params['index'])
        if index>=len(items):
            raise AppError("MEDIA_NOT_FOUND",status=404)
        return items[index]

    add("GET",r"/api/products/(?P<id>\d+)/media",gallery_meta)
    add("POST",r"/api/products/(?P<id>\d+)/media",gallery_set)
    add("GET",r"/api/products/(?P<id>\d+)/media/(?P<index>\d+)",gallery_item)
    add("GET",r"/api/inbox/(?P<id>\d+)/media",lambda req:gallery_meta(req,True))
    add("GET",r"/api/inbox/(?P<id>\d+)/media/(?P<index>\d+)",lambda req:gallery_item(req,True))

    def category_set(req):
        only_keys(req.body,{"category_path"})
        category=text(req.body,"category_path",400,required=True)
        parts=[v.strip() for v in category.split(">") if v.strip()]
        if not 1<=len(parts)<=5:
            raise fail("category_path","invalid")
        with db.write() as conn:
            pid=path_int(req.params["id"])
            get_product_row(conn,pid)
            conn.execute("UPDATE product SET category_path=? WHERE id=?",(" > ".join(parts),pid))
        return {"saved":True}

    add("GET",r"/api/products/(?P<id>\d+)/image",image_get)
    add("POST",r"/api/products/(?P<id>\d+)/image",image_set)
    add("POST",r"/api/products/(?P<id>\d+)/category",category_set)

    def manual_product(req):
        only_keys(req.body, {"title", "url"})
        title, url = text(req.body, "title", 300, required=True), text(req.body, "url", 2000)
        with db.write() as conn:
            product_id, created = create_manual_product(conn, title, url, now())
        with db.read() as conn:
            return (201 if created else 200), product_detail(conn, product_id)

    def manual_observation(req):
        product_id = path_int(req.params["id"])
        body = req.body
        only_keys(body, {"amount", "currency", "role", "availability", "note", "observed_at"})
        amount = amount_minor(body, "amount", required=True)
        currency = choice(body, "currency", CURRENCIES, "RUB")
        role = choice(body, "role", ROLES, "regular")
        availability = choice(body, "availability", AVAILABILITY, "UNKNOWN")
        note = text(body, "note", 500)
        moment = app.clock.now()
        observed = text(body, "observed_at", 40)
        observed_dt = parse_aware(observed) if observed else moment
        if observed_dt > moment + timedelta(minutes=5):
            raise fail("observed_at", "future")
        with db.write() as conn:
            get_product_row(conn, product_id)
            observation_id = add_manual_observation(
                conn, product_id, amount_minor=amount, currency=currency, role=role, availability=availability,
                note=note, observed_at=iso(observed_dt), now=iso(moment))
            events = watches.evaluate(conn, app.notifier, product_id, observation_id)
        with db.read() as conn:
            return 201, {"observation_id": observation_id, "events": events, "product": product_detail(conn, product_id)}

    add("GET", "/api/products", products)
    add("POST", "/api/products/manual", manual_product)
    add("GET", r"/api/products/(?P<id>\d+)", product)
    add("POST", r"/api/products/(?P<id>\d+)/observations/manual", manual_observation)

    def inbox(req):
        status = _q_choice(req.query, "status", (*INBOX_STATUSES, "ALL"))
        return {"items": app.inbox.list(status=status, limit=query_int(req.query, "limit", 100, 1, 500)),
                "pending": app.inbox.pending_count()}

    def inbox_import(req):
        only_keys(req.body, {"filename", "data_base64"})
        filename = text(req.body, "filename", 255)
        encoded = text(req.body, "data_base64", 22_000_000, required=True)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise fail("data_base64", "base64") from None
        item, created = app.inbox.stage_bytes(data, filename=filename, origin="USER_FILE")
        return (201 if created else 200), {"item": item, "created": created}

    add("GET", "/api/inbox", inbox)
    add("POST", "/api/inbox/import", inbox_import)
    add("POST", r"/api/inbox/(?P<id>\d+)/accept", lambda req: app.inbox.accept(path_int(req.params["id"])))
    add("POST", r"/api/inbox/(?P<id>\d+)/reject", lambda req: app.inbox.reject(path_int(req.params["id"])))

    def favorites(req):
        with db.read() as conn:
            return {"items": lists.list_favorites(conn, limit=query_int(req.query, "limit", 200, 1, 1000))}

    def favorite_set(req):
        only_keys(req.body, {"note"})
        product_id = path_int(req.params["pid"])
        with db.write() as conn:
            lists.set_favorite(conn, product_id, True, text(req.body, "note", 500), now())
        return {"product_id": product_id, "favorite": True}

    def favorite_delete(req):
        product_id = path_int(req.params["pid"])
        with db.write() as conn:
            lists.set_favorite(conn, product_id, False, None, now())
        return {"product_id": product_id, "favorite": False}

    add("GET", "/api/favorites", favorites)
    add("POST", r"/api/favorites/(?P<pid>\d+)", favorite_set)
    add("DELETE", r"/api/favorites/(?P<pid>\d+)", favorite_delete)

    def watch_list(req):
        with db.read() as conn:
            return {"items": watches.list_watches(conn), "caveat": watches.CAVEAT}

    def watch_put(req):
        body = req.body
        only_keys(body, {"target", "price_basis", "drop_percent", "notify_availability", "active"})
        product_id = path_int(req.params["pid"])
        with db.write() as conn:
            watch = watches.put_watch(
                conn, product_id, target_minor=amount_minor(body, "target"),
                price_basis=choice(body, "price_basis", watches.BASES, "lowest_shown"),
                drop_percent=integer(body, "drop_percent", 1, 90),
                notify_availability=boolean(body, "notify_availability", False),
                active=boolean(body, "active", True), now=now())
        return {"watch": watch}

    def watch_delete(req):
        with db.write() as conn:
            watches.remove_watch(conn, path_int(req.params["pid"]))
        return {"deleted": True}

    add("GET", "/api/watches", watch_list)
    add("PUT", r"/api/watches/(?P<pid>\d+)", watch_put)
    add("DELETE", r"/api/watches/(?P<pid>\d+)", watch_delete)

    def groups(req):
        with db.read() as conn:
            return {"items": lists.list_groups(conn)}

    def group_create(req):
        only_keys(req.body, {"name", "product_ids"})
        name = text(req.body, "name", 120, required=True)
        product_ids = id_list(req.body, "product_ids", lists.MAX_GROUP, required=True)
        with db.write() as conn:
            group_id = lists.create_group(conn, name, product_ids, now())
        with db.read() as conn:
            return 201, lists.group_detail(conn, group_id)

    def compare_preview(req):
        only_keys(req.body, {"product_ids"})
        product_ids = id_list(req.body, "product_ids", lists.MAX_GROUP, required=True)
        with db.read() as conn:
            return lists.compare_products(conn, product_ids)

    def group_get(req):
        with db.read() as conn:
            return lists.group_detail(conn, path_int(req.params["gid"]))

    def group_delete(req):
        with db.write() as conn:
            lists.delete_group(conn, path_int(req.params["gid"]))
        return {"deleted": True}

    def group_add(req):
        only_keys(req.body, {"product_id"})
        group_id = path_int(req.params["gid"])
        product_id = integer(req.body, "product_id", 1, 2**62, required=True)
        with db.write() as conn:
            lists.add_item(conn, group_id, product_id, now())
        with db.read() as conn:
            return lists.group_detail(conn, group_id)

    def group_remove(req):
        group_id = path_int(req.params["gid"])
        with db.write() as conn:
            lists.remove_item(conn, group_id, path_int(req.params["pid"]))
        with db.read() as conn:
            return lists.group_detail(conn, group_id)

    add("GET", "/api/compare", groups)
    add("POST", "/api/compare", group_create)
    add("POST", "/api/compare/preview", compare_preview)
    add("GET", GROUP, group_get)
    add("DELETE", GROUP, group_delete)
    add("POST", GROUP + "/items", group_add)
    add("DELETE", GROUP + r"/items/(?P<pid>\d+)", group_remove)

    def events(req):
        with db.read() as conn:
            return {"items": list_events(conn, limit=query_int(req.query, "limit", 50, 1, 500))}

    def notifications(req):
        status = _q_choice(req.query, "status", NOTIFICATION_STATUSES)
        with db.read() as conn:
            return {"items": list_notifications(conn, status=status, limit=query_int(req.query, "limit", 50, 1, 500)),
                    "counts": notification_counts(conn)}

    def mark(status):
        def handler(req):
            notification_id = path_int(req.params["id"])
            with db.write() as conn:
                changed = app.notifier.mark(conn, notification_id, status)
            return {"id": notification_id, "status": status, "changed": changed}
        return handler

    def read_all(req):
        with db.write() as conn:
            return {"updated": app.notifier.read_all(conn)}

    add("GET", "/api/events", events)
    add("GET", "/api/notifications", notifications)
    add("POST", "/api/notifications/read-all", read_all)
    add("POST", r"/api/notifications/(?P<id>\d+)/shown", mark("SHOWN"))
    add("POST", r"/api/notifications/(?P<id>\d+)/read", mark("READ"))
    add("POST", r"/api/notifications/(?P<id>\d+)/dismiss", mark("DISMISSED"))

    def tasks(req):
        status = _q_choice(req.query, "status", TASK_STATUSES)
        return {"items": app.tasks.list(status=status, limit=query_int(req.query, "limit", 50, 1, 500)),
                "counts": app.tasks.counts(), "kinds": app.tasks.kinds()}

    def task_submit(req):
        only_keys(req.body, {"kind", "params", "idempotency_key"})
        kind = text(req.body, "kind", 40, required=True)
        params = req.body.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise fail("params", "object_required")
        task, created = app.tasks.submit(kind, params, origin="USER",
                                         idempotency_key=text(req.body, "idempotency_key", 200))
        return (201 if created else 200), {"task": task, "created": created}

    add("GET", "/api/tasks", tasks)
    add("POST", "/api/tasks", task_submit)
    add("GET", TASK, lambda req: app.tasks.get(req.params["id"]))
    add("POST", TASK + "/cancel", lambda req: app.tasks.cancel(req.params["id"]))
    add("POST", TASK + "/retry", lambda req: app.tasks.retry(req.params["id"]))
    add("GET", "/api/schedules", lambda req: {"items": app.scheduler.list()})

    def settings_patch(req):
        app.settings.update(req.body)
        return {"values": app.settings.all()}

    def assistant_cloud_status(req):
        from ..assistant.cloud import CloudAssistant
        return CloudAssistant(app.assistant).status()

    def assistant_analysis(req):
        from ..assistant.analysis import Analysis
        return Analysis(app.assistant).run(req.body)

    def analysis_history(req):
        from ..assistant.analysis import Analysis
        raw=req.query.get("product_ids","")
        if not isinstance(raw,str) or len(raw)>200:raise AppError("INVALID_INPUT")
        try: ids=[int(i) for i in raw.split(",") if i]
        except ValueError:raise AppError("INVALID_INPUT") from None
        return Analysis(app.assistant).history(ids,req.query.get("kind","card"),query_int(req.query,"limit",30,1,100),query_int(req.query,"offset",0,0,1000000))

    def analysis_saved(req):
        from ..assistant.analysis import Analysis
        return Analysis(app.assistant).saved(req.params["key"])

    add("GET", "/api/assistant/analysis/history", analysis_history)
    add("GET", r"/api/assistant/analysis/history/(?P<key>[A-Za-z0-9_-]{8,100})", analysis_saved)
    add("POST", "/api/assistant/analysis", assistant_analysis)
    add("GET", "/api/assistant/provider-status", assistant_cloud_status)
    add("GET", "/api/settings", lambda req: {"values": app.settings.all()})
    add("PATCH", "/api/settings", settings_patch)

    def sessions(req):
        return {"items": app.assistant.list_sessions(query_int(req.query, "limit", 20, 1, 100))}

    def session_create(req):
        only_keys(req.body, {"title"})
        return 201, app.assistant.create_session(text(req.body, "title", 120))

    def message(req):
        only_keys(req.body, {"text", "idempotency_key", "context_product_ids"})
        value = req.body.get("text")
        if not isinstance(value, str):
            raise AppError("TEXT_REQUIRED")
        return app.assistant.send(req.params["sid"], value, req.body.get("idempotency_key"), req.body.get("context_product_ids"))

    add("GET", "/api/assistant/sessions", sessions)
    add("POST", "/api/assistant/sessions", session_create)
    add("GET", r"/api/assistant/sessions/(?P<sid>[a-f0-9]{32})", lambda req: app.assistant.get_session(req.params["sid"]))
    add("POST", r"/api/assistant/sessions/(?P<sid>[a-f0-9]{32})/messages", message)
    add("POST", r"/api/assistant/actions/(?P<token>[A-Za-z0-9_-]{10,60})/confirm",
        lambda req: app.assistant.confirm(req.params["token"]))

    def backup_now(req):
        only_keys(req.body, set())
        task, _ = app.tasks.submit("backup", {"label": "manual"}, origin="USER")
        return 202, {"task": task}

    def export_now(req):
        only_keys(req.body, set())
        return 201, export_all(app)

    def shutdown(req):
        only_keys(req.body, set())
        return 202, app.request_shutdown()

    add("GET", "/api/backups", lambda req: {"items": list_backups(app.paths.backups_dir)})
    add("POST", "/api/backups", backup_now)
    add("POST", "/api/export", export_now)
    add("GET", "/api/log/tail", lambda req: {"lines": tail(app.paths.logs_dir, query_int(req.query, "lines", 200, 1, 2000))})
    add("POST", "/api/system/shutdown", shutdown)

    def token_only(req):
        if req.via != "token":
            raise AppError("TOKEN_REQUIRED", status=403)

    def ui_session(req):
        token_only(req)
        only_keys(req.body, set())
        return 201, app.ui.issue_code()

    def ui_presence(req):
        token_only(req)
        return app.ui.presence()

    def ui_heartbeat(req):
        only_keys(req.body, {"page"})
        return {"ok": app.ui.heartbeat(text(req.body, "page", 64, required=True)), "ttl": PRESENCE_TTL}

    def ui_bye(req):
        only_keys(req.body, {"page"})
        app.ui.bye(text(req.body, "page", 64, required=True))
        return {"ok": True}

    add("POST", "/api/ui/session", ui_session)
    add("GET", "/api/ui/presence", ui_presence)
    add("POST", "/api/ui/heartbeat", ui_heartbeat)
    add("POST", "/api/ui/bye", ui_bye)

    def sparklines(req):
        ids = []
        for part in (_q_text(req.query, "ids", 1200) or "").split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit() or len(part) > 15:
                raise fail("ids", "integer_list")
            if int(part) not in ids:
                ids.append(int(part))
        if len(ids) > 100:
            raise fail("ids", "too_many")
        points = query_int(req.query, "points", 40, 2, 200)
        with db.read() as conn:
            return {"items": spark_series(conn, ids, points)}

    add("GET", "/api/sparklines", sparklines)

    folders = {"home": "home", "data": "data_dir", "exports": "exports_dir", "backups": "backups_dir", "logs": "logs_dir"}

    def open_target(target):
        try:
            app.opener(target)
        except OSError as error:
            raise AppError("OPEN_FAILED", status=500, detail=type(error).__name__) from None

    def open_folder(req):
        only_keys(req.body, {"which"})
        which = choice(req.body, "which", tuple(folders), "home")
        target = getattr(app.paths, folders[which])
        target.mkdir(parents=True, exist_ok=True)
        open_target(target)
        return {"opened": which, "path": str(target)}

    def open_url(req):
        only_keys(req.body, {"url"})
        url = text(req.body, "url", 2000, required=True)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise fail("url", "http_url_required")
        open_target(url)
        return {"opened": True}

    add("POST", "/api/system/open-folder", open_folder)
    add("POST", "/api/system/open-url", open_url)
    return router
