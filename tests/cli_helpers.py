"""Subprocess CLI invocation shared by CLI and generated-project tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_python_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(REPO_ROOT)
        if existing_python_path is None
        else str(REPO_ROOT) + os.pathsep + existing_python_path
    )
    return subprocess.run(
        [sys.executable, "-m", "rpacore.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
