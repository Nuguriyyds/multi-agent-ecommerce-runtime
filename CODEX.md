# CODEX.md

本仓库根工作区是 V3-first。根入口 `/code` 与 `$code` 默认都指向 `harness/v3/`，不是旧轨道。每一轮 `$code` 都从 fresh coding-agent session 开始，所有命令统一走仓库本地 `.venv`。

## Startup Contract

1. 先运行 `.\.venv\Scripts\python.exe harness/v3/bootstrap.py`，确认轨道健康并拿到当前 next feature。
2. 运行 `rg --files app/v3 tests/v3`，先枚举当前工作区事实。
3. 读 `CLAUDE.md`，确认架构边界和硬约束。
4. 读 `harness/v3/feature_list.json`，只选择优先级最高且依赖已完成的 pending feature。
5. 按该 feature 的 `spec_reference` 去 `docs/app_spec.md` 读取权威章节。
6. 读 `harness/v3/claude-progress.txt`，了解最近决策、阻塞和下一步建议。
7. 去 `harness/v3/validation_matrix.json` 查该 feature 的 `validation_refs`，只按这些确定性命令验收；所有验证统一使用 `.\.venv\Scripts\python.exe -m pytest ...`。
8. 状态优先级固定为 `workspace files > harness/v3/feature_list.json > harness/v3/claude-progress.txt > git log`。
9. `git log --oneline -5` 只作为可选上下文；如果失败，报告 `git history unavailable`，不要根据提交历史推断当前文件是否存在。
10. 一次只实现一个 feature；完成后更新 `harness/v3/feature_list.json` 和 `harness/v3/claude-progress.txt`。

## Boundaries

- 不要把旧轨道状态文件当作当前 backlog。
- 不要从 `archive/legacy-v1-v2/` 复制或复用基类。
- 不要使用全局 `python`、`pytest` 或其他解释器运行 V3 开发命令。
- 当前工作区是 workspace-first 权威事实源；如果工作区与 `feature_list.json` 或 `claude-progress.txt` 冲突，必须停止并报告 `state drift`。
- 只有用户明确要求归档 V2 轨道时才切到 `code-v2`。
