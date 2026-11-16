r"""Обёртка v70: запускает diag_v69_persist_all.py и ловит stdout+stderr в один файл."""
import subprocess, sys, os, time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Python315\python.exe"
SCRIPT = os.path.join(BASE, "diag_v69_persist_all.py")
OUT = os.path.join(BASE, "diag_v69_out.txt")

print(f"[v70] Запуск: {PY} {SCRIPT}", flush=True)
t0 = time.time()

proc = subprocess.run(
    [PY, SCRIPT],
    capture_output=True,
    text=True,
    cwd=BASE,
    timeout=300,
)
elapsed = time.time() - t0

with open(OUT, "w", encoding="utf-8") as f:
    f.write(f"=== STDOUT ===\n{proc.stdout}\n")
    f.write(f"=== STDERR ===\n{proc.stderr}\n")
    f.write(f"=== EXIT: {proc.returncode} | {elapsed:.1f}s ===\n")

print(f"[v70] EXIT={proc.returncode} | {elapsed:.1f}s", flush=True)
print(f"[v70] Лог: {OUT}", flush=True)

# Короткая сводка в консоль
if proc.stdout:
    lines = proc.stdout.strip().splitlines()
    for l in lines[-20:]:
        print(f"  OUT| {l}")
if proc.stderr:
    lines = proc.stderr.strip().splitlines()
    for l in lines[-30:]:
        print(f"  ERR| {l}")
