# 智能电商推荐系统（V1 / V2）

一个把电商推荐系统从 V1 固定推荐管线演进到 V2 中心化 Multi-Agent Runtime 的项目。当前仓库同时保留 V1 推荐接口与 V2 对话式导购链路，适合展示从“单次推荐 API”到“状态化 Agent 平台”的完整演进。

## 版本对照总览

| 维度 | V1 | V2 |
|------|----|----|
| 系统目标 | 单次推荐 API | 中心化 Agent 平台 |
| 用户入口 | `POST /recommend` / `POST /api/v1/recommend` | `POST /api/v2/sessions` + `POST /api/v2/sessions/{session_id}/messages` |
| 执行模型 | `Supervisor` 固定三阶段编排 | `ShoppingManager + Shared Runtime + Fixed Worker Pool` |
| 状态管理 | 单次请求上下文 | `session memory + user profile + events + snapshots` |
| 聊天语义 | 无 | 导购优先，默认只回答问题/收集偏好 |
| 推荐闭环 | 无长期偏好回写、无异步刷新 | 对话写入 `session memory` -> 后台 `profile_projection` -> `homepage` 快照投影 -> 纯快照读取 |
| 反馈处理 | 无独立 feedback 入口 | `POST /api/v2/users/{user_id}/feedback-events`，写事件并触发 `homepage` 投影刷新 |
| 当前状态 | 已完成并保留兼容接口 | V2 轨道 `12/12` feature 已完成，当前语义为 V2.2 |

## 仓库结构

```text
app/
  main.py                FastAPI 主入口
  shared/                跨版本共享配置、数据、领域模型、可观测性
  v1/                    V1 agent / orchestrator / service 实现
  v2/
    api/                 V2 schema、路由与 session service
    background/          profile/recommendation projection worker
    core/                runtime / policy / hooks / prompts / persistence
    managers/            ShoppingManager 与 planner
    reads/               recommendation snapshot read flow
    workers/             preference / catalog / inventory / comparison / copy
docs/
  v1/                    V1 设计与说明
  v2/                    V2 设计与说明
  career/                Demo、简历与展示材料
  ops/                   部署与本地 baseline 说明
harness/
  v1/                    V1 backlog / progress / validation
  v2/                    V2 backlog / progress / validation
scripts/
  v1/                    V1 smoke 等脚本
  v2/                    V2 smoke、background worker 脚本
  perf/                  baseline 报告脚本
perf/
  k6/                    压测场景
  results/               压测输出目录
archive/
  legacy-polyglot/       历史多语言残留归档，不参与主仓运行
main.py                  向后兼容 shim，继续支持 `uvicorn main:app`
smoke_test.py            V1 smoke shim
smoke_test_v2.py         V2 smoke shim
```

## V1：固定三阶段推荐链路

V1 面向“一次请求内如何协作完成推荐”，主链路如下：

```text
POST /recommend
  -> Supervisor
  -> Phase 1: 画像 + 粗召回
  -> Phase 2: 精排 + 库存校验
  -> Phase 3: 文案生成
  -> RecommendationResponse
```

V1 的展示重点：

- 画像、推荐、库存、文案四类 Agent 围绕一次请求协作。
- `asyncio.gather()` 并行执行与降级策略明确，便于解释关键链路与兜底链路。
- 兼容接口仍保留：`POST /recommend` 和 `POST /api/v1/recommend`。

更完整的 V1 说明见 [docs/v1/README.md](docs/v1/README.md) 和 [docs/v1/设计.md](docs/v1/设计.md)。

## V2：中心化 Multi-Agent 主线

V2 保持中心化架构：用户只与 `ShoppingManager` 交互，固定 worker 池互不通信，worker 只返回结构化结果。当前语义已经升级到 V2.2：

```text
conversation (advisory-first)
  -> session memory
  -> profile_projection event
  -> background profile projection
  -> recommendation_refresh event
  -> homepage snapshot
  -> /recommendations pure read
```

V2 的核心结构：

- `ShoppingManager` 作为唯一对话入口，负责理解输入、规划任务、组织回复。
- `ShoppingTurnPlanner` 是规则驱动 planner，不是自由 LLM planner；固定分流为 `clarify / fallback / advisory / recommendation / comparison`。
- `Shared Runtime` 统一承载 `PolicyGate`、`HookBus`、`PromptRegistry`、`TaskBoard`、`Memory`、`EventProcessor`、`Snapshot Store`。
- 固定 5 个 worker：`preference`、`catalog`、`inventory`、`comparison`、`copy`。
- 持久化对象：`sessions`、`session_turns`、`task_records`、`user_profiles`、`recommendation_snapshots`、`events`。

V2.2 的关键能力：

- 默认聊天走导购/澄清链路，只返回文本回答与偏好抽取结果。
- 只有明确“推荐 / 比较 / 值不值得买”意图时，`/messages` 才返回 `products / comparisons / copies`。
- `/messages` 同步只更新 `session memory`，不会现场改写长期 `UserProfile`。
- 偏好达到稳定或修正条件时，系统只入队 `profile_projection`；后台再生成 `UserProfile` 并继续入队 `homepage` 的 `recommendation_refresh`。
- `GET /api/v2/users/{user_id}/recommendations` 现在是纯快照读取：命中新鲜快照直接返回；过期或 miss 返回旧值/空值，并异步入队 `homepage` 刷新。
- `default` 只保留兼容读取语义；`homepage` 是唯一默认投影目标；`product_page` / `cart` 不再同步现场生成 contextual snapshot。
- `feedback-events` 支持 `click / skip / purchase` 入库，并只触发 `homepage` 刷新，不会在线直接改写 `UserProfile`。
- `GET /api/v2/sessions/{session_id}/turns/{user_turn_number}/trace` 返回单轮 `plan / tasks / projection`，便于调试 planner、worker、tool 与后台投影请求。

完整设计见 [docs/v2/设计V2.md](docs/v2/设计V2.md)。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

Windows / Git Bash:

```bash
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

If you do not activate the repo venv first, run the command through the repo interpreter directly:

```bash
./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

Do not recreate `.venv` with the MSYS2 / `ucrt64` Python from Git Bash. That interpreter cannot install the full FastAPI stack used by this project on Windows.

### 2. 启动 HTTP 服务

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

If the current shell is not using the repo venv yet:

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

兼容入口 `python -m uvicorn main:app` 仍然可用，但 `main.py` 现在只是薄 shim。

### 3. 可选：启动 V2 后台 worker

如果要验证完整投影链路或做 V2 baseline，需要另开一个终端启动 worker：

```bash
python scripts/v2/background_worker.py --database .tmp/v2_runtime/v2.sqlite3 --poll-interval 0.5
```

If the current shell is not using the repo venv yet:

```bash
./.venv/Scripts/python.exe scripts/v2/background_worker.py --database .tmp/v2_runtime/v2.sqlite3 --poll-interval 0.5
```

### 4. 健康检查

```bash
curl http://127.0.0.1:8000/health
```

### 5. 可选环境变量

```bash
ECOM_LLM_API_KEY=your-api-key-here
ECOM_LLM_BASE_URL=https://api.minimax.chat/v1
ECOM_LLM_MODEL=MiniMax-M2.7
ECOM_HOST=0.0.0.0
ECOM_PORT=8000
ECOM_DEBUG=true
```

未配置真实 LLM 时，仓库仍可通过 mock / deterministic 路径完成演示、测试与 baseline。

## API 示例

### V1：单次推荐

```bash
curl -X POST http://127.0.0.1:8000/recommend \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u003","num_items":3,"scene":"homepage"}'
```

### V2：创建会话

```bash
curl -X POST http://127.0.0.1:8000/api/v2/sessions \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u_demo"}'
```

响应示例：

```json
{
  "session_id": "sess_xxx",
  "manager_type": "shopping",
  "created_at": "2026-04-17T10:00:00Z"
}
```

### V2：发送消息

```bash
curl -X POST http://127.0.0.1:8000/api/v2/sessions/sess_xxx/messages \
  -H "Content-Type: application/json" \
  -d '{"message":"预算 3000，平时玩游戏"}'
```

返回重点字段：

- `reply`
- `products`
- `comparisons`
- `copies`
- `preferences_extracted`
- `recommendation_refresh_triggered`
- `agent_details`

行为说明：

- 默认 advisory 消息只返回文本回答，`products / comparisons / copies` 通常为空数组。
- 明确推荐意图，例如 `"recommend a phone under 3000"`，才会返回 `products + copies`。
- 比较意图或 `product_page / cart` 场景下的比较请求，才会返回 `comparisons`。
- `recommendation_refresh_triggered=true` 现在表示“本轮已入队后台投影链路”，不表示同步 refresh 已完成。

### V2：查看单轮 Trace

```bash
curl "http://127.0.0.1:8000/api/v2/sessions/sess_xxx/turns/2/trace"
```

说明：

- `user_turn_number` 表示“第 N 次用户发言”，不是 `session_turns.turn_number` 原始值。
- trace 只覆盖 `/messages` 单轮执行，返回 `plan / tasks / projection`。
- `projection` 摘要表示本轮是否发起了后台 `profile_projection` 或后续投影请求。

### V2：读取推荐快照

```bash
curl "http://127.0.0.1:8000/api/v2/users/u_demo/recommendations?scene=homepage"
```

说明：

- 这是纯快照读取接口，不再同步现场生成 snapshot。
- `fresh hit` 返回 `stale=false`。
- `expired hit` 返回旧 snapshot，`stale=true`，并异步入队 `homepage` refresh。
- `total miss` 返回空结果，`generated_at=null`、`stale=true`、`pending_refresh=true`。
- `default` 优先读 `default`，miss 时 fallback 到 `homepage`。
- `product_page` 需要 `product_id`，`cart` 需要 `product_ids`；miss 时只 fallback 到 `homepage`，不再现场生成 contextual snapshot。

### V2：写入反馈事件

```bash
curl -X POST http://127.0.0.1:8000/api/v2/users/u_demo/feedback-events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"click","scene":"homepage","product_id":"sku-redmi-k80","metadata":{"position":1}}'
```

说明：

- feedback 先写入 `events` 表。
- 系统只为 `homepage` 入队 `recommendation_refresh`，不再默认同时刷新 `default`。
- feedback 仍不直接在线改写 `UserProfile` 主字段。

## 验证与可运行性

当前仓库可直接使用的验证入口：

```bash
python harness/v2/bootstrap.py
pytest tests -k "test_v2_" -q
pytest tests/v1 -q
python smoke_test_v2.py
python smoke_test.py
```

当前主线验证口径：

- `python harness/v2/bootstrap.py`：V2 轨道状态为 `total=12 done=12 pending=0`。
- `pytest tests -k "test_v2_" -q`：覆盖 planner、trace、projection、snapshot read、feedback、background worker 等 V2 核心链路。
- `pytest tests/v1 -q`：覆盖 V1 推荐主链路。
- `python smoke_test_v2.py`：覆盖会话、两轮消息、trace、`profile_projection -> recommendation_refresh` 后台链路、`homepage` 快照读取与 feedback 闭环。
- `python smoke_test.py`：覆盖多用户分群、文案个性化、缺货过滤、冷启动等 V1 关键路径。

## 压测与部署准备

当前阶段需要做压测，但不建议直接拆成多机分布式部署。原因很简单：

- V2 依赖 SQLite + 进程外 background worker 共享同一个 `.sqlite3` 文件。
- 在这一版架构下，HTTP app 和 worker 仍应部署在同一台机器上。
- 因此应先做本地 baseline，再复用同一套场景到服务器复现。

推荐拓扑：

- `2C8G` 机器：运行 `uvicorn app.main:app` + `python scripts/v2/background_worker.py`
- `2C4G` 机器：运行 `k6` 发压，或用于日志 / 结果采集

本地 baseline 命令：

```bash
k6 run perf/k6/v1-steady.js -e BASE_URL=http://127.0.0.1:8000 -e SUMMARY_PATH=perf/results/v1-k6-summary.json
k6 run perf/k6/v2-mixed.js -e BASE_URL=http://127.0.0.1:8000 -e SUMMARY_PATH=perf/results/v2-k6-summary.json
python scripts/perf/build_baseline_report.py --mode v1 --label local-v1 --k6-summary perf/results/v1-k6-summary.json --output-json perf/results/v1-baseline.json --output-md perf/results/v1-baseline.md
python scripts/perf/build_baseline_report.py --mode v2 --label local-v2 --database .tmp/v2_runtime/v2.sqlite3 --k6-summary perf/results/v2-k6-summary.json --output-json perf/results/v2-baseline.json --output-md perf/results/v2-baseline.md
```

当前 V2 baseline 口径：

- `perf/k6/v2-mixed.js` 测的是 `create session -> advisory messages -> pure snapshot read -> feedback`。
- `/recommendations` 不再把同步生成 snapshot 的耗时算进读接口。
- 历史字段名 `refresh_trigger_ratio` 仍保留，但在 V2.2 下表示“消息轮次触发后台投影链路的比例”。
- `recommendation_refresh_success_rate` 表示后台 `recommendation_refresh` 事件完成率，不表示读接口现场构建成功率。

更完整的 baseline 与部署准备说明见 [docs/ops/LOCAL_BASELINE.md](docs/ops/LOCAL_BASELINE.md)。

## 项目亮点

- 从 V1 固定推荐链路演进到 V2 平台化 Agent 架构，展示了从“单次结果生成”到“持续状态闭环”的升级。
- V2 明确分离 manager、worker、tool 与 runtime，用户只与主 agent 交互，worker 之间禁止直接通信。
- 引入 `PolicyGate`、`HookBus`、`PromptRegistry`、`TaskBoard`、`EventProcessor`、`Snapshot Store`，把 Agent 运行时从业务逻辑中抽离。
- 在 V2.2 中进一步把聊天链路与后台画像 / 推荐投影拆开，显著减少聊天实时链路负担。
- 除了 deterministic validation 与 smoke，还补了独立 background worker、k6 场景和 baseline 报告脚本，便于演示与部署前评估。

## 文档索引

- [docs/v1/README.md](docs/v1/README.md)
- [docs/v1/设计.md](docs/v1/设计.md)
- [docs/v2/设计V2.md](docs/v2/设计V2.md)
- [docs/v2/PLAN.md](docs/v2/PLAN.md)
- [docs/career/DEMO.md](docs/career/DEMO.md)
- [docs/career/简历.md](docs/career/简历.md)
- [docs/ops/LOCAL_BASELINE.md](docs/ops/LOCAL_BASELINE.md)
