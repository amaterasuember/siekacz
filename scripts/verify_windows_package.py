"""Exercise the actual frozen Windows application with an isolated profile."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def verify(executable: Path) -> None:
    executable = executable.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="siekacz-package-") as profile:
        env = dict(os.environ, USERPROFILE=profile, HOME=profile, QT_QPA_PLATFORM="offscreen")
        process = subprocess.Popen([str(executable)], cwd=executable.parent, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            log_path = Path(profile) / ".cut_optimizer_desktop/siekacz9000.log"
            deadline = time.monotonic() + 30
            ready_at = None
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Application exited during startup: {process.returncode}")
                log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                if "[ERROR]" in log or "Traceback" in log:
                    raise RuntimeError(log)
                if "Main window ready" in log:
                    if ready_at is None:
                        ready_at = time.monotonic()
                    elif time.monotonic() - ready_at >= 2:
                        break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Packaged application did not initialize its window and event loop.")
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
    print(f"PASS: packaged window and event loop: {executable}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_windows_package.py path/to/SIEKACZ9000.exe")
    verify(Path(sys.argv[1]))
