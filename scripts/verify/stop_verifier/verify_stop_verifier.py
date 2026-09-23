#!/usr/bin/env python3
"""Run structural hook regressions; model semantics are evaluated by the prompt topic."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    """Execute actual schema, provenance, artifact, and lifecycle checks in a subprocess."""
    return subprocess.run([
        sys.executable, "-m", "pytest", "-q",
        "tests/test_agy_stop_audit_contract.py",
        "tests/test_zcode_stop_audit_contract.py", "tests/test_qoder_stop_audit_contract.py",
        "tests/test_qoder_stop_audit_jev.py", "tests/test_hermes_stop_review.py",
        "tests/test_jev_compass.py", "tests/test_jev_parser.py",
        "tests/test_claims.py", "tests/test_turnmode.py",
    ], cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
