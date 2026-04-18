你是 V2 Coding Agent。请按以下流程工作：

## Session Startup Ritual

1. 运行 `pwd`，确认在项目根目录
2. 运行 `python harness/v2/bootstrap.py`，确认 V2 轨道完整且能输出当前 next feature
3. 读取 `harness/v2/feature_list.json`，找到优先级最高且依赖已完成的 pending feature
4. 读取 `harness/v2/claude-progress.txt`，了解之前的进度和关键决策
5. 读取 `harness/v2/app_spec.md`，确认当前 feature 的产品目标和边界
6. 读取 `git log --oneline -5`，了解最近的提交

## 实现阶段

7. 宣布你将实现哪个 feature（ID + 名称）
8. 逐条对照该 feature 的 `acceptance_criteria` 实现
9. 只实现一个 feature，不跨 feature 扩张
10. 按该 feature 的 `validation_refs` 运行确定性验证
11. 全部验证通过后，再运行必要的更广泛回归

## 收尾阶段

12. 更新 `harness/v2/feature_list.json`：将该 feature 的 status 改为 `"done"`
13. 更新 `harness/v2/claude-progress.txt`：记录完成内容、关键决策、阻塞或残余风险、下一步建议
14. 如需要提交，提交信息优先使用：
    - `feat(V2-F0X): 功能描述`
    - `progress(v2): 更新 V2-F0X 进度`

## 规则

- **每次只做一个 V2 feature**
- V2 coding loop 只读写 `harness/v2/*`
- 不要修改根目录的 V1 `feature_list.json` 和 `claude-progress.txt`
- 用 `validation_refs` 和 `acceptance_criteria` 判断是否完成，不要自己判断“差不多了”
- 如果依赖的前置 feature 未完成，记录阻塞后停止
- 如果遇到阻塞无法继续，在 `harness/v2/claude-progress.txt` 中记录阻塞原因后停止
