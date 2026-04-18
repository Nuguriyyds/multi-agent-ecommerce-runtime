---
name: code-v2
description: Codex equivalent of a V2-specific harness coding loop. Use when the user says `/code-v2`, asks to continue the V2 harness track, or wants the next pending V2 feature from `harness/v2/feature_list.json`.
---

# Code V2

This skill is the Codex replacement for a dedicated V2 harness coding loop in this repository.

Use `$code-v2` when invoking the skill explicitly in Codex. Treat requests that mention `/code-v2` or “continue the V2 harness track” as requests to use this skill.

## Run The Startup Ritual

1. Confirm you are at the repository root.
2. Run `python harness/v2/bootstrap.py` and ensure the V2 track validates successfully.
3. Read `harness/v2/app_spec.md` to refresh the V2 product scope and non-goals.
4. Read `harness/v2/feature_list.json` and select the pending feature with the smallest numeric `priority` whose dependencies are all `done`.
5. Read `harness/v2/claude-progress.txt` to understand recent decisions, blockers, and the recommended next step.
6. Read `git log --oneline -5`.

- If any dependency for the selected feature is not `done`, do not start implementation.
- Explain the blocker clearly.
- Record the blocker in `harness/v2/claude-progress.txt`.
- Stop after documenting the state.

## Implement Exactly One V2 Feature

- Work on one V2 feature only.
- Announce the selected feature by `id` and `name` before editing.
- Use the feature's `acceptance_criteria` as the only completion bar.
- Use the feature's `validation_refs` as the deterministic acceptance gate.
- Preserve V2 track isolation:
  - read and write only `harness/v2/*` for V2 state
  - do not update the root V1 `feature_list.json`
  - do not update the root V1 `claude-progress.txt`

## Validate Incrementally

- Implement and verify one acceptance criterion at a time.
- After satisfying each criterion, run the narrowest relevant validation command immediately.
- After all criteria pass, run the broader validation commands required by the feature's `validation_refs`.
- If a blocker prevents completion, document the blocker and current state in `harness/v2/claude-progress.txt` before stopping.

## Close The Session

- Update `harness/v2/feature_list.json` and set the completed feature's `status` to `"done"`.
- Update `harness/v2/claude-progress.txt` with the completed work, key decisions, blockers or residual risks, and the recommended next step.
- If commits are requested, use:
  - `feat(V2-F0X): feature summary`
  - `progress(v2): update V2-F0X progress`
