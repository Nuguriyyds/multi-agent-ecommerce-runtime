# 智能电商多 Agent 系统 — V3 技术约束

## 项目概述

按 Anthropic Harness 方法论构建的**中心化 Main-Agent 电商导购平台**。每次会话用 `/code` 启动一个新的 Coding Agent，只做**一个** feature；所有开发和验证命令统一通过仓库本地 `.venv` 执行。

**状态文件**：
- 特性清单：[harness/v3/feature_list.json](harness/v3/feature_list.json)
- 进度交接：[harness/v3/claude-progress.txt](harness/v3/claude-progress.txt)
- 验证矩阵：[harness/v3/validation_matrix.json](harness/v3/validation_matrix.json)
- 完整需求：[docs/app_spec.md](docs/app_spec.md)
- 轨道自检：`.\.venv\Scripts\python.exe harness/v3/bootstrap.py`

**归档**：V1（多 Agent DAG 推荐系统，F01-F12）/ V2（ShoppingManager，V2.2）的完整代码在 [archive/legacy-v1-v2/](archive/legacy-v1-v2/)，仅作参考，**不复用基类、不扩展**。

## V3 架构基线

- **单 Main Agent 入口**：用户只和 Main Agent 对话；sub-agent / tool 是执行手段，不是用户入口
- **Bounded Loop**：observe → decide → act → observe → ...，max_steps=8
- **有限动作集**：`reply_to_user | ask_clarification | call_tool | call_sub_agent | fallback`
- **Turn-scoped TaskBoard**：每轮单独创建 TaskBoard；**串行**执行（一次一个 ready task），不并行
- **两层 Memory**：SessionMemory（工作记忆）+ DurableMemory（跨会话，写入受 gate 门禁）
- **Hardening Gate**：所有 reply / invocation 必须过 gate（action 合法性 + schema + evidence + 业务边界）
- **Fixed Specialist only**：V3.0 只支持固定角色 specialist；**persistent_teammate / dynamic_fork 是 V3.1/V3.2，当前绝对不写**

## 已建立的入口（勿破坏）

- 核心类型：`app/v3/models/`（Pydantic v2 模型；Action 用 discriminated union）
- 能力注册：`app/v3/registry/capability_registry.py`（统一管理本地 tool / sub-agent / MCP tool）
- Provider 抽象：`app/v3/registry/providers.py`（`ToolProvider` / `SubAgentProvider` / `MCPProvider`）
- Main Agent：`app/v3/agents/main_agent.py`（V3.0 唯一用户入口）
- Specialist 基类：`app/v3/specialists/base.py:Specialist`（所有领域 specialist 继承此类）
- MCP 接入：`app/v3/tools/mcp_client.py` + `app/v3/tools/mcp_mock_server/`（V3.0 客户端 + 内置 mock server + RAG 风格工具）
- 配置：`app/v3/config/settings.py:get_settings()`，环境变量前缀 `ECOV3_`
- Hooks：`app/v3/hooks/hook_bus.py:HookBus`（所有 feature 直接 emit，不要私建事件系统）
- 偏好档案：`app/v3/memory/preference_extractor.py` 是 `session_working_memory["confirmed_preferences"]` 和 `durable_user_memory` 裸 dict 的轻量 writer + 只读视图 + revoke 动作；**不是新的 memory 层**，只是现有两层 memory 的 API 投影。路由在 `app/v3/api/preferences.py`，UI 在右侧 tabs。

## 技术约束（所有后续 feature 必须遵守）

### 并发
- 并行用 `asyncio.gather()`，不用线程
- 所有 async 入口函数明确标 `async def`
- **绝不在单轮 turn 内并行执行 task**（spec §7 明确要求串行）

### 数据传递
- 组件间通过 `app/v3/models/` 的 Pydantic model 传数据，不用裸 dict
- Action 子类必须声明 `kind: Literal["..."]` 字段，参与 discriminated union
- 禁止把 `inferred` 字段在 ContextPacket / reply 里升级为 confirmed fact

### Hardening 原则（硬性）
- 所有 reply / invocation 必须过 HardeningGate
- Evidence rule：reply 里承诺的 claim 必须能 map 到某个 `observation_id`
- Business boundary：超范围请求（下单/支付/售后/投诉）一律 fallback，不走 LLM 生成
- Schema validation：Action / ToolArgs / Observation 全部 Pydantic 校验；失败不纠错，直接 fallback

### Memory 写入原则（硬性）
- DurableMemory 只接受 `source=user_confirmed` 的写入，inferred 一律拒
- 关键写操作必须 emit `memory_write` hook

### 外部依赖
- LLM 通过 OpenAI 兼容接口（`app/v3/agents/llm_client.py` 用 httpx）；`OPENAI_API_KEY` 为空时走 mock
- 本地工具和商品目录用 mock（`app/v3/tools/seed_data.py`）
- 外部知识（如 RAG）通过 MCP 协议接入：V3.0 用内置 mock MCP server（同进程 asyncio），接口对齐 MCP `tools.list` / `tools.call`；生产替换只需改 `MCPClient` transport，上层代码不动
- Redis / 向量库 / 真实商品库 V3.0 不接入
- 真要接外部依赖，先加到 `requirements.txt`，再实现 provider（通过 F03 Registry 注册）

### 测试
- 每个 feature 必有对应 `tests/v3/test_f??_xxx.py`
- 用 `pytest-asyncio` 测 async
- 统一使用 `.\.venv\Scripts\python.exe -m pytest ...` 运行测试，不直接使用全局 `python` / `pytest`
- 测试不依赖外部服务（LLM / 网络），全部 mock
- Smoke 测试放 `tests/v3/smoke/`

### 日志
- 用标准库 `logging`（不引入 structlog）
- 输出结构化 JSON：`timestamp / level / trace_id / session_id / turn_number / event / payload`
- 关键操作必须有 log：turn 开始/结束、每次 decision、每次 invocation、每次 fallback、每次 memory_write 判决

### Trace
- 所有 turn 的 decision / invocation / fallback_reason 落盘到 `TraceStore`（F08）
- GET `/api/v3/sessions/{id}/turns/{n}/trace` 从此读（F13）

## 不要做的事

- 不要添加 `harness/v3/feature_list.json` 中未列出的功能
- 不要引入未在 `requirements.txt` 中声明的依赖（需要新依赖先加）
- 不要修改已通过验收的 feature 代码（除非新 feature 有明确依赖需要）
- **不要从 `archive/legacy-v1-v2/` 复制代码过来**（V3 架构完全不同，复用会污染）
- 不要在单轮 turn 内并行执行 task
- 不要绕开 HardeningGate 直接 reply
- 不要把 inferred 字段写进 DurableMemory
- 不要实现 persistent_teammate / dynamic_fork（V3.1/V3.2 再做）
- 不要实现真实商品交易（下单/支付/物流/售后），V3.0 只做导购
