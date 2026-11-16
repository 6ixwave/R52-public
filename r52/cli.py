"""Командная строка R-52. Только этот модуль пишет в консоль.

Команды работают двумя способами: если ядро запущено, запрос уходит в его локальный API
(базу пишет только ядро); если не запущено, команда сама открывает базу под блокировкой.
"""

import argparse
import base64
import json
import logging
import logging.handlers
import os
import signal
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

from . import __version__
from .client import CoreClient
from .config import load_config
from .db import Database, latest_version, sqlite_ok
from .db.backup import create_backup, list_backups, restore_backup
from .db.migrate import schema_version
from .errors import AppError
from .lease import ProcessLock, is_core_running
from .log import UtcFormatter, setup_logging
from .supervisor import EXIT_ALREADY_RUNNING, EXIT_CONFIG, EXIT_ERROR, EXIT_OK, EXIT_USAGE, supervise

log = logging.getLogger("r52.cli")
TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED", "BLOCKED"}
EYE_MAX_BYTES = 16_000_000
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def out(text=""):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def warn(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _cli_logging(config, console_level):
    """Журнал пишется в core.log; в консоль попадают только сообщения уровня console_level и выше."""
    setup_logging(config.paths.logs_dir, config.log_level, console=True)
    for handler in logging.getLogger("r52").handlers:
        if type(handler) is logging.StreamHandler:
            handler.setLevel(console_level)


@contextmanager
def offline_app(config):
    """Открывает базу без сервера и фоновых потоков. Пока команда работает, ядро не запустится."""
    from .service import App

    _cli_logging(config, logging.WARNING)
    app = App(config).open()
    try:
        yield app
    finally:
        app.stop()


def _client_if_running(config):
    return CoreClient(config.paths) if is_core_running(config.paths.lock_file) else None


def _wait_task(client, task_id, poll=1.0):
    last = None
    while True:
        task = client.request("GET", f"/api/tasks/{task_id}")
        progress = task.get("progress")
        if progress is not None and progress != last and task.get("status") == "RUNNING":
            out(f"  выполнено {progress}%")
            last = progress
        if task.get("status") in TERMINAL:
            line = f"Задача {task_id}: {task.get('status')}"
            if task.get("error_code"):
                line += f" ({task.get('error_message') or task['error_code']})"
            out(line)
            return task
        time.sleep(poll)


# --- ядро -------------------------------------------------------------------

def _parent_fd():
    try:
        return None if sys.stdin is None else sys.stdin.fileno()
    except (OSError, ValueError, AttributeError):
        return None


def _watch_parent(app, fd):
    """Ядро, запущенное приложением, завершается, когда приложение закрывает stdin (и при его падении).

    Канал читается через os.read, а не через sys.stdin: буферизованный поток держит свою блокировку всё время
    ожидания, и если ядро остановят через API, интерпретатор при выходе не сможет её получить и аварийно
    завершит процесс (Fatal Python error: _enter_buffered_busy).
    """
    try:
        while True:
            try:
                if not os.read(fd, 4096):
                    break
            except OSError:
                break
        log.info("Приложение закрыло канал, ядро останавливается")
    finally:
        app.request_shutdown()


def cmd_serve(args):
    if args.port is not None and not 0 <= args.port <= 65535:
        warn("Порт должен быть от 0 до 65535")
        return EXIT_USAGE
    config = load_config(args.home)
    setup_logging(config.paths.logs_dir, config.log_level, console=args.console)
    from .service import App

    app = App(config)

    def on_signal(signum, frame):
        app.request_shutdown()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, on_signal)
    try:
        app.start(port=args.port)
    except AppError as error:
        log.error("Ядро не запущено: %s", error.message)
        raise
    except OSError as error:
        log.error("Не удалось открыть порт API: %s", error)
        warn(f"Ошибка: не удалось открыть порт API ({error.strerror or error})")
        return EXIT_CONFIG
    try:
        if args.parent_stdin:
            fd = _parent_fd()
            if fd is None:
                log.error("Канал stdin недоступен, хотя задан --parent-stdin")
                app.request_shutdown()
            else:
                threading.Thread(target=_watch_parent, args=(app, fd), name="r52-parent", daemon=True).start()
        if args.ready_stdout:
            out("R52_READY " + json.dumps(app.runtime_info(), ensure_ascii=False))
        while not app.shutdown_requested.wait(0.5):
            pass
        time.sleep(0.3)  # API успевает отдать ответ на запрос остановки
    finally:
        app.stop()
    return EXIT_OK


def cmd_supervise(args):
    config = load_config(args.home)
    logger = logging.getLogger("r52.supervisor")
    logger.setLevel(config.log_level)
    logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        config.paths.logs_dir / "supervisor.log", maxBytes=500_000, backupCount=2, encoding="utf-8", delay=True)
    handler.setFormatter(UtcFormatter(_FORMAT))
    logger.addHandler(handler)
    if args.console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(UtcFormatter(_FORMAT))
        logger.addHandler(stream)
    return supervise(home=config.paths.home)


# --- диагностика -------------------------------------------------------------

def cmd_doctor(args):
    rows = []

    def check(title, ok, detail="", required=True):
        rows.append((title, bool(ok), detail, required))

    check("Python 3.11 или новее", sys.version_info >= (3, 11), sys.version.split()[0])
    check("SQLite 3.37 или новее", sqlite_ok(), sqlite3.sqlite_version)
    config = None
    try:
        config = load_config(args.home)
        check("Файл настроек", True, str(config.paths.config_file))
    except AppError as error:
        check("Файл настроек", False, str(error.detail or error.message))
    if config is not None:
        paths = config.paths
        probe = paths.run_dir / "doctor.probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            check("Папка данных доступна для записи", True, str(paths.home))
        except OSError as error:
            check("Папка данных доступна для записи", False, str(error))
        running = is_core_running(paths.lock_file)
        check("Ядро запущено", running, "да" if running else "нет (нормально, если приложение закрыто)", required=False)
        if paths.db_file.exists():
            try:
                result = Database(paths.db_file).quick_check()
            except sqlite3.Error as error:
                result = str(error)
            check("Целостность базы", result == "ok", result)
        else:
            check("База данных", True, "ещё не создана, появится при первом запуске", required=False)
        backups = list_backups(paths.backups_dir)
        check("Резервные копии", bool(backups),
              f"{len(backups)} шт., последняя {backups[0]['name']}" if backups else "пока нет", required=False)
        for folder in config.intake_folders:
            check(f"Папка входящих {folder}", folder.is_dir(), "" if folder.is_dir() else "не найдена",
                  required=False)
    for title, ok, detail, required in rows:
        mark = "ОК" if ok else ("ОШИБКА" if required else "ВНИМАНИЕ")
        out(f"[{mark}] {title}" + (f": {detail}" if detail else ""))
    return EXIT_OK if all(ok for _, ok, _, required in rows if required) else EXIT_ERROR


def cmd_status(args):
    config = load_config(args.home)
    status = CoreClient(config.paths).request("GET", "/api/status")
    if args.json:
        out(json.dumps(status, ensure_ascii=False, indent=2))
        return EXIT_OK
    out(f"R-52 {status.get('version')}: ядро работает (PID {status.get('pid')}, с {status.get('started_at')})")
    health = status.get("health") or {}
    broken = [name for name, ok in (health.get("components") or {}).items() if not ok]
    out("Состояние: в порядке" if health.get("ok") else "Состояние: есть проблемы " + ", ".join(broken))
    database = status.get("database") or {}
    if database:
        out(f"База: {database.get('products', 0)} товаров, {database.get('observations', 0)} наблюдений, "
            f"{database.get('price_points', 0)} цен, схема v{database.get('schema_version')}")
    tasks = status.get("tasks") or {}
    out("Задачи: " + (", ".join(f"{name} {count}" for name, count in tasks.items()) or "нет"))
    out(f"Входящие ждут решения: {status.get('inbox_pending', 0)}")
    backups = status.get("backups") or {}
    last = backups.get("last")
    out(f"Резервные копии: {backups.get('count', 0)}" + (f", последняя {last['name']}" if last else ""))
    for source in status.get("sources") or []:
        if source.get("kind") == "manual":
            continue
        ready = source.get("collector") == "READY"
        out(f"Источник {source.get('name') or source.get('key')}: сбор {'подключён' if ready else 'пока не реализован'}")
    return EXIT_OK


# --- данные -------------------------------------------------------------------

def cmd_backup(args):
    config = load_config(args.home)
    client = _client_if_running(config)
    if client is not None:
        task = client.request("POST", "/api/backups", {})["task"]
        out(f"Резервная копия поставлена в очередь ядра, задача {task['id']}")
        task = _wait_task(client, task["id"])
        result = task.get("result") or {}
        if result.get("name"):
            out(f"Файл: {result['name']}")
        return EXIT_OK if task.get("status") == "COMPLETED" else EXIT_ERROR
    with offline_app(config) as app:
        info = create_backup(app.db, app.paths.backups_dir, label="manual", now=app.clock.now())
    out(f"Резервная копия создана: {info['name']} ({info['size']} байт)")
    return EXIT_OK


def cmd_backups(args):
    config = load_config(args.home)
    items = list_backups(config.paths.backups_dir)
    if not items:
        out("Резервных копий пока нет")
        return EXIT_OK
    for item in items:
        out(f"{item['name']}  {item['size']} байт  {item['created_at']}")
    return EXIT_OK


def cmd_restore(args):
    config = load_config(args.home)
    if is_core_running(config.paths.lock_file):
        raise AppError("CORE_MUST_BE_STOPPED", status=409)
    if not args.yes:
        out("Восстановление заменит текущую базу (прежняя будет сохранена отдельной копией).")
        out("Повторите команду с флагом --yes, чтобы продолжить.")
        return EXIT_USAGE
    _cli_logging(config, logging.ERROR)
    with ProcessLock(config.paths.lock_file):
        result = restore_backup(config.paths.db_file, config.paths.backups_dir, args.name)
    out(f"База восстановлена из {result['restored']}.")
    if result.get("safety_copy"):
        out(f"Прежняя база сохранена как {result['safety_copy']}.")
    return EXIT_OK


def cmd_import_legacy(args):
    if not args.d0 and not args.data01:
        warn("Укажите хотя бы один файл: --d0 и/или --data01")
        return EXIT_USAGE
    config = load_config(args.home)
    params = {"dry_run": bool(args.dry_run)}
    for key in ("d0", "data01"):
        value = getattr(args, key)
        if value:
            params[key] = str(Path(value).expanduser().resolve())
    client = _client_if_running(config)
    if client is not None:
        task = client.request("POST", "/api/tasks", {"kind": "import_legacy", "params": params})["task"]
        out(f"Импорт поставлен в очередь ядра, задача {task['id']}")
        task = _wait_task(client, task["id"])
        if task.get("result"):
            out(json.dumps(task["result"], ensure_ascii=False, indent=2))
        return EXIT_OK if task.get("status") == "COMPLETED" else EXIT_ERROR
    from .intake.legacy import import_d0, import_data01

    result = {}
    with offline_app(config) as app:
        if params.get("d0"):
            result["d0"] = import_d0(app, Path(params["d0"]), dry_run=params["dry_run"])
        if params.get("data01"):
            result["data01"] = import_data01(app, Path(params["data01"]), dry_run=params["dry_run"])
    out(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_import_eye(args):
    config = load_config(args.home)
    client = _client_if_running(config)
    failures = 0
    with (nullcontext() if client is not None else offline_app(config)) as app:
        for name in args.files:
            path = Path(name).expanduser()
            try:
                size = path.stat().st_size
                if size > EYE_MAX_BYTES:
                    warn(f"{path.name}: файл больше {EYE_MAX_BYTES // 1_000_000} МБ, пропущен")
                    failures += 1
                    continue
                data = path.read_bytes()
            except OSError as error:
                warn(f"{path}: не удалось прочитать ({error.strerror or error})")
                failures += 1
                continue
            if client is not None:
                answer = client.request("POST", "/api/inbox/import", {
                    "filename": path.name, "data_base64": base64.b64encode(data).decode("ascii")})
                item, created = answer["item"], answer["created"]
            else:
                item, created = app.inbox.stage_bytes(data, filename=path.name, origin="CLI")
            line = f"{path.name}: {'добавлен' if created else 'уже был во входящих'}, статус {item['status']}"
            if item.get("error_code"):
                line += f" ({item.get('error_message') or item['error_code']})"
                failures += 1
            out(line)
    return EXIT_OK if failures == 0 else EXIT_ERROR


def cmd_export(args):
    config = load_config(args.home)
    client = _client_if_running(config)
    if client is not None:
        result = client.request("POST", "/api/export", {})
    else:
        from .exporter import export_all

        with offline_app(config) as app:
            result = export_all(app)
    out(f"Экспорт сохранён: {result.get('path')}")
    return EXIT_OK


def cmd_migrate(args):
    config = load_config(args.home)
    if is_core_running(config.paths.lock_file):
        raise AppError("CORE_MUST_BE_STOPPED", status=409)
    with offline_app(config) as app:
        version = schema_version(app.db)
    out(f"Схема базы: версия {version} (последняя известная {latest_version()})")
    return EXIT_OK


def cmd_version(args):
    out(f"R-52 {__version__}")
    return EXIT_OK


# --- разбор аргументов ------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="папка данных (по умолчанию R52_HOME или %%LOCALAPPDATA%%\\R52)")
    parser = argparse.ArgumentParser(prog="r52", description="R-52: локальный помощник для товаров, цен и задач")
    sub = parser.add_subparsers(dest="command", required=True, metavar="команда")

    def command(name, func, text):
        item = sub.add_parser(name, parents=[common], help=text, description=text)
        item.set_defaults(func=func)
        return item

    item = command("serve", cmd_serve, "запустить ядро")
    item.add_argument("--port", type=int, help="порт API (по умолчанию из настроек; 0 = любой свободный)")
    item.add_argument("--console", action="store_true", help="дублировать журнал в консоль")
    item.add_argument("--ready-stdout", action="store_true", help=argparse.SUPPRESS)
    item.add_argument("--parent-stdin", action="store_true", help=argparse.SUPPRESS)
    item = command("supervise", cmd_supervise, "запустить ядро с автоматическим перезапуском при сбоях")
    item.add_argument("--console", action="store_true", help="дублировать журнал в консоль")
    command("doctor", cmd_doctor, "проверить окружение и данные")
    item = command("status", cmd_status, "состояние работающего ядра")
    item.add_argument("--json", action="store_true", help="вывести как JSON")
    command("backup", cmd_backup, "создать резервную копию базы")
    command("backups", cmd_backups, "показать резервные копии")
    item = command("restore", cmd_restore, "восстановить базу из копии (ядро должно быть остановлено)")
    item.add_argument("name", help="имя файла копии из команды backups")
    item.add_argument("--yes", action="store_true", help="подтвердить замену базы")
    item = command("import-legacy", cmd_import_legacy, "импортировать данные прежней версии (D0 и data-01)")
    item.add_argument("--d0", help="путь к базе D0 (SQLite)")
    item.add_argument("--data01", help="путь к базе data-01 (SQLite)")
    item.add_argument("--dry-run", action="store_true", help="только посчитать, ничего не записывать")
    item = command("import-eye", cmd_import_eye, "положить файлы экспорта R-52 Eye во входящие")
    item.add_argument("files", nargs="+", help="файлы JSON")
    command("export", cmd_export, "выгрузить данные в ZIP (CSV и JSON)")
    command("migrate", cmd_migrate, "применить миграции базы")
    command("version", cmd_version, "показать версию")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AppError as error:
        detail = f" ({error.detail})" if error.detail else ""
        warn(f"Ошибка: {error.message}{detail}")
        if error.code == "CORE_ALREADY_RUNNING":
            return EXIT_ALREADY_RUNNING
        if error.code == "CONFIG_INVALID":
            return EXIT_CONFIG
        return EXIT_ERROR
    except KeyboardInterrupt:
        warn("Прервано")
        return 130
