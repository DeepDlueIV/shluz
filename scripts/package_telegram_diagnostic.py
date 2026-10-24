"""Build isolated, read-only Windows Telegram diagnostics ZIP; no user data."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "diagnostics/Diagnose-NoClip.cmd": "Diagnose-NoClip.cmd",
    "diagnostics/telegram_menu.py": "telegram_menu.py",
    "diagnostics/README.txt": "README.txt",
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def build(target, revision):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Expected exact reviewed Git SHA")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    entries = {}
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for original, outname in FILES.items():
            blob = (ROOT / original).read_bytes()
            if original.endswith(".cmd"):
                blob = blob.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            if not 0 < len(blob) < 2**19:
                raise ValueError("Unexpected diagnostic file size")
            archive.writestr(outname, blob)
            entries[outname] = sha256(blob)
        manifest = {
            "name": "noclip-telegram-menu-readonly-diagnostic",
            "commit": revision,
            "files": entries,
            "may_modify_host_or_telegram": False,
        }
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP failed CRC")
        if set(archive.namelist()) != {*entries, "manifest.json"}:
            raise ValueError("Unexpected archive members")
        if any(sha256(archive.read(n)) != h for n, h in entries.items()):
            raise ValueError("ZIP failed content verification")
    checksum = target.with_suffix(".sha256")
    checksum.write_text(
        sha256(target.read_bytes()) + "  " + target.name + "\n",
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: package_telegram_diagnostic.py <zip> <git-sha>")
    build(sys.argv[1], sys.argv[2])
    print("PASS: portable read-only Telegram menu diagnostic ZIP, no secrets")
