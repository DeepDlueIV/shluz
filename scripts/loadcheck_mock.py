"""Нагрузочный мини-тест шлюза на mock-провайдере. Живой контейнер НЕ трогает.

Поднимает отдельный uvicorn-процесс (tmp SQLite, mock-провайдер) на 127.0.0.1:18099,
стреляет N параллельных /v1/chat/completions bootstrap-токеном, печатает p50/p95.

Запуск: python scripts/loadcheck_mock.py --n 20
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def _start_server(db_path: Path, port: int):
    """Отдельный процесс uvicorn: изоляция от живого шлюза полная."""
    code = (
        "import sys; sys.path.insert(0, r'{repo}');"
        "from fastapi.testclient import TestClient"  # не используется: см. ниже
    )
    del code  # TestClient не даёт реального HTTP — поднимаем uvicorn как в проде

    script = (
        "import sys;"
        f"sys.path.insert(0, r'{Path(__file__).resolve().parents[1]}');"
        "from pydantic import SecretStr;"
        "from app.config import Settings;"
        "from app.factory import create_app;"
        "import uvicorn;"
        "settings = Settings("
        "bootstrap_api_token=SecretStr('loadcheck'),"
        "admin_username='load',"
        "admin_password=SecretStr('loadcheck'),"
        f"database_url=SecretStr('sqlite+pysqlite:///{db_path.as_posix()}'));"
        "app = create_app(settings);"
        f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')"
    )
    # stdout/stderr ребёнка глушим: uvicorn-логи не должны смешиваться с отчётом.
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process


def _probe(port: int, results: list, index: int):
    body = json.dumps(
        {
            "model": "mock-chat",
            "messages": [{"role": "user", "content": f"load check {index}"}],
            "max_tokens": 16,
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=body,
        headers={"Authorization": "Bearer loadcheck", "Content-Type": "application/json"},
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
            results.append((time.monotonic() - start, response.status))
    except urllib.error.HTTPError as exc:
        exc.read()
        results.append((time.monotonic() - start, exc.code))
    except OSError:
        results.append((time.monotonic() - start, 0))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=20, help="число запросов (параллельно)")
    parser.add_argument("--port", type=int, default=18099)
    args = parser.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="loadcheck-"))
    try:
        process = _start_server(tmp / "loadcheck.db", args.port)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health", timeout=2)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                print("сервер не поднялся")
                return 1

            results: list = []
            threads = [
                threading.Thread(target=_probe, args=(args.port, results, index))
                for index in range(args.n)
            ]
            started = time.monotonic()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            wall = time.monotonic() - started

            latencies = sorted(duration for duration, _ in results)
            codes: dict[int, int] = {}
            for _, status in results:
                codes[status] = codes.get(status, 0) + 1

            def pct(percent):
                if not latencies:
                    return 0.0
                rank = min(len(latencies) - 1, round(len(latencies) * percent / 100))
                return latencies[rank]

            report = {
                "requests": args.n,
                "wall_seconds": round(wall, 2),
                "rps": round(args.n / wall, 1) if wall else 0,
                "p50_ms": round(pct(50) * 1000),
                "p95_ms": round(pct(95) * 1000),
                "max_ms": round(latencies[-1] * 1000) if latencies else 0,
                "codes": codes,
                "mean_ms": round(statistics.mean(latencies) * 1000) if latencies else 0,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if all(status == 200 for status in codes) else 2
        finally:
            process.terminate()
            process.wait(timeout=15)
            # Windows держит файл БД за ребёнком мгновение дольше — чистим по-возможности.
            time.sleep(1)
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        # Внешний try существует ради очистки tmp при любом исходе создания сервера.
        pass


if __name__ == "__main__":
    sys.exit(main())
