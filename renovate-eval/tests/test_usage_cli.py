"""CLI exit-code contract for the usage gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / "renovate_eval.py"


@pytest.mark.parametrize("threshold", ["0", "1.5", "-1", "abc"])
def test_invalid_threshold_is_a_usage_error(threshold):
    result = subprocess.run(
        [sys.executable, str(CLI), "usage-check", "--threshold", threshold],
        capture_output=True,
        text=True,
        check=False,
    )

    # Exit 2 (argparse) is distinct from exit 1, which means a closed gate.
    assert result.returncode == 2
    assert "--threshold" in result.stderr
