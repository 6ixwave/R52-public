"""In-process server diagnostic v3: minimal, forward-slash TOML, clear output."""
import sys, os, time, threading, traceback
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

HOME = Path.home() / ".local" / "share" / "r52"
PORT = 8421

# --- Write config with FORWARD SLASHES (TOML-safe) ---
cfg_file = HOME / "config.toml"
cfg_file.parent.mkdir(parents=True, exist_ok=True)
intake_path = str(HOME / "intake_test").replace("\\", "/")
toml_text = (
    f'[api]\n'
    f'port = {PORT}\n'
    f'\n'
    f'[logging]\n'
    f'level = "DEBUG"\n'
    f'\n'
    f'[display]\n'
    f'timezone = "Asia/Vladivostok"\n'
    f'\n'
    f'[intake]\n'
    f'folders = ["{intake_path}"]\n'
)
cfg_file.write_text(toml_text, encoding="utf-8")
print(f"[1] config.toml written:\n{toml_text}")

# --- Remove stale lock ---
lock_file = HOME / "run" / "core.lock"
if lock_file.exists():
    lock_file.unlink()
    print(f"[2] Removed stale lock")
else:
    print(f"[2] No stale lock")

# --- Ensure intake dir ---
(HOME / "intake_test").mkdir(parents=True, exist_ok=True)

# --- Load config ---
from r52.config import load_config
from r52.service import App
from r52.log import setup_logging

config = load_config(str(HOME))
print(f"[3] Config OK: port={config.api_port}, intake={config.intake_folders}")

setup_logging(config.paths.logs_dir, "DEBUG", console=True)

# --- Start App ---
app = App(config)
print("[4] App created, calling start()...")

try:
    app.start(port=PORT)
    print(f"[5] start() OK: port={app.port}, running={app._running}")
except Exception as e:
    print(f"[5] start() FAILED: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

# --- Check after 3s ---
print("[6] Waiting 3s...")
time.sleep(3)
alive = app._running
shutdown = app.shutdown_requested.is_set()
print(f"[7] After 3s: running={alive}, shutdown_requested={shutdown}")

if alive:
    health = app.health()
    print(f"[8] Health: {health}")
    print("[9] ALIVE - stopping...")
    app.stop()
    print("[10] DONE: server was ALIVE")
else:
    print(f"[8] DEAD. threads:")
    for t in threading.enumerate():
        print(f"     {t.name} alive={t.daemon}")
    print(f"     server={app.server}")
    print(f"     watcher={app.watcher}")
    app.stop()
    print("[9] DONE: server was DEAD")
