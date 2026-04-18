from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ALLOWED_FEATURE_STATUSES = {"pending", "in_progress", "blocked", "done"}
REPO_VENV_DIRNAME = ".venv"
REPO_VENV_BOOTSTRAP_COMMAND = r".\.venv\Scripts\python.exe harness/v3/bootstrap.py"
REPO_VENV_PYTEST_COMMAND = r".\.venv\Scripts\python.exe -m pytest"
REPO_VENV_SETUP_COMMANDS = [
    "python -m venv .venv",
    r".\.venv\Scripts\python.exe -m pip install -U pip",
    r".\.venv\Scripts\python.exe -m pip install -r requirements.txt",
]
WORKSPACE_ENUMERATION_COMMAND = "rg --files app/v3 tests/v3"
WORKSPACE_PRECEDENCE_ORDER = (
    "workspace files > harness/v3/feature_list.json > harness/v3/claude-progress.txt > git log"
)
STATE_DRIFT_TOKEN = "state drift"
GIT_HISTORY_UNAVAILABLE_TOKEN = "git history unavailable"
WORKSPACE_FIRST_TOKEN = "workspace-first"
REQUIRED_TRACK_FILES = [
    "bootstrap.py",
    "feature_list.json",
    "claude-progress.txt",
    "validation_matrix.json",
]
REQUIRED_PROJECT_FILES = [
    "README.md",
    "CLAUDE.md",
    "CODEX.md",
    "docs/app_spec.md",
]
REQUIRED_COMMAND_FILES = [
    ".claude/commands/code.md",
    ".codex/skills/code/SKILL.md",
    ".codex/skills/code/agents/openai.yaml",
    "CODEX.md",
]
CURRENT_FEATURE_HEADER = "## 当前正在做的 feature"
COMPLETED_FEATURES_HEADER = "## 已完成的 feature"
DECISION_HEADER = "## 关键决策与偏差记录"
NEXT_STEP_HEADER = "## 下一步建议"
OPEN_QUESTION_HEADER = "## 未解决的问题"
REQUIRED_PROGRESS_HEADERS = [
    CURRENT_FEATURE_HEADER,
    COMPLETED_FEATURES_HEADER,
    DECISION_HEADER,
    NEXT_STEP_HEADER,
    OPEN_QUESTION_HEADER,
]
PROJECT_README_TOKENS = [
    "harness/v3/feature_list.json",
    "harness/v3/claude-progress.txt",
    "harness/v3/validation_matrix.json",
    "fresh coding-agent session",
    *REPO_VENV_SETUP_COMMANDS,
    REPO_VENV_BOOTSTRAP_COMMAND,
    r".\.venv\Scripts\python.exe -m uvicorn app.main:app",
    WORKSPACE_ENUMERATION_COMMAND,
    WORKSPACE_PRECEDENCE_ORDER,
    STATE_DRIFT_TOKEN,
    GIT_HISTORY_UNAVAILABLE_TOKEN,
]
COMMAND_DOC_TOKENS = [
    "CLAUDE.md",
    "harness/v3/feature_list.json",
    "harness/v3/claude-progress.txt",
    "harness/v3/validation_matrix.json",
    "fresh coding-agent session",
    REPO_VENV_BOOTSTRAP_COMMAND,
    REPO_VENV_PYTEST_COMMAND,
    "spec_reference",
    "validation_refs",
    WORKSPACE_ENUMERATION_COMMAND,
    WORKSPACE_PRECEDENCE_ORDER,
    STATE_DRIFT_TOKEN,
    GIT_HISTORY_UNAVAILABLE_TOKEN,
    WORKSPACE_FIRST_TOKEN,
]
AGENT_DOC_TOKENS = [
    "harness/v3/",
    WORKSPACE_FIRST_TOKEN,
    STATE_DRIFT_TOKEN,
]
LEGACY_TRACK_TOKENS = [
    "harness/v1/",
    "harness/v2/",
]
SECTION_RE = re.compile(r"^#{2,6}\s+(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$")
SPEC_SECTION_RE = re.compile(r"§\s*(\d+(?:\.\d+)*)")
HEADER_RE = re.compile(r"^##\s+.+$")
FEATURE_ID_RE = re.compile(r"\b(V3-F\d+)\b")


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReadyFeature:
    feature_id: str
    name: str
    priority: int


@dataclass(frozen=True)
class ProgressState:
    current_feature_id: str | None
    completed_feature_ids: frozenset[str]


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


def normalize_repo_relative_path(raw_path: str, *, context: str) -> Path:
    if not isinstance(raw_path, str):
        raise BootstrapError(f"{context} artifact path must be a string: {raw_path!r}")

    normalized = raw_path.strip()
    if not normalized:
        raise BootstrapError(f"{context} artifact path must not be empty")

    artifact_path = Path(normalized)
    if artifact_path.is_absolute():
        raise BootstrapError(f"{context} artifact path must be repo-relative: {normalized}")
    if any(part == ".." for part in artifact_path.parts):
        raise BootstrapError(f"{context} artifact path must not escape the repo: {normalized}")
    if normalized in {".", "./"}:
        raise BootstrapError(f"{context} artifact path must not point to the repo root")

    return artifact_path


def validate_required_files(track_root: Path, project_root: Path) -> None:
    missing_track = [name for name in REQUIRED_TRACK_FILES if not (track_root / name).exists()]
    if missing_track:
        raise BootstrapError(f"Missing V3 track files: {', '.join(missing_track)}")

    missing_project = [name for name in REQUIRED_PROJECT_FILES if not (project_root / name).exists()]
    if missing_project:
        raise BootstrapError(f"Missing V3 project files: {', '.join(missing_project)}")


def validate_repo_venv_python(project_root: Path, executable: Path | None = None) -> Path:
    current_executable = (executable or Path(sys.executable)).resolve(strict=False)
    repo_venv_root = (project_root / REPO_VENV_DIRNAME).resolve(strict=False)
    if current_executable.is_relative_to(repo_venv_root):
        return current_executable

    setup_hint = " ; ".join(REPO_VENV_SETUP_COMMANDS)
    raise BootstrapError(
        "V3 development commands must run from the repo-local .venv. "
        f"Current interpreter: {current_executable}. "
        f"Re-run with `{REPO_VENV_BOOTSTRAP_COMMAND}`. "
        f"If `.venv` is missing, initialize it with `{setup_hint}`."
    )


def validate_workspace_docs(project_root: Path) -> None:
    readme = load_text(project_root / "README.md")
    for token in PROJECT_README_TOKENS:
        if token not in readme:
            raise BootstrapError(f"README.md is missing token: {token}")


def validate_command_docs(project_root: Path) -> None:
    missing = [name for name in REQUIRED_COMMAND_FILES if not (project_root / name).exists()]
    if missing:
        raise BootstrapError(f"Missing V3 command docs: {', '.join(missing)}")

    claude_doc = load_text(project_root / ".claude/commands/code.md")
    skill_doc = load_text(project_root / ".codex/skills/code/SKILL.md")
    agent_doc = load_text(project_root / ".codex/skills/code/agents/openai.yaml")
    codex_doc = load_text(project_root / "CODEX.md")

    for token in COMMAND_DOC_TOKENS:
        if token not in claude_doc:
            raise BootstrapError(f".claude/commands/code.md is missing token: {token}")
        if token not in skill_doc:
            raise BootstrapError(f".codex/skills/code/SKILL.md is missing token: {token}")
        if token not in codex_doc:
            raise BootstrapError(f"CODEX.md is missing token: {token}")

    for token in AGENT_DOC_TOKENS:
        if token not in agent_doc:
            raise BootstrapError(f".codex/skills/code/agents/openai.yaml is missing token: {token}")

    for legacy_token in LEGACY_TRACK_TOKENS:
        if legacy_token in claude_doc:
            raise BootstrapError(f".claude/commands/code.md still references legacy track token: {legacy_token}")
        if legacy_token in skill_doc:
            raise BootstrapError(f".codex/skills/code/SKILL.md still references legacy track token: {legacy_token}")
        if legacy_token in agent_doc:
            raise BootstrapError(
                f".codex/skills/code/agents/openai.yaml still references legacy track token: {legacy_token}"
            )


def validate_validation_matrix(matrix: dict) -> dict[str, dict]:
    if matrix.get("track") != "harness/v3":
        raise BootstrapError("validation_matrix.json must declare track='harness/v3'")

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
        if not isinstance(entry["artifacts"], list) or not entry["artifacts"]:
            raise BootstrapError(f"Validation entry must have non-empty artifacts: {entry['id']}")
        if entry["id"] in entries_by_id:
            raise BootstrapError(f"Duplicate validation id: {entry['id']}")

        artifact_paths = tuple(
            normalize_repo_relative_path(artifact, context=f"validation entry {entry['id']}")
            for artifact in entry["artifacts"]
        )
        entries_by_id[entry["id"]] = {**entry, "artifact_paths": artifact_paths}

    return entries_by_id


def parse_app_spec_sections(path: Path) -> set[str]:
    sections: set[str] = set()
    for line in load_text(path).splitlines():
        match = SECTION_RE.match(line.strip())
        if match:
            sections.add(match.group(1))
    return sections


def validate_spec_reference(spec_reference: str, app_spec_sections: set[str]) -> None:
    if "docs/app_spec.md" not in spec_reference:
        raise BootstrapError(f"spec_reference must target docs/app_spec.md: {spec_reference}")

    section_ids = SPEC_SECTION_RE.findall(spec_reference)
    if not section_ids:
        raise BootstrapError(f"spec_reference must contain at least one section token: {spec_reference}")

    for section_id in section_ids:
        if section_id not in app_spec_sections:
            raise BootstrapError(
                f"spec_reference points to missing section §{section_id} in docs/app_spec.md"
            )


def detect_dependency_cycles(feature_by_id: dict[str, dict]) -> None:
    visited: set[str] = set()
    visiting: set[str] = set()

    def dfs(feature_id: str) -> None:
        if feature_id in visited:
            return
        if feature_id in visiting:
            raise BootstrapError(f"Feature dependency cycle detected at {feature_id}")
        visiting.add(feature_id)
        for dependency in feature_by_id[feature_id]["depends_on"]:
            dfs(dependency)
        visiting.remove(feature_id)
        visited.add(feature_id)

    for feature_id in feature_by_id:
        dfs(feature_id)


def validate_feature_graph(feature_list: dict, validation_ids: set[str], app_spec_sections: set[str]) -> list[dict]:
    if feature_list.get("source") != "docs/app_spec.md":
        raise BootstrapError("feature_list.json must declare source='docs/app_spec.md'")

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
            "spec_reference",
            "validation_refs",
            "acceptance_criteria",
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
        if not isinstance(feature["validation_refs"], list) or not feature["validation_refs"]:
            raise BootstrapError(f"Feature {feature['id']} must have non-empty validation_refs")
        if not isinstance(feature["spec_reference"], str):
            raise BootstrapError(f"Feature {feature['id']} spec_reference must be a string")
        if feature["id"] in feature_by_id:
            raise BootstrapError(f"Duplicate feature id: {feature['id']}")

        validate_spec_reference(feature["spec_reference"], app_spec_sections)
        for validation_ref in feature["validation_refs"]:
            if validation_ref not in validation_ids:
                raise BootstrapError(
                    f"Feature {feature['id']} points to missing validation_ref {validation_ref}"
                )

        feature_by_id[feature["id"]] = feature

    for feature in features:
        for dependency in feature["depends_on"]:
            if dependency not in feature_by_id:
                raise BootstrapError(f"Feature {feature['id']} depends on unknown feature {dependency}")

    detect_dependency_cycles(feature_by_id)
    return features


def resolve_feature_artifacts(feature: dict, validation_entries: dict[str, dict]) -> tuple[Path, ...]:
    ordered_artifacts: list[Path] = []
    seen: set[Path] = set()

    for validation_ref in feature["validation_refs"]:
        entry = validation_entries[validation_ref]
        for artifact_path in entry["artifact_paths"]:
            if artifact_path in seen:
                continue
            seen.add(artifact_path)
            ordered_artifacts.append(artifact_path)

    return tuple(ordered_artifacts)


def validate_done_feature_artifacts(
    project_root: Path,
    features: list[dict],
    validation_entries: dict[str, dict],
) -> None:
    missing_by_feature: dict[str, list[str]] = {}

    for feature in features:
        if feature["status"] != "done":
            continue

        missing_artifacts = [
            artifact_path.as_posix()
            for artifact_path in resolve_feature_artifacts(feature, validation_entries)
            if not (project_root / artifact_path).exists()
        ]
        if missing_artifacts:
            missing_by_feature[feature["id"]] = missing_artifacts

    if not missing_by_feature:
        return

    details = "; ".join(
        f"{feature_id} missing {', '.join(artifacts)}"
        for feature_id, artifacts in sorted(missing_by_feature.items())
    )
    raise BootstrapError(f"{STATE_DRIFT_TOKEN}: done feature artifacts missing from workspace: {details}")


def resolve_next_feature(features: list[dict]) -> ReadyFeature | None:
    feature_by_id = {feature["id"]: feature for feature in features}
    ready: list[ReadyFeature] = []

    for feature in features:
        if feature["status"] != "pending":
            continue
        if all(feature_by_id[dependency]["status"] == "done" for dependency in feature["depends_on"]):
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


def split_markdown_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_header: str | None = None

    for line in text.splitlines():
        if HEADER_RE.match(line.strip()):
            current_header = line.strip()
            sections[current_header] = []
            continue
        if current_header is not None:
            sections[current_header].append(line)

    return {
        header: "\n".join(lines).strip()
        for header, lines in sections.items()
    }


def parse_progress_state(progress_path: Path, feature_ids: set[str]) -> ProgressState:
    text = load_text(progress_path)
    for header in REQUIRED_PROGRESS_HEADERS:
        if header not in text:
            raise BootstrapError(f"Progress file missing required header: {header}")

    sections = split_markdown_sections(text)
    current_section = sections.get(CURRENT_FEATURE_HEADER, "")
    completed_section = sections.get(COMPLETED_FEATURES_HEADER, "")

    current_feature_id: str | None = None
    for line in current_section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ID:"):
            continue
        raw_id = stripped.split(":", maxsplit=1)[1].strip()
        if raw_id in {"", "—", "-", "none"}:
            current_feature_id = None
            break
        current_feature_id = raw_id
        break

    if current_feature_id is not None and current_feature_id not in feature_ids:
        raise BootstrapError(f"Progress file references unknown feature id: {current_feature_id}")

    completed_feature_ids: set[str] = set()
    for line in completed_section.splitlines():
        match = FEATURE_ID_RE.search(line)
        if not match:
            continue
        feature_id = match.group(1)
        if feature_id not in feature_ids:
            raise BootstrapError(f"Progress file completed section references unknown feature id: {feature_id}")
        completed_feature_ids.add(feature_id)

    return ProgressState(
        current_feature_id=current_feature_id,
        completed_feature_ids=frozenset(completed_feature_ids),
    )


def validate_progress_state(progress_state: ProgressState, features: list[dict]) -> None:
    feature_by_id = {feature["id"]: feature for feature in features}
    done_feature_ids = {feature["id"] for feature in features if feature["status"] == "done"}

    if progress_state.completed_feature_ids != done_feature_ids:
        recorded = ", ".join(sorted(progress_state.completed_feature_ids)) or "none"
        expected = ", ".join(sorted(done_feature_ids)) or "none"
        raise BootstrapError(
            f"{STATE_DRIFT_TOKEN}: completed features in claude-progress.txt "
            f"({recorded}) do not match feature_list.json done set ({expected})"
        )

    current_feature_id = progress_state.current_feature_id
    if current_feature_id is None:
        return

    if current_feature_id in progress_state.completed_feature_ids:
        raise BootstrapError(
            f"{STATE_DRIFT_TOKEN}: current feature {current_feature_id} also appears in the completed list"
        )

    if feature_by_id[current_feature_id]["status"] == "done":
        raise BootstrapError(
            f"{STATE_DRIFT_TOKEN}: current feature {current_feature_id} is marked done in feature_list.json"
        )


def validate_track(
    project_root: Path,
    track_root: Path,
    *,
    include_command_docs: bool,
) -> tuple[list[dict], ReadyFeature | None]:
    validate_repo_venv_python(project_root)
    validate_required_files(track_root, project_root)
    validate_workspace_docs(project_root)
    if include_command_docs:
        validate_command_docs(project_root)

    feature_list = load_json(track_root / "feature_list.json")
    validation_matrix = load_json(track_root / "validation_matrix.json")
    validation_entries = validate_validation_matrix(validation_matrix)
    validation_ids = set(validation_entries.keys())
    app_spec_sections = parse_app_spec_sections(project_root / "docs/app_spec.md")
    features = validate_feature_graph(feature_list, validation_ids, app_spec_sections)
    progress_state = parse_progress_state(
        track_root / "claude-progress.txt",
        {feature["id"] for feature in features},
    )
    validate_progress_state(progress_state, features)
    validate_done_feature_artifacts(project_root, features, validation_entries)
    return features, resolve_next_feature(features)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and inspect the V3 harness track.")
    parser.add_argument("--check-track", action="store_true", help="Validate V3 track files and schema only.")
    parser.add_argument("--check-commands", action="store_true", help="Validate V3 command docs only.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    project_root = Path(__file__).resolve().parents[2]
    track_root = Path(__file__).resolve().parent

    try:
        if args.check_commands:
            validate_command_docs(project_root)
            print("V3 command docs OK")
            return 0

        features, next_feature = validate_track(
            project_root,
            track_root,
            include_command_docs=not args.check_track,
        )
        if args.check_track:
            print("V3 track OK")
            return 0

        done_count = sum(1 for feature in features if feature["status"] == "done")
        pending_count = sum(1 for feature in features if feature["status"] == "pending")

        print("V3 harness bootstrap OK")
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
        print(f"V3 harness bootstrap FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
