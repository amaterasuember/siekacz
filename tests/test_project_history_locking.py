"""Regression test: concurrent app instances must not overwrite history."""
from __future__ import annotations

import multiprocessing
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _save_record(history_path: str, lock_path: str, index: int) -> None:
    from app import project_history

    project_history.HISTORY_PATH = Path(history_path)
    project_history.HISTORY_LOCK_PATH = Path(lock_path)
    project_history.save_project_record({"id": f"project-{index}", "name": f"Projekt {index}"})


def test_concurrent_writers_preserve_every_record() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        history_path = root / "history.json"
        lock_path = root / "history.lock"
        context = multiprocessing.get_context("spawn")
        processes = [
            context.Process(target=_save_record, args=(str(history_path), str(lock_path), index))
            for index in range(8)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            assert process.exitcode == 0, f"writer process failed: {process.exitcode}"

        from app import project_history

        project_history.HISTORY_PATH = history_path
        project_history.HISTORY_LOCK_PATH = lock_path
        records = project_history.list_projects()
        assert {record["id"] for record in records} == {f"project-{index}" for index in range(8)}
    print("[OK] concurrent project-history writes retain all records")


def test_unreadable_history_is_backed_up_and_not_overwritten() -> None:
    from app import project_history

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        original_app_dir = project_history.APP_DIR
        original_history_path = project_history.HISTORY_PATH
        original_lock_path = project_history.HISTORY_LOCK_PATH
        try:
            project_history.APP_DIR = root
            project_history.HISTORY_PATH = root / "history.json"
            project_history.HISTORY_LOCK_PATH = root / "history.lock"
            project_history.save_project_record({"id": "original", "name": "Projekt do zachowania"})
            project_history.HISTORY_PATH.write_text("{uszkodzony json", encoding="utf-8")

            try:
                project_history.save_project_record({"id": "new", "name": "Nie wolno nadpisać"})
            except project_history.ProjectHistoryReadError:
                pass
            else:
                raise AssertionError("zapis nieczytelnej historii nie został zablokowany")

            assert project_history.HISTORY_PATH.read_text(encoding="utf-8") == "{uszkodzony json"
            assert list(root.glob("history.corrupt-*.json"))
        finally:
            project_history.APP_DIR = original_app_dir
            project_history.HISTORY_PATH = original_history_path
            project_history.HISTORY_LOCK_PATH = original_lock_path
    print("[OK] unreadable project history is preserved and write is blocked")


if __name__ == "__main__":
    test_concurrent_writers_preserve_every_record()
    test_unreadable_history_is_backed_up_and_not_overwritten()
    print("PROJECT HISTORY LOCKING TEST OK")
