"""Обёртка: запускает test_farpost_e2e.py, ловит ВСЁ в файл."""
import subprocess, sys, os, traceback

os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    r = subprocess.run(
        [sys.executable, "-u", "test_farpost_e2e.py"],
        capture_output=True, text=True, timeout=120,
    )
    with open("test_e2e_out.txt", "w", encoding="utf-8") as f:
        f.write(f"=== STDOUT ===\n{r.stdout}\n")
        f.write(f"=== STDERR ===\n{r.stderr}\n")
        f.write(f"=== EXIT CODE: {r.returncode} ===\n")
    print(f"DONE exit={r.returncode}")
except subprocess.TimeoutExpired:
    with open("test_e2e_out.txt", "w", encoding="utf-8") as f:
        f.write("TIMEOUT after 120s\n")
    print("TIMEOUT")
except Exception:
    with open("test_e2e_out.txt", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
    print("WRAPPER ERROR")
