# 智能电商推荐系统 V2 — App Spec

## 产品目标

V2 的目标不是继续扩展单一推荐 API，而是建立一套面向大型电商系统的 Agent 平台，并先落地 C 端对话购物助手。

本期核心链路：

`conversation -> preference extraction -> profile update -> async refresh -> recommendation snapshot`

## 系统形态

### C 端：对话购物助手

- 用户通过自然语言表达预算、品类、品牌、用途和排除项。
- `ShoppingManager` 是唯一用户交互入口。
- 主 manager 调度薄领域 worker 完成偏好抽取、选品、比较、库存和文案能力。

### 推荐侧：后台个性化服务

- 会话中的结构化信号写入 `session memory`。
- 当偏好稳定或被修正时，系统更新 `user profile` 并异步触发推荐刷新。
- 商品页、首页、购物车页消费推荐快照，而不是重新进入对话。

### B 端：运营 / 商家助手

- B 端与 C 端不共用同一个人格化 agent。
- 一期只预留 `merchant_ops` manager 注册位，不实现业务入口。

## 一期范围

### 做

- `shopping` manager
- 共享 runtime：`Policy Gate / Hook Bus / PromptRegistry / TaskBoard / Memory / EventProcessor / Snapshot Store`
- 薄领域 worker：`preference / catalog / comparison / inventory / copy`
- SQLite 持久化
- 进程内后台任务
- 内部 MCP-compatible ToolRegistry
- V2 会话 API、推荐读取 API、feedback 事件入口

### 不做

- 匿名用户画像合并
- 独立消息队列 / 独立 worker 进程
- 真实 MCP server
- 正式 Scheduler 子系统
- 多模态输入
- 完整 feedback 学习闭环
- B 端业务入口

## 关键约束

- 只有主 manager 与用户交互。
- worker 之间禁止直接通信。
- worker 只能被主 manager 调度，并返回结构化结果。
- `Policy Gate` 只支持 `allow / reject / clarify`。
- scene 规则采用显式上下文：
  - `default`、`homepage` 可仅依赖用户画像
  - `product_page` 必须带 `product_id`
  - `cart` 必须带 `product_ids`

## 核心 API

- `POST /api/v2/sessions`
- `POST /api/v2/sessions/{session_id}/messages`
- `GET /api/v2/users/{user_id}/recommendations`
- `POST /api/v2/users/{user_id}/feedback-events`

## 首版验收结果

- 能创建和持久化 V2 会话。
- 能在多轮对话中抽取预算、品类、品牌、用途、排除项。
- 能在偏好稳定后更新长期画像并异步刷新推荐快照。
- 能按 `default/homepage/product_page/cart` 的 scene 规则读取推荐快照。
- 能记录 feedback 事件但不触发学习闭环。
- V2 状态轨道与现有 V1 harness 状态文件相互隔离。

## 非目标

- 不要求在本次实现完整多 manager 业务平台。
- 不要求在本次实现完整 V2 runtime 业务代码。
- 不以 V1 `Supervisor` 作为 V2 顶层执行模型。
