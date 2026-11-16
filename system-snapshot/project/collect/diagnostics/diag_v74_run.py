r"""v74: обёртка — запускает diag_v73_wb_avito.py, ловит stdout+stderr."""
import subprocess, sys, os, time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Python315\python.exe"
SCRIPT = os.path.join(BASE, "diag_v73_wb_avito.py")
OUT = os.path.join(BASE, "diag_v74_full_log.txt")

print(f"[v74] Запуск: {PY} -u {SCRIPT}", flush=True)
t0 = time.time()

proc = subprocess.run(
    [PY, "-u", SCRIPT],
    capture_output=True,
    text=True,
    cwd=BASE,
    timeout=300,
)
elapsed = time.time() - t0

with open(OUT, "w", encoding="utf-8") as f:
    f.write(f"=== STDOUT ({len(proc.stdout)} bytes) ===\n")
    f.write(proc.stdout or "(empty)\n")
    f.write(f"\n=== STDERR ({len(proc.stderr)} bytes) ===\n")
    f.write(proc.stderr or "(empty)\n")
    f.write(f"\n=== EXIT: {proc.returncode} | {elapsed:.1f}s ===\n")

print(f"[v74] EXIT={proc.returncode} | {elapsed:.1f}s", flush=True)
print(f"[v74] Лог: {OUT}", flush=True)

# Печать последних строк
if proc.stdout:
    print("\n--- STDOUT (last 30) ---", flush=True)
    for l in proc.stdout.strip().splitlines()[-30:]:
        print(f"  OUT| {l}", flush=True)
if proc.stderr:
    print("\n--- STDERR (last 40) ---", flush=True)
    for l in proc.stderr.strip().splitlines()[-40:]:
        print(f"  ERR| {l}", flush=True)
