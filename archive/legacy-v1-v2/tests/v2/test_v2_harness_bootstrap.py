from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = PROJECT_ROOT / "harness" / "v2" / "bootstrap.py"
FEATURE_LIST = PROJECT_ROOT / "harness" / "v2" / "feature_list.json"


def _current_next_feature_label() -> str:
    payload = json.loads(FEATURE_LIST.read_text(encoding="utf-8"))
    done = {feature["id"] for feature in payload["features"] if feature["status"] == "done"}
    eligible = [
        feature
        for feature in payload["features"]
        if feature["status"] != "done"
        and all(dependency in done for dependency in feature["depends_on"])
    ]
    if not eligible:
        return "none"
    next_feature = min(eligible, key=lambda feature: feature["priority"])
    return f'{next_feature["id"]} {next_feature["name"]}'


def test_v2_bootstrap_reports_next_feature():
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "V2 harness bootstrap OK" in result.stdout
    assert f"Current next feature: {_current_next_feature_label()}" in result.stdout


def test_v2_bootstrap_command_doc_check_passes():
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--check-commands"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "V2 command docs OK" in result.stdout
