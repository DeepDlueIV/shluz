"""Publish checked previews only from trusted pushes. Uses runner's GH_TOKEN, not user keys."""

import json
import os
import re
import subprocess
from pathlib import Path

REPO = "DeepDlueIV/shluz"


def gh(*args, optional=False):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode and not optional:
        raise RuntimeError("GitHub release operation failed: " + result.stderr[-2000:])
    return result.stdout if result.returncode == 0 else ""


def main():
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPO
        or os.environ.get("GITHUB_EVENT_NAME") != "push"
        or os.environ.get("GITHUB_REF")
        not in {"refs/heads/main", "refs/heads/implementation/telegram-beta-complete"}
    ):
        raise RuntimeError("Release publication is restricted to trusted repository pushes")
    commit = os.environ["GITHUB_SHA"]
    if not re.fullmatch("[a-f0-9]{40}", commit):
        raise RuntimeError("Invalid commit")
    folder = Path("dist/preview")
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest["commit"] != commit:
        raise RuntimeError("Package revision mismatch")
    # Do not let a superseded run overwrite the preview channel.
    branch = os.environ["GITHUB_REF"].removeprefix("refs/heads/")
    current = json.loads(gh("api", f"repos/{REPO}/git/ref/heads/{branch}"))["object"]["sha"]
    if current != commit:
        print("A newer revision exists: skip publishing this superseded preview")
        return
    tag = "preview-" + commit
    existing = gh("release", "view", tag, "-R", REPO, "--json", "id", optional=True)
    if not existing:
        assets = [str(path) for path in sorted(folder.iterdir())]
        gh(
            "release",
            "create",
            tag,
            "-R",
            REPO,
            "--target",
            commit,
            "--prerelease",
            "--title",
            "Shluz preview " + commit[:8],
            "--notes",
            "Проверенная локальная версия: исходники, Docker-образ и запускатель. "
            "Не включает настройки, ключи и базы владельца.",
            *assets,
        )
    else:
        # Immutable releases are not overwritten on reruns.
        saved = gh("api", f"repos/{REPO}/releases/tags/{tag}")
        names = {asset["name"] for asset in json.loads(saved)["assets"]}
        if not {"image.tar.gz", "manifest.json", "source.zip", "Shluz-launcher.zip"} <= names:
            raise RuntimeError("Incomplete immutable release: do not advance preview channel")
        # Reuse the checksum/image manifest already published, not a newly rebuilt image.
        with_manifest = folder / "previous"
        with_manifest.mkdir(exist_ok=True)
        gh(
            "release",
            "download",
            tag,
            "-R",
            REPO,
            "--pattern",
            "manifest.json",
            "--dir",
            str(with_manifest),
            "--clobber",
        )
        manifest = json.loads((with_manifest / "manifest.json").read_text())
        if manifest["commit"] != commit:
            raise RuntimeError("Existing release contains a different commit")
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    channel = gh("release", "view", "preview", "-R", REPO, "--json", "id", optional=True)
    if not channel:
        gh(
            "release",
            "create",
            "preview",
            "-R",
            REPO,
            "--target",
            commit,
            "--prerelease",
            "--title",
            "Shluz — версия для проверки",
            "--notes",
            "Запускатель скачивает последнюю проверенную сборку по manifest.json. "
            "Незавершённые рабочие ветки не используются.",
        )
    gh("release", "upload", "preview", "-R", REPO, "--clobber", str(folder / "Shluz-launcher.zip"))
    gh(
        "release",
        "edit",
        "preview",
        "-R",
        REPO,
        "--notes",
        f"Проверенная версия {commit[:8]}. Полные исходники и архив образа: "
        f"https://github.com/{REPO}/releases/tag/{tag}\n"
        "Скачайте Shluz-launcher.zip, распакуйте, откройте Shluz.cmd. "
        "Настройки и существующие Docker-тома сохраняются; перед обновлением создаётся backup. "
        "Локальная preview-версия на SQLite, не production-деплой.",
    )
    # Commit the channel pointer last, after verified immutable assets are available.
    gh("release", "upload", "preview", "-R", REPO, "--clobber", str(folder / "manifest.json"))
    print("Published preview revision", commit)


if __name__ == "__main__":
    main()
