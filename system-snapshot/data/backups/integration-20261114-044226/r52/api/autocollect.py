import json
import sqlite3

from ..collectors.discovery import source_url
from ..core.autocollect import enabled
from ..errors import AppError
from ..sources import SOURCE_KEYS
from ..validate import boolean, choice, fail, integer, only_keys, path_int, text


def register(app, add):
    prefix = "/api/autocollect"

    def overview(req):
        with app.db.read() as conn:
            sources = [dict(row) for row in conn.execute("SELECT * FROM auto_source ORDER BY source")]
            targets = [dict(row) for row in conn.execute("SELECT * FROM auto_target ORDER BY id")]
        for source in sources:
            source["last_result"] = json.loads(source["last_result"] or "null")
        return {"sources": sources, "targets": targets,
                "settings": {k:v for k,v in app.settings.all().items() if k.startswith("autocollect.")}}

    def save(req):
        data = req.body
        only_keys(data, {"source","kind","value","interval_seconds","item_limit","enabled"})
        if not isinstance(data.get("source"), str) or not isinstance(data.get("kind", "query"), str):
            raise fail("source")
        source = choice(data, "source", SOURCE_KEYS)
        kind = choice(data, "kind", ("query","category","url"), "query")
        if source is None or kind is None:
            raise fail("source")
        value = text(data, "value", 1000, required=True)
        if (kind == "url" or "://" in value) and not source_url(source, value):
            raise fail("value", "http_url_required")
        count = integer(data, "item_limit", 1, 50, required=True)
        interval = integer(data, "interval_seconds", 60, 2592000)
        active = boolean(data,"enabled",True)
        if active is None:
            raise fail("enabled")
        values = (source,kind,value,interval,count,int(active))
        try:
            with app.db.write() as conn:
                if "id" in req.params:
                    ident = path_int(req.params["id"])
                    if not conn.execute("UPDATE auto_target SET source=?,kind=?,value=?,interval_seconds=?,item_limit=?,enabled=?,last_run=NULL,page=1 WHERE id=?", values+(ident,)).rowcount:
                        raise AppError("NOT_FOUND",status=404)
                else:
                    automatic = conn.execute("SELECT id FROM auto_target WHERE source=? AND kind=? AND value=? AND origin='CATALOG'",
                                             (source, kind, value)).fetchone()
                    if automatic:
                        ident = automatic["id"]
                        conn.execute("UPDATE auto_target SET interval_seconds=?,item_limit=?,enabled=?,origin='USER',last_run=NULL,page=1 WHERE id=?",
                                     (interval, count, int(active), ident))
                    else:
                        ident = conn.execute("INSERT INTO auto_target(source,kind,value,interval_seconds,item_limit,enabled) VALUES(?,?,?,?,?,?)",values).lastrowid
        except sqlite3.IntegrityError:
            raise AppError("INVALID_INPUT",status=409, detail={"field":"value","reason":"duplicate"}) from None
        return {"id":ident}

    def remove(req):
        with app.db.write() as conn:
            if not conn.execute("DELETE FROM auto_target WHERE id=?", (path_int(req.params["id"]),)).rowcount:
                raise AppError("NOT_FOUND",status=404)
        return {"deleted":True}

    def toggle(req):
        only_keys(req.body,{"enabled"})
        active = boolean(req.body,"enabled")
        if active is None:
            raise fail("enabled")
        with app.db.write() as conn:
            if not conn.execute("UPDATE auto_source SET enabled=? WHERE source=?", (int(active),req.params["source"])).rowcount:
                raise AppError("NOT_FOUND",status=404)
        return {"enabled":active}

    def trigger(req):
        only_keys(req.body,set())
        if not enabled(app):
            raise AppError("AUTOCOLLECT_DISABLED",status=409)
        task,_ = app.tasks.submit("autocollect",{"force":True})
        return {"task_id":task["id"]}

    add("GET",prefix,overview)
    add("POST",prefix+"/targets",save)
    add("PUT",prefix+r"/targets/(?P<id>\d+)",save)
    add("DELETE",prefix+r"/targets/(?P<id>\d+)",remove)
    add("PATCH",prefix+r"/sources/(?P<source>[A-Z_]+)",toggle)
    add("POST",prefix+"/run",trigger)

