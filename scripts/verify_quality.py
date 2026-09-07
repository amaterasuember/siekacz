"""Run every regression in an isolated profile and retain per-test evidence."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    output = ROOT / "outputs" / Path(os.environ.get("SIEKACZ_QA_OUTPUT", "quality")).name
    output.mkdir(parents=True, exist_ok=True)
    tests = [ROOT / arg for arg in sys.argv[1:]] if len(sys.argv) > 1 else [*sorted((ROOT / "tests").glob("test_*.py")), ROOT / "tests" / "stability_smoke.py"]
    records = []
    with tempfile.TemporaryDirectory(prefix="siekacz-qa-") as profile:
        env = dict(os.environ, USERPROFILE=profile, HOME=profile, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        subprocess.run([sys.executable, "-c", "from database.db import init_db; init_db()"], cwd=ROOT, env=env, check=True)
        for test in tests:
            started = time.monotonic()
            with (output / f"{test.stem}.log").open("w", encoding="utf-8") as log:
                try:
                    code = subprocess.run([sys.executable, str(test)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200).returncode
                except subprocess.TimeoutExpired:
                    code = -1
                    log.write("\nTIMEOUT after 1200 seconds\n")
            records.append({"test": test.name, "exit_code": code, "seconds": round(time.monotonic() - started, 2)})
            print(f"{'PASS' if code == 0 else 'FAIL'} {test.name} ({records[-1]['seconds']}s)", flush=True)
            (output / "summary.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    failed = sum(r["exit_code"] != 0 for r in records)
    print(f"{len(records) - failed}/{len(records)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
