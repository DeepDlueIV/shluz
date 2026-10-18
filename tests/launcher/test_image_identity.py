"""A config digest survives Docker legacy/containerd storage changes; a display ID need not."""

import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def load():
    spec = importlib.util.spec_from_file_location("identity_updater", ROOT / "launcher/updater.py")
    u = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(u)
    return u


def fixture_archive(path, *, image=None, marker="checked", oci=False):
    payload = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {"Cmd": ["python"], "Labels": {"revision": marker}},
            "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "f" * 64]},
        }
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    config = "blobs/sha256/" + digest if oci else digest + ".json"
    entries = {
        "manifest.json": json.dumps(
            [
                {
                    "Config": config,
                    "RepoTags": [image] if image else None,
                    "Layers": [],
                }
            ]
        ).encode(),
        config: payload,
    }
    with tarfile.open(path, "w:gz") as t:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return "sha256:" + digest


def manifest(path, image, config, schema=2):
    sha = image.split(":")[-1]
    result = {
        "schema": schema,
        "channel": "preview",
        "commit": sha,
        "image": image,
        "image_id": config if schema == 1 else "sha256:" + "b" * 64,
        "platform": "linux/amd64",
        "image_size": path.stat().st_size,
        "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "image_url": f"https://github.com/DeepDlueIV/shluz/releases/download/preview-{sha}/image.tar.gz",
    }
    if schema == 2:
        result["image_config_digest"] = config
    return result


@pytest.mark.parametrize("oci", [False, True])
def test_archive_config_is_same_across_tar_layouts(tmp_path, oci):
    u = load()
    path = tmp_path / "image.tar.gz"
    image = "shluz-preview:" + "a" * 40
    config = fixture_archive(path, image=image, oci=oci)
    assert u.archive_config_digest(path, image) == config
    with pytest.raises(u.UpdateError):
        u.archive_config_digest(path, "wrong:tag")


@pytest.mark.parametrize("schema", [1, 2])
def test_containerd_display_id_can_differ_but_config_must_match(tmp_path, monkeypatch, schema):
    u = load()
    path = tmp_path / "image.tar.gz"
    image = "shluz-preview:" + "a" * 40
    config = fixture_archive(path, image=image)
    m = manifest(path, image, config, schema)
    actual_id = "sha256:" + "d" * 64
    exports = []
    monkeypatch.setattr(
        u,
        "docker",
        lambda *a, **kw: (
            json.dumps(
                [
                    {
                        "Id": actual_id,
                        "Os": "linux",
                        "Architecture": "amd64",
                    }
                ]
            )
            if a[:2] == ("image", "inspect")
            else ""
        ),
    )

    def export(identity):
        exports.append(identity)
        return config

    monkeypatch.setattr(u, "exported_config_digest", export, raising=False)
    assert u.prepare_image(m, path) == actual_id
    assert exports == [actual_id]


def test_substituted_loaded_config_is_rejected(tmp_path, monkeypatch):
    u = load()
    path = tmp_path / "image.tar.gz"
    image = "shluz-preview:" + "a" * 40
    config = fixture_archive(path, image=image)
    m = manifest(path, image, config)
    monkeypatch.setattr(
        u,
        "docker",
        lambda *a, **kw: (
            json.dumps(
                [
                    {
                        "Id": "sha256:" + "d" * 64,
                        "Os": "linux",
                        "Architecture": "amd64",
                    }
                ]
            )
            if a[:2] == ("image", "inspect")
            else ""
        ),
    )
    monkeypatch.setattr(u, "exported_config_digest", lambda i: "sha256:" + "e" * 64, raising=False)
    with pytest.raises(u.UpdateError, match="конфигурац"):
        u.prepare_image(m, path)


def test_archive_rejection_happens_before_docker(tmp_path, monkeypatch):
    u = load()
    path = tmp_path / "image.tar.gz"
    image = "shluz-preview:" + "a" * 40
    config = fixture_archive(path, image=image)
    m = manifest(path, image, config)
    m["image_config_digest"] = "sha256:" + "e" * 64
    monkeypatch.setattr(
        u, "docker", lambda *a, **kw: pytest.fail("No Docker before archive verification")
    )
    with pytest.raises(u.UpdateError):
        u.prepare_image(m, path)


def test_classic_display_id_matches_config_without_export(tmp_path, monkeypatch):
    u = load()
    path = tmp_path / "image.tar.gz"
    image = "shluz-preview:" + "a" * 40
    config = fixture_archive(path, image=image)
    m = manifest(path, image, config)
    monkeypatch.setattr(
        u,
        "docker",
        lambda *a, **kw: json.dumps(
            [
                {
                    "Id": config,
                    "Os": "linux",
                    "Architecture": "amd64",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        u, "exported_config_digest", lambda i: pytest.fail("No export needed"), raising=False
    )
    assert u.prepare_image(m, path) == config


def test_export_is_read_only_and_does_not_execute_image(tmp_path, monkeypatch):
    u = load()
    calls = []
    expected = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        assert args[:2] == ("image", "save")
        output = Path(args[args.index("--output") + 1])
        expected.append(fixture_archive(output, oci=True))
        return ""

    monkeypatch.setattr(u, "docker", fake_docker)
    assert u.exported_config_digest("sha256:" + "d" * 64) == expected[0]
    assert len(calls) == 1
    assert not Path(calls[0][calls[0].index("--output") + 1]).exists()


def test_v2_manifest_requires_explicit_config_digest(tmp_path):
    u = load()
    path = tmp_path / "image.tar.gz"
    image = "shluz-preview:" + "a" * 40
    config = fixture_archive(path, image=image)
    m = manifest(path, image, config)
    assert u.validate_manifest(m)["schema"] == 2
    for value in [None, "latest", "sha256:bad"]:
        m["image_config_digest"] = value
        with pytest.raises(u.UpdateError):
            u.validate_manifest(m)


@pytest.mark.skipif(__import__("os").name != "nt", reason="real cmd.exe test runs in Windows CI")
@pytest.mark.parametrize("code", [0, 1])
def test_windows_entrypoint_preserves_python_exit_without_false_prerequisite_error(tmp_path, code):
    import shutil

    folder = tmp_path / "Папка с пробелами"
    folder.mkdir()
    shutil.copyfile(ROOT / "launcher/Shluz.cmd", folder / "Shluz.cmd")
    (folder / "updater.py").write_text(
        f"print('INVOKED_ONCE'); raise SystemExit({code})", encoding="utf-8"
    )
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(folder / "Shluz.cmd")],
        input=b"\r\n",
        capture_output=True,
        timeout=30,
    )
    output = result.stdout.decode("utf-8", errors="replace")
    assert result.returncode == code
    assert output.count("INVOKED_ONCE") == 1
    assert "нужен Python" not in output
    assert "Docker Desktop" not in output
