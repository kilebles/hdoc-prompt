"""Исполнитель: берёт задачи из data/web и выполняет их. Запуск: python -m promptgen.worker
(на Windows — start_worker.bat под пользователем footage, чтобы agy работал с его входом).

- Проекты идут по одному в порядке очереди; внутри проекта вызовы модели параллельны.
- Разбор шаблонов — в отдельном потоке, чтобы не ждать конца длинного проекта.
- Блокировка data/web/worker.lock обновляется каждые 20 с: по ней API показывает «worker_online»,
  и второй экземпляр не запустится.
- После падения или закрытого окна проекты «running» возвращаются в очередь и продолжают с места.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from typing import Any

from . import project as prj
from . import templates as tpl
from .caller import Caller
from .config import load_config, lock_file, read_json, tasks_dir, write_json

LOCK_FRESH_SEC = 90


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, AttributeError):
        return False


def lock_owner() -> int:
    f = lock_file()
    try:
        pid = int(f.read_text(encoding="utf-8").strip() or 0)
        age = time.time() - f.stat().st_mtime
    except (OSError, ValueError):
        return 0
    if pid and pid != os.getpid() and age < LOCK_FRESH_SEC and _pid_alive(pid):
        return pid
    return 0


def worker_online() -> bool:
    f = lock_file()
    try:
        return time.time() - f.stat().st_mtime < LOCK_FRESH_SEC
    except OSError:
        return False


def _take_lock() -> bool:
    f = lock_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    if lock_owner():
        return False
    f.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _heartbeat(stop: threading.Event) -> None:
    f = lock_file()
    while not stop.wait(20):
        try:
            if f.read_text(encoding="utf-8").strip() == str(os.getpid()):
                os.utime(f, None)
        except OSError:
            pass


def _release_lock() -> None:
    f = lock_file()
    try:
        if f.read_text(encoding="utf-8").strip() == str(os.getpid()):
            f.unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ короткие задачи
def run_tasks_once() -> int:
    """Выполнить все ждущие задачи (разбор шаблонов). Возвращает число выполненных."""
    d = tasks_dir()
    if not d.exists():
        return 0
    done = 0
    for f in sorted(d.glob("*.json")):
        rec: dict[str, Any] = read_json(f) or {}
        if rec.get("status") != "queued":
            continue
        rec["status"] = "running"
        write_json(f, rec)
        config = load_config()
        calls = d / "calls" / rec["id"]
        tid = rec["id"]
        caller = Caller(config, calls, lambda k, t, tid=tid: print(f"[task {tid}] {t}", flush=True), lambda: False)
        try:
            if rec["kind"] == "template":
                print(f"[task] разбор шаблона {rec['params']}", flush=True)
                out = tpl.analyze_task(rec["params"], caller)
                rec["status"] = "done" if out.get("status") == "ready" else "error"
                rec["error"] = out.get("error", "")
            else:
                rec.update(status="error", error=f"неизвестная задача {rec['kind']}")
        except Exception as e:  # noqa: BLE001
            rec.update(status="error", error=f"{type(e).__name__}: {e}"[:500])
        rec["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        write_json(f, rec)
        done += 1
    _cleanup_tasks()
    return done


def _cleanup_tasks(keep_days: float = 7) -> None:
    for f in tasks_dir().glob("*.json"):
        rec = read_json(f) or {}
        if rec.get("status") in ("done", "error") and time.time() - f.stat().st_mtime > keep_days * 86400:
            f.unlink(missing_ok=True)


def _tasks_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            run_tasks_once()
        except Exception as e:  # noqa: BLE001
            print(f"[task] сбой: {e}", flush=True)
        stop.wait(3)


# ------------------------------------------------------------------ проекты
def run_projects_once() -> int:
    done = 0
    while True:
        p = prj.next_queued()
        if not p:
            return done
        print(f"[project] {p['id']} «{p.get('title', '')}» — старт", flush=True)
        prj.run(p["id"])
        try:
            print(f"[project] {p['id']} — {prj.load(p['id'])['status']}", flush=True)
        except prj.ProjectError:
            print(f"[project] {p['id']} — удалён", flush=True)
        done += 1


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser(description="Исполнитель модуля промптов")
    ap.add_argument("--once", action="store_true", help="выполнить то, что есть в очереди, и выйти (для тестов)")
    args = ap.parse_args(argv)
    if not _take_lock():
        print(f"Исполнитель уже запущен (процесс {lock_owner()}). Второй не нужен.", flush=True)
        return 1
    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(stop,), daemon=True).start()
    try:
        prj.recover_orphans()
        if args.once:
            run_tasks_once()
            run_projects_once()
            return 0
        threading.Thread(target=_tasks_loop, args=(stop,), daemon=True).start()
        cfg = load_config()
        print(f"[worker] модуль промптов: исполнитель по умолчанию {cfg.get('default_backend')}, "
              f"параллельно {cfg.get('parallel')}", flush=True)
        while True:
            try:
                if not run_projects_once():
                    time.sleep(2)
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[worker] сбой цикла: {e}", flush=True)
                time.sleep(5)
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()
        _release_lock()


if __name__ == "__main__":
    sys.exit(main())
