"""Package one immutable, already tested preview revision. No user settings included."""

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    commit, image, folder = sys.argv[1:4]
    if not re.fullmatch("[a-f0-9]{40}", commit) or image != "shluz-preview:" + commit:
        raise ValueError("Invalid pinned revision")
    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)
    # Save prebuilt layers, rather than rebuilding from source on the owner's PC.
    saved = target / "image.tar.gz"
    with saved.open("wb") as output:
        save = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
        gzip = subprocess.run(["gzip", "-1"], stdin=save.stdout, stdout=output, check=False)
        save.stdout.close()
        if save.wait() or gzip.returncode:
            raise RuntimeError("Docker archive failed")
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
    ).strip()
    manifest = {
        "schema": 1,
        "channel": "preview",
        "commit": commit,
        "image": image,
        "image_id": image_id,
        "platform": "linux/amd64",
        "image_size": saved.stat().st_size,
        "image_sha256": digest(saved),
        "image_url": f"https://github.com/DeepDlueIV/shluz/releases/download/preview-{commit}/image.tar.gz",
    }
    if saved.stat().st_size >= 2 * 1024**3:
        raise RuntimeError("Image exceeds release-asset limit")
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            "--prefix=shluz/",
            commit,
            "-o",
            str(target / "source.zip"),
        ],
        check=True,
    )
    with zipfile.ZipFile(target / "Shluz-launcher.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for file in ["Shluz.cmd", "updater.py", "README.md"]:
            z.write(Path("launcher") / file, "Shluz/" + file)
    files = sorted(target.iterdir())
    (target / "SHA256SUMS").write_text(
        "\n".join(f"{digest(path)}  {path.name}" for path in files) + "\n", encoding="utf-8"
    )
    print("Packaged checked image, source, launcher and pinned manifest:", commit)


if __name__ == "__main__":
    main()
