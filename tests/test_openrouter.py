"""Offline regression tests. Never reads a real API key or sends external requests."""
import contextlib
import copy
import json
import os
import socket
import tempfile
import threading
import unittest
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from r52.assistant.engine import Assistant
from r52.assistant.cloud import CloudAssistant, TOOL
from r52.assistant.openrouter import OpenRouterClient, MODEL
from r52.core.settings import Settings
from r52.db.database import Database
from r52.db.migrate import migrate
from r52.db.schema import MIGRATIONS
from r52.errors import AppError
from r52.timeutil import Clock, iso
from r52 import netguard


def plan(operation="clarify", **kw):
    data = dict(operation=operation, query="", other="", product_ids=[], target_minor=None,
                drop_percent=None, answer=[], clarification="Что именно вы хотите узнать?")
    return {**data, **kw}


def response(p=None, cost=0.001):
    return {"plan": p or plan(), "usage": {"prompt_tokens": 100, "completion_tokens": 50}, "cost_usd": cost, "model": MODEL}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.sqlite3")
        self.db.initialize(); migrate(self.db)
        self.app = SimpleNamespace(db=self.db, clock=Clock(), tz=timezone.utc)
        self.app.settings = Settings(self.db, self.app.clock)
        self.assistant = Assistant(self.app)
        self.cloud = CloudAssistant(self.assistant)
        self.sid = self.assistant.create_session()["id"]
        self.env = patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test-secret"})
        self.env.start()
        with self.db.write() as c:
            for i, title in enumerate(("Alpha laptop", "Beta laptop"), 1):
                now = iso(self.app.clock.now())
                c.execute("INSERT INTO product(id,source,listing_id,title,origin,search_text,created_at,updated_at) VALUES(?,'MANUAL',?,?, 'manual',?,?,?)", (i,str(i),title,title.lower(),now,now))

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def enable(self):
        self.app.settings.update({"assistant.llm": "openrouter"})

    def send(self, text="Помоги выбрать ноутбук", key="test-key-0001", scope=None):
        return self.assistant.send(self.sid, text, key, scope)


class IntegrationTests(Fixture):
    def test_defaults_and_old_modes(self):
        self.assertEqual(self.app.settings.get("assistant.llm"), "off")
        self.app.settings.update({"assistant.llm":"ollama"})
        self.assertEqual(self.app.settings.get("assistant.ollama_port"), 11434)

    def test_greeting_and_identity_are_free(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat", side_effect=AssertionError("paid")):
            for n,text in enumerate(("ты ии?","кто ты?","привет")):
                out=self.send(text,key=f"smalltalk-key-{n}")
                self.assertEqual(out["reply"]["data"]["llm"]["calls"],0)
                self.assertNotIn("Не понял",out["reply"]["text"])
        self.assertEqual(self.cloud.status()["calls"],0)

    def test_off_no_cloud(self):
        with patch("r52.assistant.cloud.OpenRouterClient.chat", side_effect=AssertionError("paid")):
            self.send()

    def test_exact_commands_no_paid_call(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat", side_effect=AssertionError("paid")):
            out = self.send("найди laptop")
        self.assertEqual(out["reply"]["data"]["llm"]["provider"], "rules")
        self.assertEqual(self.cloud.status()["calls"], 0)

    def test_missing_key_fallback_no_reservation(self):
        self.enable()
        with patch.dict(os.environ, {"OPENROUTER_API_KEY":""}), patch("r52.assistant.cloud.OpenRouterClient.chat", side_effect=AssertionError("paid")):
            out=self.send()
        self.assertFalse(out["reply"]["data"]["llm"]["used"])
        self.assertEqual(self.cloud.status()["calls"],0)

    def test_ollama_kept(self):
        self.app.settings.update({"assistant.llm":"ollama"})
        with patch("r52.assistant.llm.OllamaClient.chat", return_value="Недостаточно данных") as local, patch("r52.assistant.cloud.OpenRouterClient.chat", side_effect=AssertionError("cloud")):
            out=self.send()
        local.assert_called_once()
        self.assertTrue(out["reply"]["data"]["llm"]["used"])

    def test_cloud_usage_and_replay(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat", return_value=response()) as client:
            first=self.send();second=self.send()
        client.assert_called_once();self.assertTrue(second["replayed"])
        self.assertTrue(first["reply"]["data"]["llm"]["used"])
        self.assertAlmostEqual(self.cloud.status()["known_cost_usd"],.001)

    def test_key_conflict(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat", return_value=response()):
            self.send()
        with self.assertRaises(AppError) as cm:self.send("Другой вопрос")
        self.assertEqual(cm.exception.code,"IDEMPOTENCY_CONFLICT")

    def test_parallel_same_message_paid_once(self):
        self.enable(); errors=[]; results=[]
        def work():
            try:results.append(self.send())
            except BaseException as e:errors.append(e)
        with patch("r52.assistant.cloud.OpenRouterClient.chat", return_value=response()) as client:
            threads=[threading.Thread(target=work) for _ in range(4)]
            for t in threads:t.start()
            for t in threads:t.join(10)
        self.assertFalse(errors);self.assertEqual(len(results),4);client.assert_called_once()
        self.assertEqual(sum(bool(r["replayed"]) for r in results),3)

    def test_persistent_claim_after_interruption(self):
        self.enable()
        with patch.object(self.assistant,"_send",side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):self.send()
        restarted=Assistant(self.app)
        with self.assertRaises(AppError) as cm:restarted.send(self.sid,"Помоги выбрать ноутбук","test-key-0001",None)
        self.assertEqual(cm.exception.code,"ASSISTANT_REQUEST_UNCERTAIN")

    def test_network_error_fallback_unknown_cost_no_retry(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat", side_effect=AppError("OPENROUTER_UNAVAILABLE",message="Нет связи")) as client:
            out=self.send()
        client.assert_called_once();self.assertIsNone(out["reply"]["data"]["llm"]["cost_usd"])
        self.assertEqual(self.cloud.status()["unknown_calls"],1)

    def test_budget_blocks_before_http(self):
        self.enable();self.app.settings.update({"assistant.openrouter_budget_cents":1})
        with patch("r52.assistant.cloud.OpenRouterClient.chat", side_effect=AssertionError("paid")):
            out=self.send()
        self.assertIn("бюджет",out["reply"]["data"]["note"])

    def test_bad_plan_still_accounts_cost(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat", return_value=response({"operation":"execute_sql"})):
            out=self.send()
        self.assertFalse(out["reply"]["data"]["llm"]["used"])
        self.assertAlmostEqual(self.cloud.status()["known_cost_usd"],.001)

    def test_action_only_after_confirmation_and_idempotent(self):
        self.enable();p=plan("favorite_add",product_ids=[1])
        with patch("r52.assistant.cloud.OpenRouterClient.chat",return_value=response(p)):
            out=self.send("Пожалуйста добавь первый вариант в избранное")
        action=out["reply"]["data"]["actions"][0]
        with self.db.read() as c:self.assertEqual(c.execute("SELECT count(*) FROM favorite").fetchone()[0],0)
        self.assistant.confirm(action["token"])
        again=self.assistant.confirm(action["token"])
        self.assertTrue(again["replayed"])
        with self.db.read() as c:self.assertEqual(c.execute("SELECT count(*) FROM favorite").fetchone()[0],1)

    def test_scope_blocks_writes(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat",return_value=response(plan("favorite_add",product_ids=[1]))):
            out=self.send("Пожалуйста добавь первый вариант в избранное",scope=[1])
        self.assertFalse(out["reply"]["data"]["llm"]["used"])
        self.assertFalse(out["reply"]["data"]["actions"])

    def test_scope_rejects_other_id(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat",return_value=response(plan("answer",answer=[{"product_id":2,"text":"Beta"}]))):
            out=self.send(scope=[1])
        self.assertFalse(out["reply"]["data"]["llm"]["used"])

    def test_scoped_payload_has_no_outside_history(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat", return_value=response()) as client:
            self.send(scope=[1])
        body=json.loads(client.call_args[0][0][1]["content"])
        self.assertEqual([p["id"] for p in body["products"]],[1])
        self.assertEqual(body["history"],[])

    def test_unsupported_number_rejected(self):
        self.enable()
        with patch("r52.assistant.cloud.OpenRouterClient.chat",return_value=response(plan("answer",answer=[{"product_id":1,"text":"Цена 999999 рублей"}]))):
            out=self.send()
        self.assertFalse(out["reply"]["data"]["llm"]["used"])

    def test_history_bounded(self):
        for i in range(8):self.send("помощь",key=f"history-key-{i}")
        hist=self.cloud.history(self.sid)
        self.assertEqual(len(hist),6)
        self.assertTrue(all(len(m["text"])<=600 for m in hist))

    def test_status_contains_no_secret(self):
        self.assertNotIn("offline-test-secret",json.dumps(self.cloud.status()))

    def test_upgrade_v3_preserves_existing_settings(self):
        other=Database(Path(self.tmp.name)/"old.sqlite3");other.initialize()
        with patch("r52.db.migrate.MIGRATIONS",MIGRATIONS[:3]):migrate(other)
        settings=Settings(other,self.app.clock);settings.update({"assistant.llm":"ollama"})
        migrate(other)
        self.assertEqual(Settings(other,self.app.clock).get("assistant.llm"),"ollama")
        self.assertEqual(other.quick_check(),"ok")

    def test_argument_validation(self):
        cards=[{"id":1,"title":"Alpha","price_text":"цена неизвестна","last_seen_at":None,"source":"MANUAL","availability":"UNKNOWN","observation_count":0}]
        for p in [plan("watch",product_ids=[True]),plan("watch",product_ids=[1],target_minor=-1),plan("watch",product_ids=[1],drop_percent=99),plan("search",query="x"*501),plan("favorite_add",product_ids=[1]),plan("watch",product_ids=[1],target_minor=9999900)]:
            with self.subTest(p=p),self.assertRaises((ValueError,AppError)):
                self.cloud._execute(p,cards,"обычный вопрос",[])


class TransportTests(unittest.TestCase):
    def call(self, status=200, body=None, error=None):
        from unittest.mock import MagicMock
        conn=MagicMock()
        conn.request.side_effect=error
        raw=body if isinstance(body,bytes) else json.dumps(body or {"choices":[{"finish_reason":"tool_calls","message":{"tool_calls":[{"type":"function","function":{"name":"assistant_request","arguments":json.dumps(plan())}}]}}],"usage":{"cost":.001,"prompt_tokens":12}}).encode()
        conn.getresponse.return_value=SimpleNamespace(status=status,read=lambda n:raw)
        self.conn=conn
        with patch.dict(os.environ,{"OPENROUTER_API_KEY":"offline-only"}),patch("r52.assistant.openrouter.openrouter_access",contextlib.nullcontext),patch("r52.assistant.openrouter.http.client.HTTPSConnection",return_value=conn):
            return OpenRouterClient().chat([{"role":"user","content":"hello"}],TOOL)

    def test_request(self):
        result=self.call();args,kw=self.conn.request.call_args
        self.assertEqual(args[:2],("POST","/api/v1/chat/completions"))
        body=json.loads(kw["body"]);self.assertEqual(body["model"],MODEL)
        self.assertNotIn("temperature",body);self.assertEqual(body["max_tokens"],1400)
        self.assertNotIn("parallel_tool_calls",body)
        self.assertEqual(body["reasoning"],{"effort":"low"})
        self.assertEqual(result["cost_usd"],.001)

    def test_http_errors(self):
        for status in (400,401,402,403,404,429,500,502,503):
            with self.subTest(status=status),self.assertRaises(AppError) as cm:self.call(status=status)
            self.assertEqual(cm.exception.code,"OPENROUTER_HTTP_"+str(status))
            self.conn.request.assert_called_once()

    def test_error_reason_visible_and_secret_redacted(self):
        body={"error":{"message":"No endpoints found that support all the requested parameters. offline-only sk-or-v1-secret Authorization=abc", "metadata":{"raw_request":"must-not-leak"}}}
        with self.assertRaises(AppError) as cm:self.call(status=404,body=body)
        msg=cm.exception.message
        self.assertIn("HTTP 404",msg)
        self.assertIn("No endpoints",msg)
        self.assertNotIn("offline-only",msg)
        self.assertNotIn("sk-or-v1-secret",msg)
        self.assertNotIn("must-not-leak",msg)
        self.assertNotIn("=abc",msg)

    def test_transport_errors_are_distinct(self):
        import ssl
        for error,code in [(TimeoutError(),"OPENROUTER_TIMEOUT"),(socket.gaierror(),"OPENROUTER_DNS"),(ssl.SSLError(),"OPENROUTER_TLS"),(netguard.NetworkBlocked(),"OPENROUTER_NETWORK_BLOCKED")]:
            with self.subTest(code=code),self.assertRaises(AppError) as cm:self.call(error=error)
            self.assertEqual(cm.exception.code,code)

    def test_non_json_error_is_not_dumped(self):
        with self.assertRaises(AppError) as cm:self.call(status=404,body=b"<html>secret server dump</html>")
        self.assertIn("не в формате JSON",cm.exception.message)
        self.assertNotIn("secret server",cm.exception.message)

    def test_timeout(self):
        with self.assertRaises(AppError):self.call(error=TimeoutError())
        self.conn.request.assert_called_once()

    def test_bad_json(self):
        with self.assertRaises(AppError):self.call(body=b'{invalid')

    def test_empty_or_truncated_answer_rejected(self):
        out=self.call(body={"choices":[{"finish_reason":"length","message":{"content":""}}],"usage":{"cost":.004}})
        self.assertIsNone(out["plan"]);self.assertEqual(out["cost_usd"],.004)

    def test_unknown_function_not_executed(self):
        out=self.call(body={"choices":[{"finish_reason":"tool_calls","message":{"tool_calls":[{"type":"function","function":{"name":"run_shell","arguments":"{}"}}]}}]})
        self.assertIsNone(out["plan"])

    def test_cost_missing_is_not_zero(self):
        out=self.call(body={"choices":[{"finish_reason":"length","message":{}}]})
        self.assertIsNone(out["cost_usd"])

    def test_nonfinite_cost_rejected(self):
        out=self.call(body={"choices":[{"finish_reason":"length","message":{}}],"usage":{"cost":float('nan')}})
        self.assertIsNone(out["cost_usd"])


class GuardTests(unittest.TestCase):
    def test_default_blocks_external(self):
        with self.assertRaises(netguard.NetworkBlocked):netguard.check_address(("8.8.8.8",443))
        netguard.check_address(("127.0.0.1",11434))

    def test_fixed_dns_scope_and_port(self):
        rows=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))]
        with patch("r52.netguard.socket.getaddrinfo",return_value=rows):
            with netguard.openrouter_access():
                netguard.check_address(('1.1.1.1',443))
                netguard._hook('socket.getaddrinfo',('openrouter.ai',443))
                with self.assertRaises(netguard.NetworkBlocked):netguard.check_address(('1.1.1.1',80))
                with self.assertRaises(netguard.NetworkBlocked):netguard._hook('socket.getaddrinfo',('example.com',443))
            with self.assertRaises(netguard.NetworkBlocked):netguard.check_address(('1.1.1.1',443))

    def test_private_dns_rejected(self):
        with patch("r52.netguard.socket.getaddrinfo",return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(netguard.NetworkBlocked):
                with netguard.openrouter_access():pass

    def test_exception_does_not_leak_to_other_thread(self):
        failures=[]
        def worker():
            try:netguard.check_address(('1.1.1.1',443))
            except netguard.NetworkBlocked:failures.append(True)
        with patch("r52.netguard.socket.getaddrinfo",return_value=[(2,1,6,'',('1.1.1.1',443))]):
            with netguard.openrouter_access():
                t=threading.Thread(target=worker);t.start();t.join()
        self.assertEqual(failures,[True])


if __name__=='__main__':unittest.main()
