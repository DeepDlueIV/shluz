"""Настоящий HTTP-сервер с временной SQLite для повторяемой приёмки."""

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


class GatewayProcess:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.database_url = f'sqlite+pysqlite:///{directory / "acceptance.sqlite"}'
        self.auth = ('check-owner', secrets.token_urlsafe(24))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}'
        self.env = {
            **os.environ, 'PYTHONPATH': str(ROOT), 'SHLUZ_DATABASE_URL': self.database_url,
            'SHLUZ_ADMIN_USERNAME': self.auth[0], 'SHLUZ_ADMIN_PASSWORD': self.auth[1],
            'SHLUZ_BOOTSTRAP_API_TOKEN': secrets.token_urlsafe(32),
            'SHLUZ_ACTIVE_PROVIDER': 'mock', 'SHLUZ_VENICE_API_KEY': '',
            'SHLUZ_ENVIRONMENT': 'owner-check',
        }
        self.process = self.log = None

    def migrate(self):
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                       cwd=ROOT, env=self.env, capture_output=True, timeout=30, check=True)

    def start(self):
        self.log = (self.directory / 'server.log').open('a', encoding='utf-8')
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
             '--port', str(self.port)], cwd=ROOT, env=self.env,
            stdout=self.log, stderr=subprocess.STDOUT,
        )
        with httpx.Client(timeout=1, trust_env=False) as client:
            for _ in range(100):
                if self.process.poll() is not None:
                    self.stop()
                    raise RuntimeError('Тестовый сервер завершился. См. временный server.log.')
                try:
                    if client.get(self.url + '/health').status_code == 200:
                        return self
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
        self.stop()
        raise RuntimeError('Тестовый HTTP-сервер не запустился')

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None

    def restart(self):
        self.stop()
        return self.start()
