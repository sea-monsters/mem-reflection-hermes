"""pytest entrypoint for the host-contract smoke script."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_host_contract_smoke_script_passes():
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, str(repo / "scripts" / "smoke_host_contract.py")],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
