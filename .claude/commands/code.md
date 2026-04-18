你是 V3 Coding Agent。请按以下流程工作：

## Session Startup Ritual

每一轮 `/code` 都从 fresh coding-agent session 开始，只实现一个 feature，所有开发与验证命令统一走仓库本地 `.venv`。
1. 运行 `pwd`，确认在项目根目录。
2. 运行 `.\.venv\Scripts\python.exe harness/v3/bootstrap.py`，确认 V3 轨道完整且能输出当前 next feature。
3. 运行 `rg --files app/v3 tests/v3`，先枚举当前真实工作区。
4. 读取 `CLAUDE.md`，确认架构边界和硬约束。
5. 读取 `harness/v3/feature_list.json`，找到优先级最高且依赖已完成的 pending feature。
6. 按该 feature 的 `spec_reference` 去 `docs/app_spec.md` 读取权威章节。
7. 读取 `harness/v3/claude-progress.txt`，了解之前的进度、决策和阻塞。
8. 读取 `harness/v3/validation_matrix.json`，确认该 feature 的 `validation_refs`。
9. 事实来源优先级固定为：`workspace files > harness/v3/feature_list.json > harness/v3/claude-progress.txt > git log`。
10. `git log --oneline -5` 只作为可选上下文；如果失败，明确报告 `git history unavailable`，但继续以 workspace-first 资料判断当前状态。
11. 后续所有测试统一使用 `.\.venv\Scripts\python.exe -m pytest ...`，不要直接使用全局 `python` / `pytest`。

## 实现阶段

12. 宣布你将实现哪个 feature（ID + 名称）。
13. 逐条对照该 feature 的 `acceptance_criteria` 实现。
14. 只实现一个 feature，不跨 feature 扩张。
15. 按该 feature 的 `validation_refs` 运行确定性验证。
16. 全部验证通过后，再运行必要的更广泛回归。

## 收尾阶段

17. 更新 `harness/v3/feature_list.json`：将该 feature 的 status 改为 `"done"`。
18. 更新 `harness/v3/claude-progress.txt`：记录完成内容、关键决策、阻塞或残余风险、下一步建议。
19. 如需要提交，提交信息优先使用：
    - `feat(V3-F0X): 功能描述`
    - `progress(v3): 更新 V3-F0X 进度`

## 规则

- **每次只做一个 V3 feature**
- 根入口 `/code` 默认只服务 `harness/v3/*`
- 所有 bootstrap、pytest、uvicorn 命令都必须通过仓库本地 `.venv` 执行
- 用 `validation_refs` 和 `acceptance_criteria` 判断是否完成，不要自己判断“差不多了”
- 当前工作区是权威事实源；如果工作区和 `feature_list.json` / `claude-progress.txt` 冲突，必须停止并报告 `state drift`
- 不得根据提交历史推断当前文件缺失；`git log` 不是权威状态源
- 如果依赖的前置 feature 未完成，记录阻塞后停止
- 如果遇到阻塞无法继续，在 `harness/v3/claude-progress.txt` 中记录阻塞原因后停止
