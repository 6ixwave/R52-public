"""Сетевой ограничитель: процесс ядра может соединяться только с этим компьютером.

Старый хук DATA-01 блокировал и localhost, поэтому локальная модель была недоступна.
Здесь разрешён только loopback (127.0.0.0/8 и ::1). Хук аудита нельзя снять,
поэтому он ставится только в процессе ядра (команда serve).
"""

import ipaddress
import socket
import sys
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit

_lock = threading.Lock()
_installed = False
_thread_local = threading.local()

_OPENROUTER_HOSTS = frozenset(("openrouter.ai",))
_openrouter_allowed = False


class NetworkBlocked(PermissionError):
    pass


def is_loopback(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    host = host.strip("[]").split("%")[0].lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_openrouter(host):
    if not isinstance(host, str):
        return False
    host = host.strip("[]").split("%")[0].lower()
    return host in _OPENROUTER_HOSTS or host.endswith(".openrouter.ai")


def _is_private_or_loopback(ip_str):
    """Проверка IP на loopback/private — защита от DNS-rebinding."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return addr.is_loopback or addr.is_private
    except ValueError:
        return False


def check_address(address):
    if isinstance(address, (str, bytes)):
        return  # AF_UNIX: локальный сокет
    if isinstance(address, tuple) and address and is_loopback(address[0]):
        return
    if _openrouter_allowed and isinstance(address, tuple) and address:
        host = address[0]
        port = address[1] if len(address) > 1 else None
        if _is_openrouter(host):
            if port is None or port == 443:
                return
        else:
            ips = getattr(_thread_local, "ips", set())
            if host in ips and (port is None or port == 443):
                return
    raise NetworkBlocked("NETWORK_BLOCKED")


def _hook(event, args):
    if event in ("socket.connect", "socket.sendto", "socket.sendmsg"):
        if len(args) > 1:
            check_address(args[1])
    elif event == "socket.getaddrinfo":
        host = args[0] if args else None
        if host is not None and not is_loopback(host) and not (_openrouter_allowed and _is_openrouter(host)):
            raise NetworkBlocked("NETWORK_BLOCKED")
    elif event in ("socket.gethostbyname", "socket.gethostbyaddr"):
        if args and not is_loopback(args[0]) and not (_openrouter_allowed and _is_openrouter(args[0])):
            raise NetworkBlocked("NETWORK_BLOCKED")
    elif event == "urllib.Request":
        if args and not is_loopback(urlsplit(str(args[0])).hostname or ""):
            raise NetworkBlocked("NETWORK_BLOCKED")


def install():
    global _installed
    with _lock:
        if not _installed:
            sys.addaudithook(_hook)
            _installed = True


def installed():
    return _installed


@contextmanager
def openrouter_access():
    """Temporarily allow network access to openrouter.ai (bypasses loopback-only).

    Resolves openrouter hosts on entry; rejects private/loopback IPs (DNS rebinding).
    Only port 443 is permitted for resolved IPs.  IPs are stored per-thread so a
    worker thread that did not perform the resolution cannot reach the endpoint.

    Usage:
        with openrouter_access():
            conn.request(...)
    """
    global _openrouter_allowed
    _openrouter_allowed = True
    _thread_local.ips = set()
    try:
        for host in _OPENROUTER_HOSTS:
            try:
                results = socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for _family, _socktype, _proto, _canon, sockaddr in results:
                    ip = sockaddr[0]
                    if _is_private_or_loopback(ip):
                        raise NetworkBlocked("NETWORK_BLOCKED")
                    _thread_local.ips.add(ip)
            except socket.gaierror:
                pass
        yield
    finally:
        _openrouter_allowed = False
        _thread_local.ips = set()
