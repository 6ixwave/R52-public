"""Запуск v75 с перехватом stdout/stderr."""
import subprocess, sys, time
from pathlib import Path

BASE = Path(__file__).parent
PY = sys.executable
SCRIPT = BASE / "diag_v75_e2e_all.py"
LOG = BASE / "diag_v75_out.txt"

t0 = time.monotonic()
proc = subprocess.run(
    [PY, str(SCRIPT)],
    cwd=str(BASE),
    capture_output=True,
    text=True,
    timeout=300,
)
elapsed = time.monotonic() - t0

with open(LOG, "w", encoding="utf-8") as f:
    f.write(f"=== STDOUT ({len(proc.stdout)} bytes) ===\n")
    f.write(proc.stdout)
    f.write(f"\n=== STDERR ({len(proc.stderr)} bytes) ===\n")
    f.write(proc.stderr)
    f.write(f"\n=== EXIT: {proc.returncode} | {elapsed:.1f}s ===\n")

print(f"v75 EXIT={proc.returncode} time={elapsed:.1f}s")
print(f"Log: {LOG}")
if proc.stdout:
    print(proc.stdout[-2000:])
if proc.stderr:
    print("--- STDERR (last 2000) ---")
    print(proc.stderr[-2000:])
