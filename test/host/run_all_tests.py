#!/usr/bin/env python3
"""Run all host suites present in this branch; shared by the three proposals."""
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[2]
for name in ("run_tests.py", "run_setpoint_tests.py", "run_update_tests.py"):
    script = root / "test/host" / name
    if script.is_file():
        subprocess.run([sys.executable, str(script)], cwd=root, check=True)
