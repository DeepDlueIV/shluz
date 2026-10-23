"""Пропускает backend только для доказанно изолированных веб-изменений."""
import os
import subprocess

WEB_ONLY_FILES = {
    ".github/workflows/web-check.yml",
    "docs/website-passport.md",
    "docs/ci-workflow.md",
}


def requires_backend(files: list[str] | None) -> bool:
    if not files:
        return True
    return any(not (path.startswith("web/") or path in WEB_ONLY_FILES) for path in files)


def changed_files(base: str, head: str, pull_request: bool) -> list[str] | None:
    if not base or not head or set(base) == {"0"}:
        return None
    try:
        # PR сравниваем с общей базой, push — со всей предыдущей версией ветки.
        if pull_request:
            base = subprocess.check_output(
                ["git", "merge-base", base, head], text=True, timeout=30
            ).strip()
        output = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, head, "--"],
            timeout=30,
        )
        return [
            path.decode("utf-8", errors="surrogateescape")
            for path in output.split(b"\0") if path
        ]
    except (OSError, subprocess.SubprocessError):
        # Ошибка сравнения не должна превращаться в зелёный пропуск проверок.
        return None


if __name__ == "__main__":
    files = changed_files(
        os.environ.get("BASE_SHA", ""),
        os.environ.get("HEAD_SHA", ""),
        os.environ.get("GITHUB_EVENT_NAME") == "pull_request",
    )
    print(f"backend={str(requires_backend(files)).lower()}")
