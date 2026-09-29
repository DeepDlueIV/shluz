import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def test_tariff_pattern_accepts_latin_codes_in_browser_unicode_mode():
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("Node.js is required for the browser-pattern CI check")
        pytest.skip("Optional browser-pattern check requires Node.js 22+")
    template = Path("app/templates/admin/plan_fields.html").read_text(encoding="utf-8")
    pattern = re.search(r'pattern="([^"]+)"', template).group(1)
    script = """
    const fs = require('node:fs');
    const pattern = JSON.parse(fs.readFileSync(0, 'utf8'));
    const regex = new RegExp(`^(?:${pattern})$`, 'v');
    for (const value of ['test', 'Pro', 'pro-1', 'test_2', '1', 'a'.repeat(64)]) {
      if (!regex.test(value)) throw new Error(`Valid code rejected: ${value}`);
    }
    for (const value of ['тест', 'two words', '-pro', '_pro', 'a'.repeat(65)]) {
      if (regex.test(value)) throw new Error(`Invalid code accepted: ${value}`);
    }
    """
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(pattern), text=True,
        capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_mock_browser_script_has_valid_syntax():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional JavaScript check requires Node.js")
    result = subprocess.run(
        [node, "--check", "app/static/test-client.js"], text=True,
        capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
