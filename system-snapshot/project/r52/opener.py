"""Открывает папку или ссылку средствами системы: Проводник, Finder или браузер по умолчанию.

Ссылки на магазины открываются в обычном браузере пользователя, а не в окне R-52: там его вход на сайты
и расширение Eye. Путь или ссылку проверяет вызывающий код.
"""

import os
import subprocess
import sys
import threading


def open_path(target):
    target = str(target)
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606
        return
    command = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    threading.Thread(target=process.wait, name="r52-opener", daemon=True).start()
