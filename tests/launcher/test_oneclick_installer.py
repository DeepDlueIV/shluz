"""Offline safety and file-integrity checks for the Windows one-click update flow."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[2] / "installer"
if str(INSTALLER) not in sys.path:
    sys.path.insert(0, str(INSTALLER))

import bootstrap  # noqa: E402


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def setup_case(tmp_path, revision="a" * 40, tag="new"):
    source = tmp_path / "Download" / "NoClip"
    payload = source / "payload"
    root = tmp_path / "Existing" / "Shluz"
    payload.mkdir(parents=True)
    (root / "launcher").mkdir(parents=True)
    data = {
        name: (tag + ":" + name + "\n").encode("utf-8")
        for name in bootstrap.FILES
    }
    for name, blob in data.items():
        file = payload / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(blob)
        old = root / name
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(("old:" + name + "\n").encode())
    manifest = {
        "schema": 1,
        "product": bootstrap.PRODUCT,
        "revision": revision,
        "gateway_revision": bootstrap.GATEWAY_SHA,
        "mode": "launcher-code-only",
        "files": {name: checksum(blob) for name, blob in data.items()},
    }
    (source / "package-manifest.json").write_text(json.dumps(manifest))
    (root / "config").mkdir()
    (root / "config" / ".env.owner").write_text(
        "VENICE_API_KEY=KEEP_THIS_LOCAL\n"
    )
    (root / "backups").mkdir()
    (root / "backups" / "history.sqlite").write_bytes(b"keep user data")
    return source, root, manifest, data


def install(source, root, *, events=None, fail=False):
    events = events if events is not None else []

    def launched(_):
        events.append("launch")
        if fail:
            raise bootstrap.InstallError("Synthetic bad local start")

    return bootstrap.install(
        source, root, docker_check=lambda _: events.append("docker"),
        launch=launched,
    )


def test_one_click_installs_from_arbitrary_directory_without_touching_data(tmp_path):
    source, root, manifest, data = setup_case(tmp_path)
    assert install(source, root) == 0
    for name in data:
        assert (root / name).read_bytes() == data[name]
    assert (root / "config" / ".env.owner").read_text() == (
        "VENICE_API_KEY=KEEP_THIS_LOCAL\n"
    )
    assert (root / "backups" / "history.sqlite").read_bytes() == b"keep user data"
    state = bootstrap.load_state(root)
    assert state["revision"] == manifest["revision"]
    assert not (root / bootstrap.CONTROL / "journal.json").exists()


def test_idempotent_second_run_keeps_one_history_snapshot(tmp_path):
    source, root, _, _ = setup_case(tmp_path)
    events = []
    install(source, root, events=events)
    history = root / bootstrap.CONTROL / "history"
    assert len(list(history.iterdir())) == 1
    install(source, root, events=events)
    assert events == ["docker", "launch", "docker", "launch"]
    assert len(list(history.iterdir())) == 1


def test_sha256_mismatch_is_rejected_before_touching_existing_code(tmp_path):
    source, root, _, _ = setup_case(tmp_path)
    existing = (root / "NoClip.cmd").read_bytes()
    (source / "payload" / "NoClip.cmd").write_bytes(b"tampered")
    with pytest.raises(bootstrap.InstallError, match="SHA-256"):
        install(source, root)
    assert (root / "NoClip.cmd").read_bytes() == existing


def test_archived_secret_or_unlisted_file_cannot_enter_working_install(tmp_path):
    source, root, _, _ = setup_case(tmp_path)
    manifest_file = source / "package-manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["files"]["config/.env.owner"] = "a" * 64
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(bootstrap.InstallError, match="Manifest"):
        install(source, root)
    assert (root / "config" / ".env.owner").read_text().startswith("VENICE_API_KEY=")


def test_local_codex_edits_are_not_overwritten(tmp_path):
    source, root, _, _ = setup_case(tmp_path)
    install(source, root)
    edit = root / "launcher" / "miniapp_live.py"
    edit.write_text("local uncommitted patch\n")
    with pytest.raises(bootstrap.InstallError, match="локальные изменения"):
        install(source, root)
    assert edit.read_text() == "local uncommitted patch\n"


def test_launch_failure_restores_all_previous_code_and_prior_marker(tmp_path):
    source, root, manifest, _ = setup_case(tmp_path)
    old = {n: (root / n).read_bytes() for n in bootstrap.FILES}
    with pytest.raises(bootstrap.InstallError, match="Synthetic"):
        install(source, root, fail=True)
    for name in old:
        assert (root / name).read_bytes() == old[name]
    assert bootstrap.load_state(root) is None
    assert not (root / bootstrap.CONTROL / "journal.json").exists()


def test_mid_copy_error_restores_old_program(tmp_path, monkeypatch):
    source, root, _, data = setup_case(tmp_path)
    original = {n: (root / n).read_bytes() for n in bootstrap.FILES}

    def partially_write(target, src, manifest):
        first = sorted(manifest["files"])[0]
        (target / first).write_bytes(data[first])
        raise bootstrap.InstallError("Simulated disk write failure")

    monkeypatch.setattr(bootstrap, "write_candidate", partially_write)
    with pytest.raises(bootstrap.InstallError, match="Simulated"):
        install(source, root)
    assert all((root / n).read_bytes() == val for n, val in original.items())


def test_interrupted_update_restores_old_files_on_next_run(tmp_path):
    source, root, manifest, data = setup_case(tmp_path)
    ctl = root / bootstrap.CONTROL
    op = "b" * 32
    work = ctl / "work" / op
    work.mkdir(parents=True)
    prior = {n: (root / n).read_bytes() for n in bootstrap.FILES}
    bootstrap.take_snapshot(root, work, manifest, None)
    bootstrap.put_json(ctl / "journal.json", {"operation": op})
    first = sorted(data)[0]
    (root / first).write_bytes(data[first])
    bootstrap.recover_interrupted(root)
    assert not (ctl / "journal.json").exists()
    assert all((root / n).read_bytes() == val for n, val in prior.items())


def test_committed_marker_survives_crash_during_cleanup(tmp_path):
    source, root, manifest, data = setup_case(tmp_path)
    ctl = root / bootstrap.CONTROL
    op = "c" * 32
    work = ctl / "work" / op
    work.mkdir(parents=True)
    bootstrap.take_snapshot(root, work, manifest, None)
    bootstrap.put_json(ctl / "journal.json", {"operation": op})
    for name, blob in data.items():
        (root / name).write_bytes(blob)
    bootstrap.put_json(
        ctl / "current.json", {
            "product": bootstrap.PRODUCT,
            "revision": manifest["revision"],
            "files": manifest["files"],
        },
    )
    bootstrap.recover_interrupted(root)
    assert all((root / n).read_bytes() == val for n, val in data.items())
    assert not (ctl / "journal.json").exists()


def test_housekeeping_keeps_two_verified_owned_code_snapshots(tmp_path):
    source, root, manifest, _ = setup_case(tmp_path)
    install(source, root)
    for index, letter in enumerate("bcd"):
        next_rev = letter * 40
        tag = "release" + str(index)
        manifest["revision"] = next_rev
        for name in bootstrap.FILES:
            blob = (tag + name).encode("utf-8")
            (source / "payload" / name).write_bytes(blob)
            manifest["files"][name] = checksum(blob)
        (source / "package-manifest.json").write_text(json.dumps(manifest))
        install(source, root)
    history = root / bootstrap.CONTROL / "history"
    assert len(list(history.iterdir())) == 2
    assert (root / "backups" / "history.sqlite").exists()
    assert (root / "config" / ".env.owner").exists()


def test_symlink_in_download_or_runtime_is_rejected(tmp_path):
    source, root, _, _ = setup_case(tmp_path)
    file = source / "payload" / "NoClip.cmd"
    file.unlink()
    external = tmp_path / "external"
    external.write_text("some data")
    try:
        file.symlink_to(external)
    except OSError:
        pytest.skip("Creating symlinks requires privileges on this Windows runner")
    with pytest.raises(bootstrap.InstallError, match="symlink"):
        install(source, root)


def test_empty_folder_or_git_checkout_refused_without_any_change(tmp_path):
    source, root, _, _ = setup_case(tmp_path)
    other = tmp_path / "empty"
    other.mkdir()
    with pytest.raises(bootstrap.InstallError, match="рабочая установка|прежняя"):
        install(source, other)
    (root / ".git").mkdir()
    with pytest.raises(bootstrap.InstallError, match="рабочая установка"):
        install(source, root)


def test_real_packer_contains_only_named_files_no_runtime_secrets(tmp_path):
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    import package_oneclick_update
    revision = "f" * 40
    dest = tmp_path / "NoClip-OneClick.zip"
    package_oneclick_update.build(dest, revision)
    with zipfile.ZipFile(dest) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert names == {
            "Install-NoClip.cmd", "bootstrap.py",
            "README.txt", "package-manifest.json",
        } | {"payload/" + s for s in bootstrap.FILES}
        assert not any("/config/" in p or "/backups/" in p for p in names)
        target = tmp_path / "ArchiveExtract"
        archive.extractall(target)
    assert bootstrap.read_package(target)["revision"] == revision


def test_docker_preflight_uses_read_only_inspection_and_expected_volumes(
    tmp_path, monkeypatch
):
    source, root, _, _ = setup_case(tmp_path)
    (root / "runtime.json").write_text(json.dumps({
        "project": bootstrap.PROJECT,
        "volumes": {"gateway": "gw-data", "bot": "tg-data"},
    }))
    commands = []

    def fake(*args):
        commands.append(args)
        if args[0] == "info":
            return "linux/amd64"
        if args[0] == "ps":
            return "gateway-id" if "shluz" in args[-1] else "bot-id"
        if args[0] == "inspect":
            service = "shluz" if args[-1] == "gateway-id" else "telegram_bot"
            return json.dumps([{
                "Config": {"Labels": {
                    "com.docker.compose.project": bootstrap.PROJECT,
                    "com.docker.compose.service": service,
                }},
                "Mounts": [{
                    "Destination": "/app/data", "Type": "volume",
                    "Name": "gw-data" if service == "shluz" else "tg-data",
                }],
            }])
        raise AssertionError(args)

    monkeypatch.setattr(bootstrap, "docker_command", fake)
    assert bootstrap.docker_preflight(root)
    assert all(args[0] in {"info", "ps", "inspect"} for args in commands)
    state = json.loads((root / "runtime.json").read_text())
    state["volumes"]["bot"] = "wrong"
    (root / "runtime.json").write_text(json.dumps(state))
    with pytest.raises(bootstrap.InstallError, match="volume"):
        bootstrap.docker_preflight(root)

@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd.exe only")
def test_real_windows_batch_handles_spaces_and_unicode_path(tmp_path):
    import shutil
    import subprocess

    folder = tmp_path / "Моя папка NoClip" / "новая версия"
    folder.mkdir(parents=True)
    shutil.copy2(INSTALLER / "Install-NoClip.cmd", folder / "Install-NoClip.cmd")
    (folder / "bootstrap.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('batch-pass.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(folder / "Install-NoClip.cmd")],
        capture_output=True, timeout=30, check=False,
    )
    assert completed.returncode == 0
    assert (folder / "batch-pass.txt").read_text() == "ok"
