# V2 Initializer Report

## Source

- Source spec: [设计V2.md](../../docs/v2/设计V2.md)
- Derived app spec: [app_spec.md](./app_spec.md)

## Purpose

本报告记录 `docs/v2/设计V2.md -> app_spec / feature backlog / validation matrix` 的初始化映射，目的是让后续 coding agent 明确：

- 哪些内容来自人类主读设计文档
- 哪些内容已经被 Initializer 凝练为可执行 backlog
- 哪些章节由 deterministic validation 覆盖

## Derived Artifacts

| Artifact | Purpose |
|----------|---------|
| `harness/v2/app_spec.md` | 给 Initializer / coding agent 读取的精炼输入 |
| `harness/v2/feature_list.json` | V2 coding loop 的唯一 backlog |
| `harness/v2/claude-progress.txt` | V2 外部记忆与交接文件 |
| `harness/v2/validation_matrix.json` | V2 的 deterministic validation 轨道 |
| `harness/v2/README.md` | V2 harness 使用说明 |
| `.claude/commands/code-v2.md` | Claude 侧 V2 coding loop 入口 |
| `.codex/skills/code-v2/SKILL.md` | Codex 侧 V2 coding loop 入口 |
| `harness/v2/bootstrap.py` | 轨道一致性与 next-feature 自检入口 |

## Feature Mapping

| Feature | Source Sections |
|---------|-----------------|
| `V2-F01` | `1. 产品目标`、`2. 系统边界`、`12. 测试验收` |
| `V2-F02` | `3. 平台架构`、`5. Tool 与 Worker 模型`、`9. 核心类型定义` |
| `V2-F03` | `6. 状态、记忆与存储` |
| `V2-F04` | `4. Runtime 内核`、`11. 降级与错误恢复` |
| `V2-F05` | `3. 平台架构`、`8. 公开 API` |
| `V2-F06` | `6. 状态、记忆与存储`、`7. 推荐闭环与场景快照` |
| `V2-F07` | `5. Tool 与 Worker 模型`、`7. 推荐闭环与场景快照` |
| `V2-F08` | `5. Tool 与 Worker 模型`、`4. Runtime 内核` |
| `V2-F09` | `4. Runtime 内核`、`7. 推荐闭环与场景快照` |
| `V2-F10` | `7. 推荐闭环与场景快照`、`8. 公开 API` |
| `V2-F11` | `7. 推荐闭环与场景快照`、`8. 公开 API` |
| `V2-F12` | `11. 降级与错误恢复`、`12. 测试验收` |

## Validation Coverage

- Every `V2-Fxx` feature in `feature_list.json` has at least one `validation_ref`.
- All track-level bootstrap checks are handled by `harness/v2/bootstrap.py`.
- Future runtime and API features are mapped to dedicated `pytest` targets in `validation_matrix.json`.
- Global gates explicitly separate:
  - `GLOBAL-V2-SMOKE`
  - `GLOBAL-V1-NONREGRESSION`

## Initializer Outcome

- `设计V2.md` remains the human-readable, high-context source document.
- `harness/v2/app_spec.md` becomes the agent-facing product input.
- `harness/v2/feature_list.json + harness/v2/claude-progress.txt + harness/v2/validation_matrix.json` become the external memory system for the V2 coding loop.
