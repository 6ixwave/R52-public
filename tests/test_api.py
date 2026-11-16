"""Локальный API ядра: доступ, проверка входных данных, входящие, задачи, слежение."""

import base64
import http.client
import json
import shutil
import unittest
from urllib.parse import quote

import helpers
from r52.desktop.link import CoreLink


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = helpers.temp_home()
        cls.link = CoreLink(cls.home)
        cls.link.connect()
        cls.runtime = cls.link.client.runtime()

    @classmethod
    def tearDownClass(cls):
        cls.link.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def api(self, method, path, body=None):
        return self.link.client.request(method, path, body)

    def raw(self, method, path, body=None, headers=None, token=True):
        sent = dict(headers or {})
        if token:
            sent.setdefault("Authorization", "Bearer " + self.runtime["token"])
        if body is not None:
            body = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            sent.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.runtime["port"], timeout=20)
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
        data = json.loads(payload) if payload else {}
        return response.status, ((data.get("error") or {}).get("code") if isinstance(data, dict) else None), data

    # --- доступ ------------------------------------------------------------------

    def test_health_without_token(self):
        self.assertEqual(self.raw("GET", "/healthz", token=False)[0], 200)

    def test_token_required(self):
        self.assertEqual(self.raw("GET", "/api/status", token=False)[0], 401)
        self.assertEqual(self.raw("GET", "/api/status", headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.raw("GET", "/api/status")[0], 200)

    def test_browser_origin_refused(self):
        status, code, _ = self.raw("GET", "/api/status", headers={"Origin": "http://localhost:8000"})
        self.assertEqual((status, code), (403, "ORIGIN_REFUSED"))

    def test_foreign_host_refused(self):
        status, _, _ = self.raw("GET", "/api/status", headers={"Host": "evil.example"})
        self.assertIn(status, (400, 403, 421))

    def test_unknown_route(self):
        self.assertEqual(self.raw("GET", "/api/nothing-here")[0], 404)

    def test_body_must_be_json_object(self):
        self.assertEqual(self.raw("POST", "/api/products/manual", b"not json")[0], 400)
        self.assertEqual(self.raw("POST", "/api/products/manual", b"[1, 2]")[0], 400)
        self.assertEqual(self.raw("POST", "/api/products/manual", {"title": "x", "surprise": 1})[0], 400)

    # --- задачи -----------------------------------------------------------------

    def test_task_lookup_by_id(self):
        task = self.api("POST", "/api/tasks", {"kind": "maintenance", "params": {}})["task"]
        self.assertEqual(len(task["id"]), 32)
        found = self.api("GET", f"/api/tasks/{task['id']}")
        self.assertEqual(found.get("task", found)["id"], task["id"])
        for action in ("cancel", "retry"):
            self.assertNotEqual(self.raw("POST", f"/api/tasks/{task['id']}/{action}", {})[0], 404, action)
        self.assertEqual(self.raw("GET", "/api/tasks/" + "0" * 32)[0], 404)

    def test_idempotent_submit(self):
        body = {"kind": "maintenance", "params": {}, "idempotency_key": "same-request-1"}
        first, second = self.api("POST", "/api/tasks", body), self.api("POST", "/api/tasks", body)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["task"]["id"], second["task"]["id"])

    def test_unknown_task_kind(self):
        self.assertEqual(self.raw("POST", "/api/tasks", {"kind": "format_disk", "params": {}})[0], 400)

    # --- входящие и товары ---------------------------------------------------------

    def import_eye(self, data, name="eye.json"):
        return self.api("POST", "/api/inbox/import", {"filename": name, "data_base64": base64.b64encode(data).decode("ascii")})

    def test_inbox_flow(self):
        data = helpers.eye_bytes(listing_id="555000111", title="Ноутбук Тестовый 15",
                                 prices=(("regular", "64990", "64 990 ₽"),))
        first = self.import_eye(data)
        self.assertTrue(first["created"])
        self.assertEqual(first["item"]["status"], "PENDING")
        self.assertEqual(first["item"]["preview"]["regular_minor"], 6499000)
        again = self.import_eye(data, "copy.json")
        self.assertFalse(again["created"])
        self.assertEqual(again["item"]["id"], first["item"]["id"])
        self.api("POST", f"/api/inbox/{first['item']['id']}/accept", {})
        open_ids = [item["id"] for item in self.api("GET", "/api/inbox")["items"]]
        self.assertNotIn(first["item"]["id"], open_ids)
        all_ids = [item["id"] for item in self.api("GET", "/api/inbox?status=ALL")["items"]]
        self.assertIn(first["item"]["id"], all_ids)
        found = self.api("GET", "/api/products?q=" + quote("Ноутбук Тестовый"))["items"]
        self.assertEqual([item["title"] for item in found], ["Ноутбук Тестовый 15"])
        status = self.raw("POST", f"/api/inbox/{first['item']['id']}/accept", {})[0]
        self.assertTrue(400 <= status < 500, status)

    def test_invalid_export_is_kept_with_reason(self):
        document = helpers.eye_document(listing_id="555000222")
        document["fully_autonomous"] = True
        item = self.import_eye(json.dumps(document).encode("utf-8"), "auto.json")["item"]
        self.assertEqual((item["status"], item["error_code"]), ("INVALID", "MANUAL_CAPTURE_REQUIRED"))
        self.assertTrue(item["error_message"])

    def test_manual_product_and_watch(self):
        product = self.api("POST", "/api/products/manual", {"title": "Тестовый товар для слежения"})["product"]
        path = f"/api/watches/{product['id']}"
        self.assertEqual(self.raw("PUT", path, {"target_price": 100})[0], 400)
        self.assertEqual(self.raw("PUT", path, {"drop_percent": 95})[0], 400)
        watch = self.api("PUT", path, {"target": "1 500", "drop_percent": 10})["watch"]
        self.assertEqual(watch["target_minor"], 150000)
        manual = f"/api/products/{product['id']}/observations/manual"
        self.assertEqual(self.raw("POST", manual, {"amount": "100", "observed_at": "2099-01-01T00:00:00Z"})[0], 400)
        self.assertEqual(self.raw("POST", manual, {"amount": "100", "currency": "XYZ"})[0], 400)
        result = self.api("POST", manual, {"amount": "1 400"})
        self.assertEqual(result["product"]["product"]["regular_minor"], 140000)
        watches = self.api("GET", "/api/watches")["items"]
        mine = [row for row in watches if row["product"]["id"] == product["id"]]
        self.assertTrue(mine and mine[0]["target_reached"])
        self.assertTrue(self.api("DELETE", path)["deleted"])

    # --- настройки --------------------------------------------------------------

    def test_settings_validation(self):
        self.assertEqual(self.raw("PATCH", "/api/settings", {"no.such": 1})[1], "SETTING_UNKNOWN")
        self.assertEqual(self.raw("PATCH", "/api/settings", {"backup.keep_daily": 0})[1], "SETTING_INVALID")
        self.assertEqual(self.raw("PATCH", "/api/settings", {"digest.time": "25:00"})[1], "SETTING_INVALID")
        values = self.api("PATCH", "/api/settings", {"backup.keep_daily": 10})["values"]
        self.assertEqual(values["backup.keep_daily"], 10)
        self.assertEqual(self.api("GET", "/api/settings")["values"]["backup.keep_daily"], 10)
