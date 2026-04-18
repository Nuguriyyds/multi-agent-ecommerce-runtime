from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.v3 import bootstrap


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = PROJECT_ROOT / "harness" / "v3" / "bootstrap.py"
FEATURE_LIST = PROJECT_ROOT / "harness" / "v3" / "feature_list.json"
PROGRESS_FILE = PROJECT_ROOT / "harness" / "v3" / "claude-progress.txt"
VALIDATION_MATRIX = PROJECT_ROOT / "harness" / "v3" / "validation_matrix.json"


def _current_next_feature_label() -> str:
    payload = json.loads(FEATURE_LIST.read_text(encoding="utf-8"))
    done = {feature["id"] for feature in payload["features"] if feature["status"] == "done"}
    eligible = [
        feature
        for feature in payload["features"]
        if feature["status"] == "pending"
        and all(dependency in done for dependency in feature["depends_on"])
    ]
    if not eligible:
        return "none"
    next_feature = min(eligible, key=lambda feature: (feature["priority"], feature["id"]))
    return f'{next_feature["id"]} {next_feature["name"]}'


def _load_feature_list() -> dict:
    return json.loads(FEATURE_LIST.read_text(encoding="utf-8-sig"))


def _load_validation_entries() -> dict[str, dict]:
    matrix = json.loads(VALIDATION_MATRIX.read_text(encoding="utf-8-sig"))
    return bootstrap.validate_validation_matrix(matrix)


def test_v3_bootstrap_reports_next_feature() -> None:
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "V3 harness bootstrap OK" in result.stdout
    assert f"Current next feature: {_current_next_feature_label()}" in result.stdout


def test_v3_bootstrap_track_check_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--check-track"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "V3 track OK" in result.stdout


def test_v3_bootstrap_command_doc_check_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "--check-commands"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "V3 command docs OK" in result.stdout


def test_v3_validate_repo_venv_python_allows_repo_local_paths() -> None:
    executable = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

    resolved = bootstrap.validate_repo_venv_python(PROJECT_ROOT, executable)

    assert resolved.is_relative_to((PROJECT_ROOT / ".venv").resolve(strict=False))


def test_v3_validate_repo_venv_python_rejects_non_venv_paths() -> None:
    executable = PROJECT_ROOT.parent / "python.exe"

    with pytest.raises(bootstrap.BootstrapError, match="repo-local \\.venv"):
        bootstrap.validate_repo_venv_python(PROJECT_ROOT, executable)


def test_v3_resolve_feature_artifacts_uses_validation_refs() -> None:
    feature_list = _load_feature_list()
    validation_entries = _load_validation_entries()
    feature = next(item for item in feature_list["features"] if item["id"] == "V3-F02")

    artifacts = bootstrap.resolve_feature_artifacts(feature, validation_entries)

    assert Path("app/v3/models/session.py") in artifacts
    assert Path("tests/v3/test_f02_models.py") in artifacts


def test_v3_parse_progress_state_matches_current_repo() -> None:
    feature_list = _load_feature_list()
    feature_ids = {feature["id"] for feature in feature_list["features"]}
    done_feature_ids = {
        feature["id"] for feature in feature_list["features"] if feature["status"] == "done"
    }

    progress_state = bootstrap.parse_progress_state(PROGRESS_FILE, feature_ids)

    assert progress_state.current_feature_id is None
    assert progress_state.completed_feature_ids == frozenset(done_feature_ids)


def test_v3_validate_progress_state_rejects_completed_set_mismatch() -> None:
    features = [
        {"id": "V3-F01", "status": "done"},
        {"id": "V3-F02", "status": "pending"},
    ]
    progress_state = bootstrap.ProgressState(
        current_feature_id=None,
        completed_feature_ids=frozenset({"V3-F02"}),
    )

    with pytest.raises(bootstrap.BootstrapError, match="state drift"):
        bootstrap.validate_progress_state(progress_state, features)


def test_v3_validate_progress_state_rejects_current_feature_overlap() -> None:
    features = [
        {"id": "V3-F01", "status": "pending"},
        {"id": "V3-F02", "status": "done"},
    ]
    progress_state = bootstrap.ProgressState(
        current_feature_id="V3-F02",
        completed_feature_ids=frozenset({"V3-F02"}),
    )

    with pytest.raises(bootstrap.BootstrapError, match="state drift"):
        bootstrap.validate_progress_state(progress_state, features)


def test_v3_validate_done_feature_artifacts_rejects_missing_workspace_contract() -> None:
    features = [
        {
            "id": "V3-F99",
            "status": "done",
            "validation_refs": ["V3-F99-PYTEST"],
        }
    ]
    validation_entries = {
        "V3-F99-PYTEST": {
            "artifact_paths": (Path("tests/v3/test_missing_workspace_contract.py"),),
        }
    }

    with pytest.raises(bootstrap.BootstrapError, match="state drift"):
        bootstrap.validate_done_feature_artifacts(PROJECT_ROOT, features, validation_entries)
