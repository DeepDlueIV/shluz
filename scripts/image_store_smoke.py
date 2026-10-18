"""CI-only: reproduce legacy vs containerd image IDs on two actual Docker daemons.

No owner volumes, user credentials or image execution. A tiny checksum-verified
Docker-save fixture is loaded on both engines; launcher verification is not mocked.
"""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, optional=False):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode and not optional:
        raise RuntimeError(f"CI-only command failed: {args[:3]}: {result.stderr[-2000:]}")
    return result.stdout if result.returncode == 0 else ""


def create_fixture(path, image):
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as archive:
        payload = b"Shluz harmless image-store regression fixture\n"
        entry = tarfile.TarInfo("marker")
        entry.size = len(payload)
        entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(payload))
    layer_bytes = layer.getvalue()
    layer_digest = hashlib.sha256(layer_bytes).hexdigest()
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {"Cmd": ["/not-executed"], "Labels": {"test": "shluz-identity"}},
            "rootfs": {"type": "layers", "diff_ids": ["sha256:" + layer_digest]},
            "history": [{"created_by": "CI fixture, never executed"}],
        }
    ).encode()
    config_digest = hashlib.sha256(config).hexdigest()
    entries = {
        config_digest + ".json": config,
        layer_digest + "/layer.tar": layer_bytes,
        "manifest.json": json.dumps(
            [
                {
                    "Config": config_digest + ".json",
                    "RepoTags": [image],
                    "Layers": [layer_digest + "/layer.tar"],
                }
            ]
        ).encode(),
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            archive.addfile(entry, io.BytesIO(payload))
    return "sha256:" + config_digest


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise RuntimeError("This test is restricted to isolated Linux CI runners")
    spec = importlib.util.spec_from_file_location("store_updater", ROOT / "launcher/updater.py")
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    daemons = []
    previous_tempdir = tempfile.tempdir
    try:
        run("docker", "pull", "docker:29-dind")
        with tempfile.TemporaryDirectory(prefix="shluz-stores-") as folder:
            directory = Path(folder)
            # Save output from the nested CLI must be readable at the same path on the host.
            tempfile.tempdir = str(directory)
            image = "shluz-preview:" + "a" * 40
            archive = directory / "image.tar.gz"
            config_id = create_fixture(archive, image)
            data = {
                "schema": 1,
                "channel": "preview",
                "commit": "a" * 40,
                "image": image,
                "image_id": config_id,
                "platform": "linux/amd64",
                "image_size": archive.stat().st_size,
                "image_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "image_url": updater.RELEASE_ROOT + "preview-" + "a" * 40 + "/image.tar.gz",
            }
            ids = {}
            for backend in ("legacy", "containerd"):
                name = "shluz-store-" + backend
                daemons.append(name)
                run(
                    "docker",
                    "run",
                    "--detach",
                    "--privileged",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--env",
                    "DOCKER_TLS_CERTDIR=",
                    "--mount",
                    f"type=bind,source={directory},target={directory}",
                    "docker:29-dind",
                    "--iptables=false",
                    "--ip6tables=false",
                    "--bridge=none",
                    "--features=containerd-snapshotter="
                    + ("true" if backend == "containerd" else "false"),
                )
                for _attempt in range(90):
                    if run(
                        "docker",
                        "exec",
                        name,
                        "docker",
                        "info",
                        "--format",
                        "{{.ID}}",
                        optional=True,
                    ):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError(
                        "Nested Docker failed: " + run("docker", "logs", name)[-3000:]
                    )

                def nested(*args, timeout=180, optional=False, daemon=name):
                    return run("docker", "exec", daemon, "docker", *args, optional=optional)

                updater.docker = nested
                updater.validate_manifest(data)
                local_id = updater.prepare_image(data, archive)
                assert updater.exported_config_digest(local_id) == config_id
                assert updater.prepare_image(data, archive) == local_id
                created = nested("create", "--network", "none", local_id).strip()
                resolved = json.loads(nested("inspect", created))[0]
                assert resolved["Image"] in {local_id, config_id}
                nested("rm", created)
                ids[backend] = local_id
                v2 = {**data, "schema": 2, "image_id": local_id, "image_config_digest": config_id}
                assert updater.prepare_image(v2, archive) == local_id
                corrupt = {**v2, "image_config_digest": "sha256:" + "0" * 64}
                try:
                    updater.prepare_image(corrupt, archive)
                except updater.UpdateError:
                    pass
                else:
                    raise AssertionError("Mismatched config must be rejected")
                print(f"PASS {backend}: local_id={local_id}; config={config_id}")
                run("docker", "rm", "--force", "--volumes", name)
            assert ids["legacy"] == config_id
            assert ids["containerd"] != config_id, "The old cross-store mismatch must be reproduced"
            print(
                "PASS: real cross-engine ID mismatch reproduced; "
                "config verification accepts identical content"
            )
    finally:
        tempfile.tempdir = previous_tempdir
        for name in daemons:
            run("docker", "rm", "--force", "--volumes", name, optional=True)


if __name__ == "__main__":
    main()
