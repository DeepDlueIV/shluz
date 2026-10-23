"""Пакет единого лаунчера: только исходный код и pinned owner-trial manifest.

Запускается в CI. Никаких локальных config, env, backup или Docker volumes.
"""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "launcher"
PINNED_MINIAPP_COMMIT = "fbaeb43b58c393d48d6000393f388e26991ecf34"
INCLUDED = ("NoClip.cmd", "owner_control.py", "updater.py", "miniapp_trial.py", "PORTABLE.md")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def build(target, manifest_path):
    sys.path.insert(0, str(LAUNCHER))
    import miniapp_trial

    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    miniapp_trial.validate_manifest(manifest)
    if manifest["commit"] != PINNED_MINIAPP_COMMIT:
        raise ValueError("MiniApp release is not the reviewed pinned build")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    checks = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in INCLUDED:
            blob = (LAUNCHER / name).read_bytes()
            if name.endswith(".cmd"):
                blob = blob.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            output_name = "README.md" if name == "PORTABLE.md" else name
            archive.writestr(output_name, blob)
            checks.append((output_name, sha256(blob)))
        blob = (json.dumps(manifest, indent=2) + "\n").encode()
        archive.writestr("trial-manifest.json", blob)
        checks.append(("trial-manifest.json", sha256(blob)))
    with zipfile.ZipFile(target) as archive:
        if archive.testzip():
            raise ValueError("Portable archive failed integrity check")
        if set(archive.namelist()) != {name for name, _ in checks}:
            raise ValueError("Portable archive entries mismatch")
    checksum = target.with_suffix(".sha256")
    checksum.write_text(sha256(target.read_bytes()) + "  " + target.name + "\n", encoding="utf-8")
    return checks


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: package_owner_control.py output.zip pinned-manifest.json")
    entries = build(sys.argv[1], sys.argv[2])
    print("Portable bundle verified: " + str(len(entries)) + " files, no user data")
