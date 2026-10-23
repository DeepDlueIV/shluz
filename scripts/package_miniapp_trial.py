"""Immutable owner-test bundle; never changes the regular preview channel."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import package_preview


def main():
    commit = sys.argv[1]
    folder = Path("dist/miniapp-test")
    image = "shluz-preview:" + commit
    sys.argv = [sys.argv[0], commit, image, str(folder)]
    package_preview.main()
    manifest = json.loads((folder / "manifest.json").read_text())
    cloudflared = json.loads(
        subprocess.check_output(
            ["docker", "image", "inspect", "cloudflare/cloudflared:2026.10.3"], text=True
        )
    )[0]["RepoDigests"]
    manifest.update(
        channel="miniapp-test",
        image_url=f"https://github.com/DeepDlueIV/shluz/releases/download/miniapp-test-{commit}/image.tar.gz",
        cloudflared_image=next(v for v in cloudflared if v.startswith("cloudflare/cloudflared@")),
    )
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with zipfile.ZipFile(folder / "NoClip-MiniApp-Test.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name in ["Start-MiniApp.cmd", "Stop-MiniApp.cmd", "miniapp_trial.py", "updater.py"]:
            text = (Path("launcher") / name).read_text(encoding="utf-8")
            if name.endswith(".cmd"):
                text = text.replace("\n", "\r\n")
            z.writestr("NoClip-MiniApp-Test/" + name, text)
        z.writestr("NoClip-MiniApp-Test/trial-manifest.json", json.dumps(manifest, indent=2))
        z.write("docs/MINIAPP_OWNER_TEST.md", "NoClip-MiniApp-Test/README.md")
    (folder / "Shluz-launcher.zip").unlink()  # Не выдавать этот релиз за обычное обновление.
    (folder / "SHA256SUMS").unlink()
    (folder / "SHA256SUMS").write_text(
        "\n".join(f"{package_preview.digest(p)}  {p.name}" for p in sorted(folder.iterdir()))
        + "\n",
        encoding="utf-8",
    )
    print("Packaged isolated Mini App test:", commit)


if __name__ == "__main__":
    main()
