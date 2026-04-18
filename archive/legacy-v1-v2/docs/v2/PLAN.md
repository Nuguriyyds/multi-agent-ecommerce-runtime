# `设计V2.md` 草稿方案

## Summary
- 文档定位为 V2 的正式技术设计文档，目标是把项目从 V1 的“固定推荐编排”升级为“C 端对话购物助手 + 后台推荐闭环”的电商 Agent 系统。
- 文档正文应明确写出：产品目标、系统边界、运行时架构、Tool 协议、状态与存储、推荐闭环、外部 API、兼容策略、降级策略、测试验收。
- 文档整体风格沿用当前中文技术设计文档，但不再按 V1 的单一推荐 DAG 叙述，而是按“Manager Agent + Worker Agents + Shared State + Event”展开。

## Key Changes
- 开篇定义 V2 目标：
  - V2 不再只是推荐 API。
  - V2 的一期产品是登录用户可用的对话购物助手。
  - AI 与用户对话过程中抽取偏好，并在后台异步更新推荐结果，供商品页、首页等消费。
- 明确系统角色：
  - `ShoppingManagerAgent` 是唯一对话入口。
  - `Worker Agents` 只负责能力执行，不直接互相通信。
  - `TaskBoard` 和 `SessionState` 由中心运行时维护。
  - 推荐服务仍保留，但变为兼容层和后台能力。
- 架构章节应固定写成以下结论：
  - 主 agent 采用“自由规划 + 硬护栏”。
  - 允许自由拆任务，但只能调用注册工具。
  - 每轮最多 `8` 个 task step。
  - tool 输入输出必须通过 schema 校验。
  - 运行时终止状态固定为 `reply_ready`、`needs_clarification`、`fallback_used`。
- Tool/MCP 章节应明确：
  - 一期采用 `内部 Tool 接口 + MCP 兼容设计`。
  - 先不做真实 MCP server。
  - 每个工具必须定义 `name`、`description`、`input_schema`、`output_schema`、`side_effect_level`、`execute`。
  - 一期工具集合固定为 `session.read_memory`、`session.write_memory`、`profile.extract_preferences`、`catalog.search_products`、`recommendation.run_workflow`、`product.compare`、`inventory.check`、`copy.generate`、`recommendation.read_snapshot`、`recommendation.request_refresh`。
- 状态与存储章节应明确：
  - 一期只支持登录用户。
  - 会话态与关键业务数据使用最小真实持久化。
  - 存储基线选 `SQLite-backed repositories`。
  - 最少持久化 `SessionStore`、`UserProfileStore`、`RecommendationSnapshotStore`、`EventStore`。
- 记忆与画像章节应固定写出：
  - 对话中先写 `session memory`。
  - 只有稳定偏好才异步回写长期画像。
  - 稳定偏好至少覆盖预算、品类、品牌、用途、排除项中的两类，并满足 confidence 阈值。
- 推荐闭环章节应明确：
  - 推荐刷新不是每轮都跑。
  - 在偏好稳定、核心偏好被修正、或会话结束时异步触发。
  - 刷新任务通过事件表入队，再由进程内 worker 消费。
  - 商品页读取最新 `RecommendationSnapshot`，而不是直接依赖聊天链路同步返回。
- 兼容章节应明确：
  - 保留 `POST /recommend` 和 `POST /api/v1/recommend`。
  - 旧接口内部走新的推荐能力与新画像数据。
  - 新增 V2 会话接口，不直接替换 V1。
- 降级章节应明确：
  - planner 非法输出、超过 step cap、关键工具失败时进入 fallback。
  - fallback 至少能返回安全回复、一次简化推荐结果和一个澄清问题。
  - 推荐与聊天失败互相隔离，不能因为某个 worker 失败导致整个会话崩掉。

## Public APIs / Interfaces / Types
- 文档必须写出新增接口：
  - `POST /api/v2/sessions`
  - `POST /api/v2/sessions/{session_id}/messages`
  - `GET /api/v2/users/{user_id}/recommendations?scene=...`
- 文档必须写出继续保留的兼容接口：
  - `POST /recommend`
  - `POST /api/v1/recommend`
- 文档必须定义新增核心类型：
  - `SessionState`
  - `TaskRecord`
  - `PreferenceSignal`
  - `RecommendationSnapshot`
  - `ToolSpec`
  - `ChatTurnResponse`
- 文档必须说明 `recommendation.run_workflow` 由现有 `Supervisor + user_profile/product_rec/inventory/marketing_copy` 封装而来，作为 V2 内部工具使用。

## Test Plan
- 多轮对话能抽取预算、品类、品牌、排除项，并在偏好稳定后触发异步推荐刷新。
- 信息不足时主 agent 返回澄清问题，而不是直接给低质量推荐。
- worker 之间没有直接调用，所有任务流转都经过 task board。
- 非法 tool 名、非法 tool 输出、step 超限时，系统进入 fallback 且仍返回可用回复。
- 推荐快照更新后，`GET /api/v2/users/{user_id}/recommendations` 能读到新结果。
- `POST /recommend` 和 `POST /api/v1/recommend` 的返回 shape 不变。
- 服务重启后，会话、画像、推荐快照和未完成事件仍可恢复。
- 未达到稳定偏好的对话不会污染长期画像。

## Assumptions
- 一期只做 `C 端购物助手 + 推荐闭环`，B 端运营入口不在本次文档落地范围内。
- 一期只支持登录用户，不处理匿名用户画像合并。
- 一期不接 Redis、MySQL、向量库，先用最小真实持久化完成闭环验证。
- 主 agent 采用接近 Claude Code 的交互风格，但执行层保留硬护栏。
- 文档文件名固定为 `设计V2.md`，内容语言为中文。
