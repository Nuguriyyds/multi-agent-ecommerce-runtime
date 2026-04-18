---
name: code
description: Codex equivalent of the repository's V3 `/code` command. Use when the user says `/code`, asks Codex to continue the V3 repository, resume the next pending feature from `harness/v3/feature_list.json`, validate with `validation_refs`, and update `harness/v3/claude-progress.txt`.
---

# Code

This skill is the Codex replacement for the V3 `/code` command in this repository.

Use `$code` when invoking the skill explicitly in Codex. Treat user requests that mention `/code` as requests to use this skill. Each round starts from a fresh coding-agent session and all V3 development commands must use the repo-local `.venv`.

## Run The Startup Ritual

1. Confirm you are at the repository root.
2. Run `.\.venv\Scripts\python.exe harness/v3/bootstrap.py` and ensure the V3 track validates successfully.
3. Enumerate the real workspace with `rg --files app/v3 tests/v3`.
4. Read `CLAUDE.md` first to refresh project constraints and protected patterns.
5. Read `harness/v3/feature_list.json` and identify the pending feature with the smallest numeric `priority` whose dependencies are all `done`.
6. Read the selected feature's `spec_reference` sections in `docs/app_spec.md`.
7. Read `harness/v3/claude-progress.txt` to understand recent decisions, blockers, and the recommended next step.
8. Read `harness/v3/validation_matrix.json` and resolve the selected feature's `validation_refs`.
9. Treat workspace-first evidence in this precedence order: `workspace files > harness/v3/feature_list.json > harness/v3/claude-progress.txt > git log`.
10. Read `git log --oneline -5` only as optional context. If it fails, report `git history unavailable` and continue from the workspace-first sources.
11. Run all validation commands through `.\.venv\Scripts\python.exe -m pytest ...`, not global `python` / `pytest`.

- If the workspace contradicts `feature_list.json` or `claude-progress.txt`, stop and report `state drift`.
- If any dependency for the selected feature is not `done`, do not start implementation.
- Explain the blocker clearly.
- Record the blocker in `harness/v3/claude-progress.txt`.
- Stop after documenting the state.

## Implement Exactly One Feature

- Work on one feature only.
- Announce the selected feature by `id` and `name` before editing.
- Use the feature's `acceptance_criteria` as the only completion bar.
- Use the feature's `validation_refs` as the deterministic validation gate from `harness/v3/validation_matrix.json`.
- Preserve the repository rules from `CLAUDE.md`, especially:
  - keep agent execution async
  - use `asyncio.gather()` for parallel work instead of threads
  - pass agent data through Pydantic models under `models/`
  - keep external services mocked or in-memory unless the repo already defines a real integration path
  - add or update pytest coverage for each implemented agent
  - log key start, success, failure, and degradation events with the standard `logging` module
- Do not add features that are not listed in `harness/v3/feature_list.json`.
- Do not modify completed features unless the current feature explicitly requires it.

## Validate Incrementally

- Implement and verify one acceptance criterion at a time.
- After satisfying each criterion, run the narrowest relevant validation command immediately.
- After all criteria pass, run the broader validation commands required by the feature's `validation_refs`.
- If a blocker prevents completion, document the blocker and current state in `harness/v3/claude-progress.txt` before stopping.

## Close The Session

- Update `harness/v3/feature_list.json` and set the completed feature's `status` to `"done"`.
- Update `harness/v3/claude-progress.txt` with the completed work, key decisions, blockers or residual risks, and the recommended next step.
- If commits are requested, use:
  - `feat(V3-F0X): feature summary`
  - `progress(v3): update V3-F0X progress`
