"""Быстрые проверки без запуска ядра: деньги, цены, Eye, помощник, сеть, блокировка, надзор."""

import unittest

import helpers
from r52 import netguard
from r52.assistant.intents import parse
from r52.config import load_config
from r52.domain.normalize import normalize_eye, resolve_role
from r52.errors import AppError
from r52.intake.eye import MAX_BYTES, check_export, parse_export_bytes
from r52.lease import ProcessLock, is_core_running
from r52.money import format_minor
from r52.supervisor import RestartPolicy, supervise
from r52.validate import amount_minor


class MoneyTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_minor(None), "цена неизвестна")
        text = format_minor(3199000)
        self.assertIn("31\u00a0990", text)
        self.assertTrue(text.endswith("₽"))
        self.assertIn(",50", format_minor(3199050))
        self.assertIn("USD", format_minor(1000, "USD"))

    def test_amount(self):
        self.assertEqual(amount_minor({"amount": "49 990"}, "amount"), 4999000)
        self.assertEqual(amount_minor({"amount": "49990,50"}, "amount"), 4999050)
        self.assertEqual(amount_minor({"amount": 100}, "amount"), 10000)
        self.assertIsNone(amount_minor({}, "amount"))
        with self.assertRaises(AppError):
            amount_minor({}, "amount", required=True)
        with self.assertRaises(AppError):
            amount_minor({"amount": "дорого"}, "amount")


class EyeTest(unittest.TestCase):
    def test_normalize(self):
        document = helpers.eye_document(prices=(("regular", "31990", "31 990 ₽"),
                                                ("ozon_bank", "29990", "29 990 ₽ с Ozon Банком")))
        normalized, _ = normalize_eye(check_export(document))
        self.assertEqual(normalized["source"], "OZON")
        self.assertEqual(normalized["listing_id"], "100200300")
        self.assertEqual(sorted((item["role"], item["amount_minor"]) for item in normalized["prices"]),
                         [("card", 2999000), ("regular", 3199000)])

    def test_title_and_time_required(self):
        for broken, code in ((dict(title=None), "TITLE_WITH_EVIDENCE_REQUIRED"),
                             (dict(observed_at=None), "OBSERVATION_TIME_REQUIRED")):
            with self.assertRaises(AppError) as caught:
                normalize_eye(helpers.eye_document(**broken)["observation"])
            self.assertEqual(caught.exception.code, code)

    def test_only_explicit_capture(self):
        document = helpers.eye_document()
        document["fully_autonomous"] = True
        with self.assertRaises(AppError) as caught:
            check_export(document)
        self.assertEqual(caught.exception.code, "MANUAL_CAPTURE_REQUIRED")
        document = helpers.eye_document()
        document["format"] = "SOMETHING_ELSE"
        with self.assertRaises(AppError) as caught:
            check_export(document)
        self.assertEqual(caught.exception.code, "EXPORT_FORMAT_UNSUPPORTED")

    def test_bytes(self):
        with self.assertRaises(AppError) as caught:
            parse_export_bytes(b"{not json")
        self.assertEqual(caught.exception.code, "EXPORT_NOT_JSON")
        with self.assertRaises(AppError) as caught:
            parse_export_bytes(b" " * (MAX_BYTES + 1))
        self.assertEqual(caught.exception.code, "EXPORT_TOO_LARGE")
        self.assertEqual(parse_export_bytes("\ufeff{}".encode("utf-8")), {})

    def test_price_roles(self):
        self.assertEqual(resolve_role("ozon_bank", ""), ("card", "SOURCE_ROLE"))
        self.assertEqual(resolve_role("regular", ""), ("regular", "SOURCE_ROLE"))
        self.assertEqual(resolve_role("strange_role", "")[0], "unknown")


class IntentTest(unittest.TestCase):
    def test_parse(self):
        cases = {"статус": "status", "помощь": "help", "что нового": "news", "найди rtx 4060": "search",
                 "история цен rtx 4060": "history", "абвгд": "free"}
        for text, name in cases.items():
            self.assertEqual(parse(text).name, name, text)
        compare = parse("сравни rtx 4060 и rx 7600")
        self.assertEqual((compare.name, compare.query, compare.other), ("compare", "rtx 4060", "rx 7600"))
        watch = parse("следи за rtx 4060 до 29000")
        self.assertEqual((watch.name, watch.query, watch.amount_minor), ("watch", "rtx 4060", 2900000))
        self.assertEqual(parse("следи за rtx 4060 на 5%").percent, 5)


class NetworkGuardTest(unittest.TestCase):
    def test_loopback(self):
        for host in ("127.0.0.1", "::1", "localhost"):
            self.assertTrue(netguard.is_loopback(host), host)
        for host in ("8.8.8.8", "10.0.0.1", "example.com"):
            self.assertFalse(netguard.is_loopback(host), host)

    def test_check_address(self):
        netguard.check_address(("127.0.0.1", 80))
        with self.assertRaises(netguard.NetworkBlocked):
            netguard.check_address(("8.8.8.8", 443))


class LockTest(unittest.TestCase):
    def test_single_owner(self):
        path = helpers.temp_home(self) / "run" / "core.lock"
        first, second = ProcessLock(path), ProcessLock(path)
        first.acquire()
        try:
            self.assertTrue(first.held)
            with self.assertRaises(AppError) as caught:
                second.acquire()
            self.assertEqual(caught.exception.code, "CORE_ALREADY_RUNNING")
            self.assertTrue(is_core_running(path))
        finally:
            first.release()
        self.assertFalse(is_core_running(path))
        second.acquire()
        second.release()


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        config = load_config(helpers.temp_home(self))
        self.assertTrue(config.paths.config_file.exists())
        for folder in (config.paths.logs_dir, config.paths.backups_dir, config.paths.exports_dir):
            self.assertTrue(folder.is_dir(), folder)
        self.assertEqual(config.timezone, "Asia/Vladivostok")
        self.assertIsNotNone(config.tz)

    def test_invalid(self):
        for text in ("[nonsense]\nx = 1\n", "[api]\nport = 70000\n", "[api]\nport = \"80\"\n",
                     "[logging]\nlevel = \"LOUD\"\n", "это не toml = = =\n"):
            home = helpers.temp_home(self)
            config_file = load_config(home).paths.config_file
            config_file.write_text(text, encoding="utf-8")
            with self.assertRaises(AppError) as caught:
                load_config(home)
            self.assertEqual(caught.exception.code, "CONFIG_INVALID", text)


class SupervisorTest(unittest.TestCase):
    class Process:
        def __init__(self, code):
            self.code = code

        def wait(self):
            return self.code

    def run_codes(self, codes, policy=None):
        codes, sleeps, clock = list(codes), [], iter(range(0, 100_000, 10))
        result = supervise(home=helpers.temp_home(self), policy=policy, spawn=lambda: self.Process(codes.pop(0)),
                           sleep=sleeps.append, monotonic=lambda: next(clock))
        return result, sleeps, codes

    def test_restart_after_crash(self):
        self.assertEqual(self.run_codes([1, 1, 0]), (0, [1, 2], []))

    def test_final_codes_stop(self):
        for code in (2, 3, 4):
            self.assertEqual(self.run_codes([code, 0]), (code, [], [0]))

    def test_too_many_crashes(self):
        result, sleeps, _ = self.run_codes([1] * 10, RestartPolicy(max_crashes=2))
        self.assertEqual(result, 1)
        self.assertEqual(len(sleeps), 2)

    def test_delays_grow(self):
        policy = RestartPolicy(delays=(1, 5, 30))
        delays = []
        for second in range(4):
            policy.record_crash(second)
            delays.append(policy.delay())
        self.assertEqual(delays, [1, 5, 30, 30])
