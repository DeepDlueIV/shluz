"""Узкий website bridge: существующий guest Gateway и защищённый ngrok, без Telegram."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from contextlib import suppress
from pathlib import Path

import updater

SITE_URL = "https://noclip-web.vercel.app"
CONTAINER = "noclip-site-gateway"
LABEL = "io.noclip.site-bridge"
IMAGE = "sha256:d681c163ed411b2e82df6e4d67a83faf44019253da9fb5c938b997fcff6bd50b"
REVISION = "08145402383f681f2843e325f054af3d50dac1e0"
GUEST_PATHS = {"/v1/web/session": "post", "/v1/web/chat/completions": "post", "/v1/web/me": "get"}


class BridgeError(RuntimeError):
    pass


def docker(*args, **kwargs):
    return updater.docker(*args, **kwargs)


def settings(root):
    root = Path(root).resolve()
    try:
        data = json.loads((root / "config" / "site-bridge.json").read_text(encoding="utf-8-sig"))
        port = data["port"]
        config = Path(data["configPath"]).resolve()
        policy = Path(data["trafficPolicy"]).resolve()
        if (
            type(port) is not int
            or not 1024 <= port <= 65535
            or config != root / "config" / "site-bridge" / "ngrok.yml"
            or policy != root / "config" / "site-bridge" / "traffic-policy.yml"
        ):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise BridgeError(
            "Настройки сайта отсутствуют или некорректны; конфигурация не изменена."
        ) from exc
    return {
        "root": root,
        "port": port,
        "config": config,
        "policy": policy,
        "runtime": config.parent,
        "upstream": f"http://127.0.0.1:{port}",
    }


def inspect_gateway():
    raw = docker("inspect", CONTAINER, optional=True, timeout=10)
    return json.loads(raw)[0] if raw else None


def validate_gateway(item, port):
    if not item:
        raise BridgeError("Гостевой Gateway не найден. Новая установка автоматически не создаётся.")
    labels = item.get("Config", {}).get("Labels") or {}
    bindings = item.get("HostConfig", {}).get("PortBindings") or {}
    expected = [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
    if (
        labels.get(LABEL) != "1"
        or labels.get("org.opencontainers.image.revision") != REVISION
        or item.get("Image") != IMAGE
        or bindings.get("8000/tcp") != expected
        or set(bindings) != {"8000/tcp"}
    ):
        raise BridgeError(
            "Контейнер сайта не прошёл проверку владельца, образа или loopback-порта."
        )
    return item


def powershell_json(script):
    executable = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    # Windows PowerShell не должен импортировать несовместимые модули родительского pwsh.
    command = (
        "$ErrorActionPreference='Stop';$env:PSModulePath=Join-Path $PSHOME 'Modules';" + script
    )
    result = subprocess.run(
        [str(executable), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise BridgeError(
            "Не удалось прочитать Windows-метаданные ngrok; чужие процессы не затронуты."
        )
    return json.loads(result.stdout.strip() or "null")


def resolve_ngrok():
    launch = shutil.which("ngrok")
    if not launch:
        raise BridgeError("ngrok не установлен или недоступен в PATH.")
    installed = powershell_json(
        "$p=Get-AppxPackage -Name ngrok.ngrok;"
        "if($p){(Join-Path $p.InstallLocation 'ngrok.exe')|ConvertTo-Json}"
    )
    return launch, str(installed or Path(launch).resolve())


def windows_args(command):
    if os.name != "nt":
        import shlex

        return [part.strip('"') for part in shlex.split(command, posix=False)]
    argc = ctypes.c_int()
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    result = shell.CommandLineToArgvW(command, ctypes.byref(argc))
    if not result:
        return []
    try:
        return [result[index] for index in range(argc.value)]
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(ctypes.cast(result, ctypes.c_void_p))


def agents():
    data = (
        powershell_json(
            "@(Get-CimInstance Win32_Process -Filter \"Name='ngrok.exe'\" |"
            "Select-Object ProcessId,ExecutablePath,CommandLine)|ConvertTo-Json -Compress"
        )
        or []
    )
    if isinstance(data, dict):
        data = [data]
    return [
        {
            "pid": item["ProcessId"],
            "executable": item.get("ExecutablePath") or "",
            "args": windows_args(item.get("CommandLine") or ""),
        }
        for item in data
    ]


def option(args, key):
    if key in args and args.index(key) + 1 < len(args):
        return args[args.index(key) + 1]
    return next((arg[len(key) + 1 :] for arg in args if arg.startswith(key + "=")), "")


def owned_agent(item, cfg, executable):
    args = item["args"]

    def normalize(path):
        return os.path.normcase(os.path.normpath(str(path)))

    return (
        normalize(item["executable"]) == normalize(executable)
        and "http" in args
        and cfg["upstream"] in args
        and normalize(option(args, "--config")) == normalize(cfg["config"])
        and normalize(option(args, "--traffic-policy-file")) == normalize(cfg["policy"])
        and option(args, "--inspect") == "false"
    )


def local_tunnels():
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3) as response:
            data = json.load(response)
        if not isinstance(data.get("tunnels"), list):
            raise ValueError
        return data["tunnels"]
    except urllib.error.HTTPError as exc:
        raise BridgeError("Локальный порт 4040 занят другим HTTP-сервисом.") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError) or (
            isinstance(exc.reason, OSError) and exc.reason.errno in (errno.ECONNREFUSED, 10061)
        ):
            return None
        raise BridgeError("Не удалось проверить занятость ngrok-порта 4040.") from exc
    except TimeoutError as exc:
        raise BridgeError("Локальный порт 4040 не ответил; повторный агент не запущен.") from exc
    except (ValueError, TypeError, AttributeError) as exc:
        raise BridgeError("Локальный порт 4040 занят неизвестным сервисом.") from exc


def wait_gateway(cfg, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(cfg["upstream"] + "/health", timeout=3) as response:
                assert json.load(response).get("status") == "ok"
            with urllib.request.urlopen(cfg["upstream"] + "/openapi.json", timeout=3) as response:
                paths = json.load(response).get("paths", {})
            if not all(method in paths.get(path, {}) for path, method in GUEST_PATHS.items()):
                raise BridgeError("Gateway не реализует необходимые гостевые маршруты.")
            return
        except (OSError, ValueError, AssertionError):
            time.sleep(1)
    raise BridgeError("Гостевой Gateway не ответил; публичный доступ не включён.")


def prepare_policy(cfg):
    if not cfg["config"].is_file() or not (cfg["runtime"] / "bridge-password.txt").is_file():
        raise BridgeError(
            "Локальные credentials ngrok/bridge не настроены; новые секреты не создаются."
        )
    executable = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    result = subprocess.run(
        [
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(cfg["root"] / "web" / "tunnel" / "start-windows.ps1"),
            "-GatewayPort",
            str(cfg["port"]),
            "-RuntimeDirectory",
            str(cfg["runtime"]),
            "-NgrokConfig",
            str(cfg["config"]),
            "-PrepareOnly",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise BridgeError("Защитная policy не прошла подготовку; туннель не запущен.")


def spawn_agent(executable, cfg):
    cfg["runtime"].mkdir(parents=True, exist_ok=True)
    with (cfg["runtime"] / "agent.log").open("ab") as log:
        env = dict(os.environ)
        for name in ("NGROK_AUTHTOKEN", "NGROK_API_KEY"):
            env.pop(name, None)
        process = subprocess.Popen(
            [
                executable,
                "http",
                cfg["upstream"],
                "--config",
                str(cfg["config"]),
                "--traffic-policy-file",
                str(cfg["policy"]),
                "--inspect=false",
                "--log=stdout",
                "--log-format=json",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=0x08000000 | 0x00000200,
            close_fds=True,
        )
    return process.pid


def wait_agent(cfg, executable, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        owned = [item for item in agents() if owned_agent(item, cfg, executable)]
        tunnels = local_tunnels() or []
        for tunnel in tunnels:
            target = tunnel.get("config", {})
            url = tunnel.get("public_url", "")
            parsed = urllib.parse.urlsplit(url)
            if (
                len(owned) == 1
                and target.get("addr") == cfg["upstream"]
                and target.get("inspect") is False
                and parsed.scheme == "https"
                and parsed.hostname
                and "." in parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.port
                and parsed.path in ("", "/")
                and not parsed.query
                and not parsed.fragment
            ):
                return owned[0]["pid"], url
        time.sleep(1)
    raise BridgeError(
        "ngrok не подтвердил защищённый HTTPS; подробности остаются в локальном agent.log."
    )


def wait_stopped(pid, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(entry["pid"] == pid for entry in agents()):
            return
        time.sleep(0.2)
    raise BridgeError("Прежний website-агент ещё завершается; второй агент не запущен.")


def start(root):
    cfg = settings(root)
    item = validate_gateway(inspect_gateway(), cfg["port"])
    launch, executable = resolve_ngrok()
    owned = [entry for entry in agents() if owned_agent(entry, cfg, executable)]
    tunnels = local_tunnels()
    if len(owned) > 1:
        raise BridgeError("Найдено несколько website-агентов; повторный запуск отменён.")
    if not owned and tunnels is not None:
        raise BridgeError("Порт ngrok 4040 занят другим агентом; ничего не остановлено.")
    # Аргументы старого процесса не доказывают содержимое загруженной policy.
    # Закрываем только подтверждённый собственный агент до подготовки новых файлов.
    for entry in owned:
        with suppress(ProcessLookupError):
            os.kill(entry["pid"], signal.SIGTERM)
        wait_stopped(entry["pid"])
    prepare_policy(cfg)
    if not item["State"]["Running"]:
        docker("start", item["Id"], timeout=20)
    wait_gateway(cfg)
    spawn_agent(launch, cfg)
    pid, url = wait_agent(cfg, executable)
    updater.write_json(
        cfg["runtime"] / "state.json",
        {
            "schema": 1,
            "pid": pid,
            "https_url": url,
            "container": CONTAINER,
            "config_path": str(cfg["config"]),
            "traffic_policy": str(cfg["policy"]),
            "gateway_port": cfg["port"],
        },
    )
    print("Website Gateway и защищённый HTTPS запущены.")
    print("Сайт NoClip:", SITE_URL)
    print("Технический HTTPS-туннель:", url)
    print("Vercel и публичный гостевой режим автоматически не изменялись.")


def stop(root):
    cfg = settings(root)
    agent_error = None
    try:
        _, executable = resolve_ngrok()
        for entry in agents():
            if owned_agent(entry, cfg, executable):
                with suppress(ProcessLookupError):
                    os.kill(entry["pid"], signal.SIGTERM)
    except (BridgeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        agent_error = exc
    # Отсутствие агента/повреждение его state не мешает закрыть собственный Gateway.
    item = inspect_gateway()
    if item:
        validate_gateway(item, cfg["port"])
        if item["State"]["Running"]:
            docker("stop", "--timeout", "15", item["Id"], timeout=25)
    updater.write_json(
        cfg["runtime"] / "state.json",
        {"schema": 1, "phase": "partial_stop" if agent_error else "stopped"},
    )
    if agent_error:
        raise BridgeError(
            "Gateway сайта остановлен; остановку ngrok подтвердить не удалось."
        ) from agent_error
    print("Website-туннель и его Gateway остановлены. Telegram и данные не затронуты.")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(os.environ.get("SHLUZ_HOME", Path(__file__).resolve().parents[1]))
    arguments = sys.argv[1:] if argv is None else argv
    try:
        cfg = settings(root)
        with updater.update_lock(cfg["runtime"]):
            operation = arguments[0] if arguments else "start"
            if operation == "start":
                start(root)
            elif operation == "stop":
                stop(root)
            else:
                raise BridgeError("Допустимы только start и stop.")
        if not arguments:
            # Браузер открывается только после подтверждённого успешного запуска.
            with suppress(OSError, webbrowser.Error):
                webbrowser.open(SITE_URL)
        return 0
    except BridgeError as exc:
        print("Website не готов:", str(exc))
        return 1
    except (updater.UpdateError, OSError, ValueError, subprocess.TimeoutExpired):
        print("Website не готов: проверьте локальную конфигурацию или передайте окно агенту.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
