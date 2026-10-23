"""Регрессии маршрутизации CI: неизвестные изменения нельзя пропускать."""
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).with_name("ci_scope.py")
spec = importlib.util.spec_from_file_location("ci_scope", path)
module = importlib.util.module_from_spec(spec) if path.exists() else None
if module is not None:
    spec.loader.exec_module(module)


class ScopeTests(unittest.TestCase):
    def required(self, files):
        # До оптимизации backend запускался для любого изменения.
        return module.requires_backend(files) if module else True

    def test_frontend_only(self):
        paths = ["web/src/components/HomePage.tsx", "web/package-lock.json"]
        self.assertFalse(self.required(paths))

    def test_web_workflow_and_docs(self):
        paths = [
            ".github/workflows/web-check.yml",
            "docs/website-passport.md",
            "docs/ci-workflow.md",
        ]
        self.assertFalse(self.required(paths))

    def test_mixed_changes(self):
        self.assertTrue(self.required(["web/src/app/page.tsx", "app/main.py"]))

    def test_backend_and_shared_configuration(self):
        paths = [
            "pyproject.toml", "Dockerfile", ".dockerignore", ".github/workflows/ci.yml",
            ".github/scripts/ci_scope.py", "tests/test_api.py", "alembic/env.py",
            "README.md", "unknown.config",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(self.required([path]))

    def test_lookalike_paths_are_not_excluded(self):
        self.assertTrue(self.required(["web-server/app.py"]))
        self.assertTrue(self.required(["docs/website-passport.md.bak"]))

    def test_empty_or_unavailable_diff_is_conservative(self):
        self.assertTrue(self.required([]))
        self.assertTrue(self.required(None))

    def test_renames_across_boundary_check_both_paths(self):
        self.assertTrue(self.required(["app/shared.py", "web/shared.py"]))

    def test_unavailable_history_requires_full_backend_checks(self):
        error = subprocess.CalledProcessError(128, "git")
        with patch.object(module.subprocess, "check_output", side_effect=error):
            self.assertIsNone(module.changed_files("a" * 40, "b" * 40, False))

    def test_first_push_requires_full_backend_checks(self):
        self.assertIsNone(module.changed_files("0" * 40, "b" * 40, False))

    def test_pr_uses_merge_base_and_nul_separated_paths(self):
        results = ["c" * 40 + "\n", b"web/file name.ts\0app/main.py\0"]
        with patch.object(module.subprocess, "check_output", side_effect=results) as git:
            paths = module.changed_files("a" * 40, "b" * 40, True)
        self.assertEqual(paths, ["web/file name.ts", "app/main.py"])
        self.assertEqual(git.call_args_list[0].args[0][1], "merge-base")
        self.assertIn("--no-renames", git.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
