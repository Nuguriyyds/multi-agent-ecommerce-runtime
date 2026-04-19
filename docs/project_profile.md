# V3 多 Agent 可审计运行时 — 作品说明

## 项目定位

V3 是一个**业务无关的可审计 Agent Runtime**,按 Anthropic Harness 方法论构建,电商导购是首个验证场景。项目重点不在"推荐耳机"本身,而在把复杂 AI 协作系统拆成**可控、可追溯、可审计**的工程结构:用户只与 Main Agent 对话,Specialist、Tool 与 MCP 能力统一通过 CapabilityRegistry 调度,每一步 decision 前后过 HardeningGate 四道硬化,每一次 observation / invocation / fallback 都落盘 TraceStore。

**对标 Anthropic Harness 方法论**(Main Agent + Bounded Loop + Fixed Specialist),**而非 LangGraph 风格的静态并行 Supervisor**。V3 的主 Agent 每一步**实时**选择下一个 action,串行执行,每一步都可解释、可干预、可回滚,天然适合企业场景。

## 核心能力

- **有界 Main-Agent Loop**: 每个 turn 最多 8 步,按 `observe → decide → act → observe` 闭环执行,避免无限工具调用或失控推理
- **固定 Specialist 协作**: shopping_brief / candidate_analysis / comparison / recommendation_rationale 四类领域角色通过 sub-agent 协议返回结构化 observation
- **统一能力注册**: 本地工具、sub-agent、MCP tool 都注册到 CapabilityRegistry,调用方不直接依赖具体实现
- **MCP 风格外部知识接入**: 内置 mock MCP server 暴露 rag_product_knowledge,证明外部知识源可以通过 provider 方式替换
- **HardeningGate 安全边界**: action 白名单、schema 校验、evidence 引用、business boundary 四道检查,拒绝越界下单/支付/售后请求
- **两层 Memory + 写入 Gate**: session 工作记忆 + durable 跨会话记忆;durable 层**只接受 `source=user_confirmed`**,拒写 inferred / tool_fact,防画像污染
- **显式对话式偏好档案**: 对话中采集到的偏好形成可视化档案,支持逐条撤销(触发 memory revoke + hook)— 区别于 RFM 等隐式画像的不可审计特性
- **Trace 与结构化日志**: 每个 turn 保存 decisions / invocations / observations / fallback_reason,UI 可直接展示完整决策链
- **测试闭环**: 每个 feature 有对应 pytest,smoke 场景覆盖澄清、完整推荐链路、业务边界 fallback

## 可演示场景

1. **澄清链路**: 用户只说"帮我选个礼物",Main Agent 不直接推荐,而是识别缺少预算、对象、品类等槽位并继续追问
2. **完整导购链路**: 用户输入"完整演示:3000 左右通勤降噪耳机,不要 Beats",系统依次执行需求结构化、候选分析、商品对比、推荐理由生成,最终给出带证据链的建议
3. **业务边界 fallback**: 用户要求"帮我下单"时,HardeningGate 触发边界保护,明确拒绝代下单,但保留继续导购咨询的出口
4. **显式偏好档案 + 实时投影**: 对话中提到的偏好(预算 / 场景 / 排斥品牌)实时显示在右侧面板;点击任意条目 × 按钮触发 memory revoke,推荐卡片同步刷新 — 符合"AI 知道我什么必须对我透明可撤销"的企业合规要求

## 迁移到飞书课题二:IM 办公协同智能助手

V3 Runtime 的每个组件都能一对一映射到 IM 办公协同场景:

| V3 组件 | 映射到 IM 办公协同助手 |
|---|---|
| Main Agent + Bounded Loop | IM 消息入口的多步决策(理解指令 → 调工具 → 回复) |
| Fixed Specialist Pool | 文档摘要 / 日程冲突检测 / 任务派发 / 待办提取 / 跨群消息合并 specialist |
| HardeningGate business boundary | IM 权限边界(不越权发消息 / 不删他人文件 / 不读隐私频道) |
| TraceStore | **操作审计** — IM 企业场景硬需求 |
| MCP Client | 接入飞书 OpenAPI / 文档 API / 日历 API / 会议 API |
| Durable Memory + 写入 Gate | 跨会话记工作习惯(会议时段偏好、常用群、消息摘要粒度),拒写未确认推断 |
| Turn-scoped TaskBoard | IM 任务链路可视化与阻塞追溯 |
| **显式偏好档案 + revoke** | IM 场景下"我的助理知道什么"必须对用户透明可撤销 |

## 与典型 Multi-Agent 方案的差异化

| 维度 | LangGraph 风格并行 Supervisor | V3 动态 Bounded Loop |
|---|---|---|
| 编排 | 静态 DAG(Phase 1/2/3) | 动态 observe→decide→act,每步实时选 action |
| 画像 | 隐式 RFM / embedding 分群 | **显式对话偏好**,user_confirmed 才存,可撤销 |
| 审计 | 无 / 需额外接入 | 原生 TraceStore + HardeningGate |
| Memory | 直接覆盖 | 两层 + 写入 Gate(拒 inferred) |
| 扩展 | 硬编码 Redis / SQL | MCP 抽象,provider 可热替换 |

**V3 的定位不是"功能最多"的 Multi-Agent 框架,而是"最可控、最可审计、最符合 Harness 方法论"的多 Agent 运行时**。后续扩展场景不改主循环语义,只接入新的 provider / specialist。

## 个人贡献表述建议

可在简历或报名材料中描述为:

> 设计并实现一个按 Anthropic Harness 方法论构建的**可审计动态 Agent Runtime**,支持固定 Specialist 协作、CapabilityRegistry 统一能力注册、MCP 风格外部知识接入、HardeningGate 运行时安全校验、TraceStore 决策链追踪、两层受控 Memory + 显式对话式偏好档案。电商导购为首个验证场景,架构可迁移至 **IM 办公协同 / 流程引擎 / 数据分析**等多 Agent 协作场景。项目包含 15 个 harness feature、完整 pytest/smoke 验证与可复现本地启动流程。

## 与比赛初筛的关联

该项目适合作为"过往 AI 相关项目/作品"提交。它展示的是 AI Agent **工程化能力**:编排、工具接入、证据约束、可观测性、测试与演示闭环。当前业务壳仍是电商导购,不提前绑定正式赛题;通过初筛进入正式开发阶段后,同一套运行时可以迁移到 IM 协同、流程引擎、数据分析等 Agent 场景。

**作品组合**:本项目 + 作者另一项目 **InfiniteChat**(Spring Boot / Netty / RocketMQ / Redis,994 RPS / P99 80ms 的分布式 IM 后端)= **IM 系统工程能力 × Agent Runtime 工程能力**,天然契合课题二"基于 IM 的办公协同智能助手"。

## 本地复现

```powershell
.\.venv\Scripts\python.exe harness/v3/bootstrap.py
.\.venv\Scripts\python.exe -m pytest tests/v3 -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动后访问 `http://127.0.0.1:8000/ui`:
- 左侧聊天窗,可切换"演示模式"运行预置脚本
- 右侧三个 tab:**我的偏好**(档案 + revoke) / **为你推荐**(个性化商品卡) / **Trace**(决策链)
