"""Экспорт данных в ZIP: товары и цены в CSV для Excel, слежения, избранное и настройки в JSON."""

import csv
import io
import json
import os
import zipfile

from .core.watches import list_watches
from .store.lists import list_favorites

README = (
    "Экспорт данных R-52\r\n\r\n"
    "products.csv: товары и последняя известная цена.\r\n"
    "prices.csv: все сохранённые цены с ролью (regular обычная, card с картой банка, "
    "wallet с кошельком, coupon по купону, old старая зачёркнутая, unknown условия не распознаны).\r\n"
    "Файлы CSV в кодировке UTF-8 с разделителем точка с запятой, открываются в Excel.\r\n"
    "Время указано в UTC. Пустое время означает, что в источнике его не было.\r\n"
    "Цена с условием не равна итоговой стоимости: доставка и применимость условий не проверены.\r\n"
)


def _amount(minor):
    if minor is None:
        return ""
    whole, kop = divmod(int(minor), 100)
    return f"{whole},{kop:02d}"


def _cell(value):
    if value is None:
        return ""
    text = str(value)
    # Защита от формул в Excel: текст не должен начинаться со служебного символа.
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return "\ufeff" + buffer.getvalue()


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def export_all(app):
    stamp = app.clock.now().strftime("%Y%m%dT%H%M%SZ")
    app.paths.exports_dir.mkdir(parents=True, exist_ok=True)
    target = app.paths.exports_dir / f"r52-export-{stamp}.zip"
    counter = 2
    while target.exists():
        target = app.paths.exports_dir / f"r52-export-{stamp}-{counter}.zip"
        counter += 1
    with app.db.read() as conn:
        products = conn.execute(
            "SELECT id, source, listing_id, title, url, brand, model, origin, last_seen_at, last_currency, "
            "last_regular_minor, last_lowest_minor, last_lowest_role, last_availability, observation_count "
            "FROM product ORDER BY id").fetchall()
        prices = conn.execute(
            "SELECT pp.product_id, p.source, p.listing_id, pp.observed_at, pp.role, pp.amount_minor, pp.currency, "
            "pp.label FROM price_point pp JOIN product p ON p.id = pp.product_id "
            "ORDER BY pp.product_id, pp.observed_at, pp.id").fetchall()
        watch_items = list_watches(conn)
        favorites = list_favorites(conn, limit=1_000_000)
    products_csv = _csv(
        ["id", "source", "listing_id", "title", "url", "brand", "model", "origin", "last_seen_at_utc", "currency",
         "regular_price", "lowest_price", "lowest_role", "availability", "observations"],
        [[row["id"], row["source"], _cell(row["listing_id"]), _cell(row["title"]), _cell(row["url"]),
          _cell(row["brand"]), _cell(row["model"]), row["origin"], row["last_seen_at"] or "", row["last_currency"] or "",
          _amount(row["last_regular_minor"]), _amount(row["last_lowest_minor"]), row["last_lowest_role"] or "",
          row["last_availability"] or "", row["observation_count"]] for row in products])
    prices_csv = _csv(
        ["product_id", "source", "listing_id", "observed_at_utc", "role", "price", "currency", "label"],
        [[row["product_id"], row["source"], _cell(row["listing_id"]), row["observed_at"] or "", row["role"],
          _amount(row["amount_minor"]), row["currency"], _cell(row["label"])] for row in prices])
    temp = target.with_name(target.name + ".tmp")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("products.csv", products_csv)
        archive.writestr("prices.csv", prices_csv)
        archive.writestr("watches.json", _json(watch_items))
        archive.writestr("favorites.json", _json(favorites))
        archive.writestr("settings.json", _json(app.settings.all()))
        archive.writestr("README.txt", README)
    os.replace(temp, target)
    return {"path": str(target), "size": target.stat().st_size, "products": len(products), "prices": len(prices)}
