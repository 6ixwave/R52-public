"""Общие помощники тестов.

- временная папка данных и запуск команд r52 в отдельном процессе;
- синтетические выгрузки R-52 Eye (выдуманные товары, никаких настоящих данных).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def temp_home(test=None):
    path = Path(tempfile.mkdtemp(prefix="r52-test-"))
    if test is not None:
        test.addCleanup(shutil.rmtree, path, True)
    return path


def child_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("R52_HOME", None)
    return env


def cli(home, *args, timeout=90):
    """Запускает python -m r52 <args> --home <home> и возвращает CompletedProcess."""
    return subprocess.run([sys.executable, "-m", "r52", *args, "--home", str(home)], cwd=ROOT, env=child_env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


# --- синтетические выгрузки Eye --------------------------------------------------

def eye_document(listing_id="100200300", title="Видеокарта Тестовая RTX 4060 8 ГБ",
                 prices=(("regular", "31990", "31 990 ₽"),), observed_at="2026-11-01T09:00:00.000Z", source="OZON"):
    url = "https://www.ozon.ru/product/videokarta-testovaya-" + str(listing_id) + "/"

    def evidence(fragment, selector):
        return {"fragment": fragment, "method": "CONTENT_SCRIPT_VISIBLE_DOM", "selector": selector}

    fields = {key: {"status": "UNKNOWN", "value": None, "evidence": None}
              for key in ("availability", "brand", "bundle", "condition", "currency", "delivery",
                          "displayed_price", "model", "price_role", "seller", "variant")}
    if title is not None:
        fields["title"] = {"status": "KNOWN", "value": title, "evidence": evidence(title, "h1")}
    candidates = [{
        "amount": amount, "currency": "RUB", "role": role, "condition_type": role, "visible_label": text,
        "evidence": evidence(text, '[data-widget="webPrice"]'), "observed_at": observed_at, "source_url": url,
        "usable_purchase_price": True, "variant_association": "UNKNOWN",
    } for role, amount, text in prices]
    observation = {
        "browser_api": "REAL_CHROME_CONTENT_SCRIPT",
        "evidence_level": "REAL_NATIVE_EXTENSION_CURRENT_DOM",
        "fields": fields,
        "http_status": 200,
        "id": listing_id,
        "price_candidates": candidates,
        "source": source,
        "status": "SUCCESS",
        "url": url,
    }
    if observed_at is not None:
        observation["observed_at"] = observed_at
    return {"format": "JARVIS_BR02_EYE_USER_CAPTURE", "capture_action": "EXPLICIT_POPUP_CLICK",
            "fully_autonomous": False, "observation": observation}


def eye_bytes(**kwargs):
    return json.dumps(eye_document(**kwargs), ensure_ascii=False).encode("utf-8")


def write_eye(folder, name, **kwargs):
    path = Path(folder) / name
    path.write_bytes(eye_bytes(**kwargs))
    return path
