"""Package an immutable one-click code update for the existing Windows installation.

Source files ONLY; no owner config, volumes, credentials, or runtime state.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))
import bootstrap  # noqa: E402


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def build(target, revision):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("A 40-character reviewed Git SHA is required")
    expected_gateway = f'PREVIEW_COMMIT = "{bootstrap.GATEWAY_SHA}"'
    live_code = (ROOT / "launcher" / "miniapp_live.py").read_text(encoding="utf-8")
    if expected_gateway not in live_code:
        raise ValueError("Gateway pin changed: this code-only updater cannot migrate a DB")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": 1,
        "product": bootstrap.PRODUCT,
        "revision": revision,
        "gateway_revision": bootstrap.GATEWAY_SHA,
        "mode": "launcher-code-only",
        "files": {},
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bootstrap.FILES):
            file = ROOT / path
            if not file.is_file() or file.is_symlink():
                raise ValueError("Missing or linked runtime file: " + path)
            data = file.read_bytes()
            if path.endswith(".cmd"):
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            if not data or len(data) > 2 * 1024 * 1024:
                raise ValueError("Invalid runtime source size")
            manifest["files"][path] = checksum(data)
            archive.writestr("payload/" + path, data)
        for path, archive_name in (
            ("installer/Install-NoClip.cmd", "Install-NoClip.cmd"),
            ("installer/bootstrap.py", "bootstrap.py"),
            ("installer/README.txt", "README.txt"),
        ):
            data = (ROOT / path).read_bytes()
            if path.endswith(".cmd"):
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            archive.writestr(archive_name, data)
        archive.writestr(
            "package-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    with zipfile.ZipFile(target) as archive:
        if archive.testzip():
            raise ValueError("ZIP integrity check failed")
        expected_paths = {"payload/" + s for s in bootstrap.FILES} | {
            "Install-NoClip.cmd", "bootstrap.py", "README.txt", "package-manifest.json"
        }
        if set(archive.namelist()) != expected_paths:
            raise ValueError("Unexpected ZIP contents")
    checksum_file = target.with_suffix(".sha256")
    checksum_file.write_text(
        checksum(target.read_bytes()) + "  " + target.name + "\n", encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: package_oneclick_update.py <output.zip> <reviewed-git-sha>")
    build(sys.argv[1], sys.argv[2])
    print("PASS: one-click local update ZIP, pinned source, no user data")
