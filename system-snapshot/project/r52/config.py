"""Пути и статическая конфигурация.

В config.toml только то, что нужно до открытия базы. Всё, что меняется из окна
(тихие часы, сводка, помощник), хранится в базе: см. core/settings.py.
"""

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import AppError
from .fsutil import atomic_write_text
from .timeutil import zone

DEFAULT_TOML = """# Настройки ядра R-52. Файл читается при запуске.
# Тихие часы, сводка, помощник и резервные копии настраиваются в окне R-52.

[api]
# 0 = свободный порт выбирается автоматически. Адрес всегда 127.0.0.1.
port = 0

[logging]
# DEBUG, INFO, WARNING или ERROR
level = "INFO"

[display]
timezone = "Asia/Vladivostok"

[intake]
# Папки, где R-52 ищет выгрузки Eye (jarvis-eye*.json). Файлы не удаляются.
folders = ["~/Downloads"]
"""

_SCHEMA = {
    "api": {"port": int},
    "logging": {"level": str},
    "display": {"timezone": str},
    "intake": {"folders": list},
}
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def default_home():
    env = os.environ.get("R52_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "R52"
    return Path.home() / ".local" / "share" / "r52"


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def config_file(self):
        return self.home / "config.toml"

    @property
    def data_dir(self):
        return self.home / "data"

    @property
    def db_file(self):
        return self.data_dir / "r52.sqlite3"

    @property
    def backups_dir(self):
        return self.home / "backups"

    @property
    def logs_dir(self):
        return self.home / "logs"

    @property
    def run_dir(self):
        return self.home / "run"

    @property
    def exports_dir(self):
        return self.home / "exports"

    @property
    def runtime_file(self):
        return self.run_dir / "runtime.json"

    @property
    def lock_file(self):
        return self.run_dir / "core.lock"

    def ensure(self):
        for directory in (self.home, self.data_dir, self.backups_dir, self.logs_dir, self.run_dir, self.exports_dir):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Config:
    paths: Paths
    api_port: int = 0
    log_level: str = "INFO"
    timezone: str = "Asia/Vladivostok"
    intake_folders: tuple = field(default_factory=tuple)

    @property
    def tz(self):
        return zone(self.timezone)


def _invalid(detail):
    return AppError("CONFIG_INVALID", status=500, detail=detail)


def _expand_folder(text):
    return Path(os.path.expandvars(os.path.expanduser(text)))


def load_config(home=None):
    paths = Paths(Path(home).expanduser().resolve() if home else default_home())
    paths.ensure()
    if not paths.config_file.exists():
        atomic_write_text(paths.config_file, DEFAULT_TOML)
    try:
        data = tomllib.loads(paths.config_file.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise _invalid(f"{paths.config_file}: {error}") from None
    for section, values in data.items():
        if section not in _SCHEMA or not isinstance(values, dict):
            raise _invalid(f"неизвестный раздел [{section}]")
        for key, value in values.items():
            expected = _SCHEMA[section].get(key)
            if expected is None:
                raise _invalid(f"неизвестный ключ {section}.{key}")
            if not isinstance(value, expected) or isinstance(value, bool):
                raise _invalid(f"{section}.{key}: неверный тип")
    port = data.get("api", {}).get("port", 0)
    if not 0 <= port <= 65535:
        raise _invalid("api.port должен быть от 0 до 65535")
    level = data.get("logging", {}).get("level", "INFO").upper()
    if level not in _LEVELS:
        raise _invalid("logging.level: DEBUG, INFO, WARNING или ERROR")
    tz_name = data.get("display", {}).get("timezone", "Asia/Vladivostok")
    zone(tz_name)
    folders = data.get("intake", {}).get("folders", ["~/Downloads"])
    if len(folders) > 5 or not all(isinstance(item, str) and 0 < len(item) < 500 for item in folders):
        raise _invalid("intake.folders: до 5 путей строками")
    return Config(
        paths=paths,
        api_port=port,
        log_level=level,
        timezone=tz_name,
        intake_folders=tuple(_expand_folder(item) for item in folders),
    )
