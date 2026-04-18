from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ALLOWED_FEATURE_STATUSES = {"pending", "in_progress", "blocked", "done"}
REQUIRED_TRACK_FILES = [
    "app_spec.md",
    "feature_list.json",
    "claude-progress.txt",
    "validation_matrix.json",
    "initializer_report.md",
    "README.md",
]
REQUIRED_COMMAND_FILES = [
    ".claude/commands/code-v2.md",
    ".codex/skills/code-v2/SKILL.md",
    ".codex/skills/code-v2/agents/openai.yaml",
]
REQUIRED_PROGRESS_HEADERS = [
    "## Status:",
    "## Current Next Feature:",
    "## Session Update",
    "## Validation Summary",
    "## Blockers",
    "## Key Decisions",
]


class BootstrapError(RuntimeError):
    pass


@dataclass
class ReadyFeature:
    feature_id: str
    name: str
    priority: int


def load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise BootstrapError(f"Missing required text file: {path}") from exc


def load_json(path: Path) -> dict:
    try:
        return json.loads(load_text(path))
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"Invalid JSON in {path}: {exc}") from exc


def parse_design_headings(path: Path) -> set[str]:
    headings: set[str] = set()
    for line in load_text(path).splitlines():
        match = re.match(r"^#{2,6}\s+(.*)$", line.strip())
        if match:
            headings.add(match.group(1).strip())
    return headings


def validate_required_files(track_root: Path) -> None:
    missing = [name for name in REQUIRED_TRACK_FILES if not (track_root / name).exists()]
    if missing:
        raise BootstrapError(f"Missing V2 track files: {', '.join(missing)}")


def validate_command_docs(project_root: Path) -> None:
    missing = [name for name in REQUIRED_COMMAND_FILES if not (project_root / name).exists()]
    if missing:
        raise BootstrapError(f"Missing V2 command docs: {', '.join(missing)}")

    claude_doc = load_text(project_root / ".claude/commands/code-v2.md")
    skill_doc = load_text(project_root / ".codex/skills/code-v2/SKILL.md")
    agent_doc = load_text(project_root / ".codex/skills/code-v2/agents/openai.yaml")

    required_v2_tokens = [
        "harness/v2/feature_list.json",
        "harness/v2/claude-progress.txt",
        "harness/v2/app_spec.md",
    ]
    for token in required_v2_tokens:
        if token not in claude_doc:
            raise BootstrapError(f".claude/commands/code-v2.md is missing token: {token}")
        if token not in skill_doc:
            raise BootstrapError(f".codex/skills/code-v2/SKILL.md is missing token: {token}")

    if "code-v2" not in agent_doc or "harness/v2/" not in agent_doc:
        raise BootstrapError(".codex/skills/code-v2/agents/openai.yaml does not describe the V2 entrypoint")


def validate_validation_matrix(matrix: dict) -> dict[str, dict]:
    entries = matrix.get("entries")
    if not isinstance(entries, list) or not entries:
        raise BootstrapError("validation_matrix.json must contain a non-empty 'entries' list")

    entries_by_id: dict[str, dict] = {}
    for entry in entries:
        required = {"id", "scope", "commands", "success_condition", "artifacts"}
        missing = required - entry.keys()
        if missing:
            raise BootstrapError(f"Validation entry missing keys {sorted(missing)}: {entry}")
        if entry["scope"] not in {"track", "feature", "global"}:
            raise BootstrapError(f"Validation entry has invalid scope: {entry['id']}")
        if not isinstance(entry["commands"], list) or not entry["commands"]:
            raise BootstrapError(f"Validation entry must have non-empty commands: {entry['id']}")
        if entry["id"] in entries_by_id:
            raise BootstrapError(f"Duplicate validation id: {entry['id']}")
        entries_by_id[entry["id"]] = entry
    return entries_by_id


def validate_spec_ref(spec_ref: str, expected_design_name: str, design_headings: set[str]) -> None:
    if "#" not in spec_ref:
        raise BootstrapError(f"Invalid spec_ref format (expected file#heading): {spec_ref}")
    file_name, heading = spec_ref.split("#", 1)
    if file_name != expected_design_name:
        raise BootstrapError(f"spec_ref must target {expected_design_name}: {spec_ref}")
    if heading not in design_headings:
        raise BootstrapError(f"spec_ref points to missing heading '{heading}' in {expected_design_name}")


def detect_dependency_cycles(feature_by_id: dict[str, dict]) -> None:
    visited: set[str] = set()
    visiting: set[str] = set()

    def dfs(feature_id: str) -> None:
        if feature_id in visited:
            return
        if feature_id in visiting:
            raise BootstrapError(f"Feature dependency cycle detected at {feature_id}")
        visiting.add(feature_id)
        for dep in feature_by_id[feature_id]["depends_on"]:
            dfs(dep)
        visiting.remove(feature_id)
        visited.add(feature_id)

    for feature_id in feature_by_id:
        dfs(feature_id)


def validate_feature_graph(feature_list: dict, validation_ids: set[str], design_headings: set[str]) -> list[dict]:
    features = feature_list.get("features")
    if not isinstance(features, list) or not features:
        raise BootstrapError("feature_list.json must contain a non-empty 'features' list")

    feature_by_id: dict[str, dict] = {}
    for feature in features:
        required = {
            "id",
            "name",
            "description",
            "priority",
            "status",
            "depends_on",
            "acceptance_criteria",
            "spec_refs",
            "validation_refs",
        }
        missing = required - feature.keys()
        if missing:
            raise BootstrapError(f"Feature missing keys {sorted(missing)}: {feature}")
        if feature["status"] not in ALLOWED_FEATURE_STATUSES:
            raise BootstrapError(f"Feature {feature['id']} has invalid status {feature['status']}")
        if not isinstance(feature["priority"], int):
            raise BootstrapError(f"Feature {feature['id']} priority must be an integer")
        if not isinstance(feature["depends_on"], list):
            raise BootstrapError(f"Feature {feature['id']} depends_on must be a list")
        if not isinstance(feature["acceptance_criteria"], list) or not feature["acceptance_criteria"]:
            raise BootstrapError(f"Feature {feature['id']} must have non-empty acceptance_criteria")
        if not isinstance(feature["spec_refs"], list) or not feature["spec_refs"]:
            raise BootstrapError(f"Feature {feature['id']} must have non-empty spec_refs")
        if not isinstance(feature["validation_refs"], list) or not feature["validation_refs"]:
            raise BootstrapError(f"Feature {feature['id']} must have non-empty validation_refs")
        if feature["id"] in feature_by_id:
            raise BootstrapError(f"Duplicate feature id: {feature['id']}")

        for spec_ref in feature["spec_refs"]:
            validate_spec_ref(spec_ref, "docs/v2/设计V2.md", design_headings)
        for validation_ref in feature["validation_refs"]:
            if validation_ref not in validation_ids:
                raise BootstrapError(
                    f"Feature {feature['id']} points to missing validation_ref {validation_ref}"
                )

        feature_by_id[feature["id"]] = feature

    for feature in features:
        for dep in feature["depends_on"]:
            if dep not in feature_by_id:
                raise BootstrapError(f"Feature {feature['id']} depends on unknown feature {dep}")

    detect_dependency_cycles(feature_by_id)
    return features


def resolve_next_feature(features: list[dict]) -> ReadyFeature | None:
    feature_by_id = {feature["id"]: feature for feature in features}
    ready: list[ReadyFeature] = []
    for feature in features:
        if feature["status"] != "pending":
            continue
        if all(feature_by_id[dep]["status"] == "done" for dep in feature["depends_on"]):
            ready.append(
                ReadyFeature(
                    feature_id=feature["id"],
                    name=feature["name"],
                    priority=feature["priority"],
                )
            )
    if not ready:
        return None
    ready.sort(key=lambda item: (item.priority, item.feature_id))
    return ready[0]


def validate_progress_file(progress_path: Path, features: list[dict]) -> None:
    text = load_text(progress_path)
    for header in REQUIRED_PROGRESS_HEADERS:
        if header not in text:
            raise BootstrapError(f"Progress file missing required header: {header}")

    match = re.search(r"^## Current Next Feature:\s*(.+)$", text, flags=re.MULTILINE)
    if not match:
        raise BootstrapError("Progress file is missing 'Current Next Feature'")
    current_value = match.group(1).strip()

    next_feature = resolve_next_feature(features)
    if next_feature is None:
        expected = "none"
    else:
        expected = f"{next_feature.feature_id} {next_feature.name}"

    if current_value != expected:
        raise BootstrapError(
            f"Progress file Current Next Feature mismatch: expected '{expected}', got '{current_value}'"
        )


def validate_track(project_root: Path, track_root: Path) -> tuple[list[dict], ReadyFeature | None]:
    validate_required_files(track_root)
    validate_command_docs(project_root)

    feature_list = load_json(track_root / "feature_list.json")
    validation_matrix = load_json(track_root / "validation_matrix.json")
    validation_ids = set(validate_validation_matrix(validation_matrix).keys())
    design_headings = parse_design_headings(project_root / "docs/v2/设计V2.md")
    features = validate_feature_graph(feature_list, validation_ids, design_headings)
    validate_progress_file(track_root / "claude-progress.txt", features)
    return features, resolve_next_feature(features)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and inspect the V2 harness track.")
    parser.add_argument("--check-track", action="store_true", help="Validate V2 track files and schema only.")
    parser.add_argument("--check-commands", action="store_true", help="Validate V2 command docs only.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    project_root = Path(__file__).resolve().parents[2]
    track_root = Path(__file__).resolve().parent

    try:
        if args.check_commands:
            validate_command_docs(project_root)
            print("V2 command docs OK")
            return 0

        features, next_feature = validate_track(project_root, track_root)
        if args.check_track:
            print("V2 track OK")
            return 0

        done_count = sum(1 for feature in features if feature["status"] == "done")
        pending_count = sum(1 for feature in features if feature["status"] == "pending")

        print("V2 harness bootstrap OK")
        print(f"Python: {sys.executable}")
        print(f"Project root: {project_root}")
        print(f"Track root: {track_root}")
        print(f"Features: total={len(features)} done={done_count} pending={pending_count}")
        if next_feature is None:
            print("Current next feature: none")
        else:
            print(f"Current next feature: {next_feature.feature_id} {next_feature.name}")
        return 0
    except BootstrapError as exc:
        print(f"V2 harness bootstrap FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
