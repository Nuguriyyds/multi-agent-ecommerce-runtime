# Multi-Agent Ecommerce System V3 Workspace

当前仓库根目录已经重置为 V3 工作区。
V1 / V2 的完整源码、文档、测试与压测结果已归档到：
- [archive/legacy-v1-v2/README.md](archive/legacy-v1-v2/README.md)

## Current Status

- 根目录 `app/`、`docs/`、`tests/` 只服务 V3。
- 需求规格：[docs/app_spec.md](docs/app_spec.md)
- 特性清单（15 个 V3 feature + 验收标准 + spec 锚点）：[harness/v3/feature_list.json](harness/v3/feature_list.json)
- 进度交接：[harness/v3/claude-progress.txt](harness/v3/claude-progress.txt)
- 验证矩阵（`validation_refs` 权威入口）：[harness/v3/validation_matrix.json](harness/v3/validation_matrix.json)
- 技术约束：[CLAUDE.md](CLAUDE.md)
- Codex 启动契约：[CODEX.md](CODEX.md)

## Workspace Layout

```text
app/
  main.py              FastAPI app entry
  v3/                  V3 modules
    config/            pydantic-settings (F01)
    models/            core Pydantic types (F02)
    registry/          CapabilityRegistry (F03)
    hooks/             HookBus (F04)
    memory/            Session + Durable + Gate (F05)
    prompts/           PromptRegistry (F06)
    permissions/       PermissionPolicy (F07)
    hardening/         HardeningGate (F07)
    runtime/           TaskBoard + Executor + TraceStore (F08)
    agents/            MainAgent + LLMClient (F09)
    specialists/       Specialist base + domain specialists (F10/F13)
    tools/             local tools + MCP integration (F11/F12)
    api/               V3 endpoints + middleware (F14)
tests/
  test_v3_workspace.py   root smoke placeholder
  v3/                    per-feature tests + smoke scenarios
harness/
  v3/                    feature list + progress + validation matrix + bootstrap
archive/
  legacy-v1-v2/          frozen V1 / V2 reference materials
```

## Bootstrap

All V3 development and validation commands must run through the repo-local `.venv`.
Each `/code` round starts a fresh coding-agent session and implements exactly one feature.

First-time local setup:

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Validate the V3 workflow artifacts and current next feature:

```bash
.\.venv\Scripts\python.exe harness/v3/bootstrap.py
```

Enumerate the current workspace before trusting track metadata:

```bash
rg --files app/v3 tests/v3
```

Start the placeholder V3 app shell:

```bash
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Current placeholder endpoint:

- `GET /health`

## Workflow

- 根入口 `/code` 和 Codex 的 `$code` 默认都指向 V3 轨道。
- 每一轮 `/code` 都从 fresh coding-agent session 开始，只实现一个 feature。
- 所有 bootstrap、pytest、uvicorn 命令都统一通过仓库本地 `.venv` 的 `.\.venv\Scripts\python.exe` 执行。
- 当前状态优先级固定为 `workspace files > harness/v3/feature_list.json > harness/v3/claude-progress.txt > git log`。
- 如果工作区与记录状态冲突，必须停止并报告 `state drift`。
- `git log --oneline -5` 只作为可选上下文；如果失败，报告 `git history unavailable`，不要根据提交历史推断当前文件缺失。
- 每个 feature 的确定性验证命令以 `harness/v3/validation_matrix.json` 为准。
- 旧版 V2 只通过独立入口 `code-v2` 继续使用。

## Legacy Reference

Use the legacy archive for:

- V1 fixed recommendation pipeline reference
- V2 centralized runtime, projection, snapshot, and feedback loop reference
- Historical tests, smoke scripts, harness files, and baseline results
