# 智能电商推荐系统 Demo 手册

这份文档用于 `7-10 分钟面试版` 演示。主线路只依赖当前仓库里已经可运行、已验证的命令；备选线路用于面试官临时想看 HTTP 调用时手工展示。

## 演示目标

你要在最短时间内讲清三件事：

1. 这个项目不是单个聊天机器人，而是从 V1 推荐 API 演进到 V2 中心化 Multi-Agent Runtime。
2. V2.2 已经把链路收口成“导购优先聊天 + 后台画像投影 + `homepage` 纯快照推荐”。
3. 这个仓库不是“只会讲设计”，而是有可运行的验证、smoke 和接口入口。

## 环境准备

### 基础准备

```bash
pip install -r requirements.txt
```

Windows / Git Bash:

```bash
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

If activation is inconvenient, call the repo interpreter directly:

```bash
./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

### 主线路稳定命令

```bash
python harness/v2/bootstrap.py
pytest tests -k "test_v2_" -q
python smoke_test_v2.py
```

### 备选 HTTP 演示启动命令

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

后台 worker 另开一终端：

```bash
python scripts/v2/background_worker.py --database .tmp/v2_runtime/v2.sqlite3 --poll-interval 0.5
```

If needed, replace `python` with `./.venv/Scripts/python.exe`。

## 主线路：7-10 分钟面试版

### 第 1 分钟：项目定位

你先说：

> 这个项目分成两个阶段：V1 是固定三阶段推荐链路，解决一次请求内的推荐、库存和文案协同；V2 则把系统升级成中心化 Agent 平台，用户只和 `ShoppingManager` 对话，worker 不互相通信，聊天链路负责导购和收集偏好，后台再把偏好投影成画像与首页推荐快照。

建议展示：

- [README.md](../../README.md) 的开头和“版本对照总览”部分。

你要强调：

- V1 的核心是“一次请求怎么协作完成推荐”。
- V2.2 的核心是“聊天链路和推荐投影链路怎么解耦”。

### 第 2 分钟：架构图

你先说：

> V2 的系统中心已经不是 V1 的 Supervisor，而是 `ShoppingManager + Shared Runtime + Fixed Worker Pool`。用户只和 manager 交互，worker 只做单域任务，状态统一沉到 memory、event 和 snapshot。

建议展示：

- [README.md](../../README.md) 的 V2 部分。
- 或 [设计V2.md](../v2/设计V2.md) 中的架构图与执行循环。

你只讲这条主链路：

```text
ShoppingManager
  -> preference_worker
  -> catalog_worker
  -> inventory_worker
  -> comparison_worker
  -> copy_worker
  -> session memory / events / user profile / homepage snapshots
```

重点说明：

- manager 统一调度
- worker 禁止直接通信
- runtime 负责通用能力
- planner 是规则驱动，不是自由 LLM planner

### 第 3-4 分钟：展示 V2 已完成

运行：

```bash
python harness/v2/bootstrap.py
```

预期输出重点：

- `V2 harness bootstrap OK`
- `Features: total=12 done=12 pending=0`
- `Current next feature: none`

你怎么讲：

> 这不是只写了设计文档，V2 轨道已经按 feature backlog 全部收口。bootstrap 会检查 V2 轨道文件、状态文件和 next feature，所以这里可以直接证明这条轨道已经完成。

### 第 4-6 分钟：展示 V2 全链路 smoke

运行：

```bash
python smoke_test_v2.py
```

你重点看这些输出：

- `V2 smoke test passed.`
- `Session: sess_xxx`
- `Processed background events: ...`
- `Snapshots: ...`
- `Task counts: ...`
- `Feedback event: evt_xxx`

你怎么讲：

> 这个 smoke 不是只打一两个接口，它把创建会话、两轮消息、单轮 trace、`profile_projection -> recommendation_refresh` 后台链路、`homepage` 快照读取，以及 feedback 最小闭环串起来了。  
> 输出里的 `Processed background events` 说明后台投影链路走通了，`Snapshots` 说明快照落库了，`Task counts` 说明 conversation / background 任务都被记录了，`Feedback event` 说明行为反馈入口和后续首页刷新都可用。

这一段一定要解释两个点：

- 前两轮消息默认可以只是 advisory，对话窗口不一定直接给商品。
- 只有偏好达到稳定条件时，`recommendation_refresh_triggered` 才会变成 `true`，它表示“已入队后台投影链路”，不是“同步刷新完成”。

### 第 6-7 分钟：讲 V2 API 链路

建议打开 [README.md](../../README.md) 的 API 示例部分。

按这个顺序讲：

1. `POST /api/v2/sessions`
2. `POST /api/v2/sessions/{session_id}/messages`
3. `GET /api/v2/sessions/{session_id}/turns/{user_turn_number}/trace`
4. 后台 projection worker 消费事件
5. `GET /api/v2/users/{user_id}/recommendations`
6. `POST /api/v2/users/{user_id}/feedback-events`

你要强调的不是“接口有几个”，而是：

- 会话 API 负责进入对话式导购链路。
- message API 默认返回文本回答和偏好抽取结果；只有明确推荐/比较意图才返回商品或比较结果。
- trace API 负责解释本轮 planner、worker、tool 和 projection 请求。
- recommendation read API 已经变成纯快照读取，不再同步生成 snapshot。
- feedback 先入事件表，再只触发 `homepage` refresh，不在线直接改写 `UserProfile`。

### 第 8 分钟：一句话对比 V1

最后说：

> V1 的 `POST /recommend` 还在，适合展示一次推荐请求内部的 Agent 协作；但系统中心已经在 V2 演进为 `Shared Runtime + ShoppingManager`，而且 V2.2 进一步把聊天链路和推荐投影链路拆开了，这才是这个项目真正的增量价值。

如果时间还够，可以补一句：

> 所以这个项目既能讲一次推荐链路，也能讲中心化 Agent Runtime、状态闭环和后台投影系统。

## 备选线路：现场手工调 HTTP

这个线路只在面试官明确要看接口调用时使用。它能展示公开 API，但不适合拿来证明后台事件消费，因此一旦现场不稳定，立刻切回 `python smoke_test_v2.py`。

### 1. 启动服务

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. 启动后台 worker

```bash
python scripts/v2/background_worker.py --database .tmp/v2_runtime/v2.sqlite3 --poll-interval 0.5
```

### 3. PowerShell 手调 V2 API

```powershell
$base = "http://127.0.0.1:8000"

$session = Invoke-RestMethod `
  -Method Post `
  -Uri "$base/api/v2/sessions" `
  -ContentType "application/json" `
  -Body '{"user_id":"u_demo"}'

$sessionId = $session.session_id

$turn1 = Invoke-RestMethod `
  -Method Post `
  -Uri "$base/api/v2/sessions/$sessionId/messages" `
  -ContentType "application/json" `
  -Body '{"message":"budget 3000"}'

$turn2 = Invoke-RestMethod `
  -Method Post `
  -Uri "$base/api/v2/sessions/$sessionId/messages" `
  -ContentType "application/json" `
  -Body '{"message":"phone apple gaming"}'

$trace = Invoke-RestMethod `
  -Method Get `
  -Uri "$base/api/v2/sessions/$sessionId/turns/2/trace"

$rec = Invoke-RestMethod `
  -Method Get `
  -Uri "$base/api/v2/users/u_demo/recommendations?scene=homepage"

$feedback = Invoke-RestMethod `
  -Method Post `
  -Uri "$base/api/v2/users/u_demo/feedback-events" `
  -ContentType "application/json" `
  -Body '{"event_type":"click","scene":"homepage","product_id":"sku-redmi-k80","metadata":{"position":1}}'
```

如果你想现场展示聊天窗口直接给商品，可以再补一轮显式推荐消息：

```powershell
$turn3 = Invoke-RestMethod `
  -Method Post `
  -Uri "$base/api/v2/sessions/$sessionId/messages" `
  -ContentType "application/json" `
  -Body '{"message":"recommend a phone under 3000"}'
```

### 4. 预期能看到什么

- `$session` 里会有 `session_id`
- `$turn1` / `$turn2` 里会有：
  - `reply`
  - `preferences_extracted`
  - `recommendation_refresh_triggered`
  - `agent_details`
- advisory 路径下，`products / comparisons / copies` 默认可能是空数组
- `$trace` 里会有 `plan`、`tasks`、`projection`
- `$rec` 里会有 `scene`、`products`、`copies`、`stale`、`pending_refresh`
- `$feedback` 里会有 `accepted` 和 `event_id`

### 5. 这条线路怎么讲

你可以说：

> 这条线路展示的是公开 API 行为，适合让面试官看 session、message、trace、recommendation read、feedback 这几个入口是如何串起来的。  
> 如果要证明后台 `profile_projection` 和 `recommendation_refresh` 事件已经被消费，我会切回 `smoke_test_v2.py`，因为那个脚本把内部事件处理也一起覆盖了。

## 常见追问与回答要点

### 1. V1 和 V2 的本质区别是什么？

回答要点：

- V1 解决的是“一次请求里怎么做推荐”。
- V2 解决的是“推荐如何形成持续闭环与平台化抽象”。
- V2.2 进一步把聊天窗口和后台推荐投影解耦。

### 2. 为什么 worker 不能直接通信？

回答要点：

- 避免隐式依赖和耦合扩散。
- 所有跨域协作都回到 manager，链路更可观测。
- 便于测试、回放和后续接入更多 manager。

### 3. 为什么首版用 SQLite，不直接上 MQ / PostgreSQL？

回答要点：

- 首版目标是把中心化 runtime 链路跑通，不是先堆生产基础设施。
- SQLite + WAL 足够支撑本地开发、单机验证和 deterministic 测试。
- repository 边界已经独立，后续替换数据库与队列的成本可控。

### 4. 为什么 feedback 不直接改写 UserProfile？

回答要点：

- feedback 先作为事件进入系统，再作用于后续首页快照投影。
- 这样可以让闭环成立，同时避免把一次点击 / 跳过直接固化为长期画像。
- 这是工程上更保守也更可解释的边界。

### 5. `/recommendations` 为什么改成纯快照读取？

回答要点：

- 聊天窗口的实时任务应该是导购、问答和收集偏好，而不是现场构建推荐。
- 推荐快照生成挪到后台后，读接口和聊天接口的时延都更稳定。
- miss / expired 仍然可用，因为系统会返回旧值或空值，并异步入队刷新。

### 6. 失败时怎么降级？

回答要点：

- manager 有 `reply_ready / needs_clarification / fallback_used`
- scene 缺上下文时优先 clarify 或 fallback 到 `homepage`
- 非关键 worker 失败尽量组织部分结果
- 后台 refresh 失败保留旧 snapshot 并记录 failed event

## 失败兜底方案

### 情况 1：现场环境有问题，HTTP 起不来

直接切回：

```bash
python harness/v2/bootstrap.py
python smoke_test_v2.py
```

理由：

- 这两条命令已经足够证明“功能完成度 + 全链路可运行性”。

### 情况 2：面试官要求看 V1

补一条：

```bash
python smoke_test.py
```

然后只讲一句：

> V1 保留的是固定三阶段推荐链路，方便展示推荐、库存和文案如何围绕一次请求协作；V2 才是这次重点升级的平台化部分。

### 情况 3：手工 HTTP 调用不稳定

立刻切回：

```bash
python smoke_test_v2.py
```

并说明：

- 手工 HTTP 更适合展示接口形态
- smoke 更适合展示完整闭环

### 情况 4：依赖环境切错 Python 解释器

先确认：

```bash
pip install -r requirements.txt
```

On Windows / Git Bash, prefer:

```bash
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

如果 `smoke_test_v2.py` 仍提示找不到 FastAPI，可以设置：

```bash
ECOM_SMOKE_V2_PYTHON=你的_python_路径
```

## 最后提醒

真正的主讲顺序不要变：

1. 先讲项目定位
2. 再讲架构升级
3. 再证明 V2 已完成
4. 再跑全链路 smoke
5. 最后讲接口与 V1 对比

这样最稳，也最容易让面试官快速理解你做的不是“拼几个 API”，而是把推荐系统抽象成了一个可运行、可验证、可扩展的中心化 Agent 平台。
