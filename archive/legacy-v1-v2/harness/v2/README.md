# V2 Harness Track

## What This Is

`harness/v2/` 是与现有 V1 harness 并行的一条新轨道，用于从 [设计V2.md](../../docs/v2/设计V2.md) 派生并驱动 V2 的 coding loop。

这条轨道遵循同样的核心原则：

- Initializer 先拆解 spec
- 项目状态放到外部状态文件
- 每轮 coding agent 只做一个 feature
- 完成判定依赖 deterministic validation，而不是模型主观判断

## Track Files

- `app_spec.md`
- `feature_list.json`
- `claude-progress.txt`
- `validation_matrix.json`
- `initializer_report.md`
- `bootstrap.py`

## Startup

运行：

```bash
python harness/v2/bootstrap.py
```

它会：

- 校验 V2 轨道文件是否齐全
- 校验 `设计V2.md` 中的 `spec_refs`
- 校验 `validation_refs` 是否都存在
- 输出当前 next pending feature

## Coding Loop

V2 不复用根目录的 `/code` 轨道，而是使用独立入口：

- Claude: `.claude/commands/code-v2.md`
- Codex: `.codex/skills/code-v2/SKILL.md`

固定流程：

1. 读取 `harness/v2/feature_list.json`
2. 选择优先级最高且依赖已完成的 pending feature
3. 读取 `harness/v2/claude-progress.txt`
4. 只实现一个 feature
5. 按 `validation_refs` 跑确定性验证
6. 更新 `harness/v2/feature_list.json`
7. 更新 `harness/v2/claude-progress.txt`

## Isolation Rules

- V2 coding loop 只读写 `harness/v2/*`
- 不修改根目录的 V1 `feature_list.json`
- 不修改根目录的 V1 `claude-progress.txt`
- V1 仍然是已完成的独立轨道

## Source of Truth

- 人类主读设计：`设计V2.md`
- agent 输入 spec：`harness/v2/app_spec.md`
- V2 外部状态记忆：`feature_list.json + claude-progress.txt + validation_matrix.json`

## Validation Entry Points

- V2 轨道一致性：`python harness/v2/bootstrap.py`
- V2 全量 deterministic suite：`pytest tests -k "test_v2_" -q`
- V2 端到端 smoke：`python smoke_test_v2.py`
- 全仓回归入口：`pytest tests -q`

