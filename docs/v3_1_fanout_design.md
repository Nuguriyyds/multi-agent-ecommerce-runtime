# V3.1 受控 Fan-out — 设计文档(未实现)

> 本文是 V3.1 的前置设计,**不是 V3.0 范围**。V3.0 的主循环固定串行(spec §7.5),本文说明在什么条件下开放有限并行、以及如何保持 V3.0 已有的 hardening / trace / evidence 保证。

## 1. 为什么 V3.0 选串行

V3.0 的同步 turn runtime 固定采用**单 ready task 串行执行**(`docs/app_spec.md` §7.5),核心原因:

- **HardeningGate 的 Evidence rule** — reply 里承诺的 claim 必须能 map 到某个 `observation_id`,这条规则天然要求 observation 产出顺序可追溯。并行执行会让 observation id 的挂接关系复杂化,evidence 合并需要额外规格
- **Main Agent 的每步 decision 基于前一步 observation** — 这是 bounded loop 的核心产品语义,并行破坏这条因果链
- **Trace 的线性链可读性** — 评审者/审计者阅读线性 trace 比阅读并行 DAG 容易一个数量级
- **V3.0 的产品定位是"可控"优先于"吞吐"** — spec §1 明确 V3.0 要证明"核心循环成立",不追求性能

## 2. V3.1 要解决的问题

当 turn 内需要执行**多个互不依赖**的 specialist 任务时,串行是纯粹的浪费。典型场景:

- candidate_analysis 要从 4 个维度(降噪 / 佩戴 / 续航 / 便携)评估同一批候选 — 4 个维度之间无依赖
- IM 办公协同场景:一条 "帮我准备明天会议" 指令要同时触发 "读日程"、"读会议纪要模板"、"查参会人最近邮件",彼此无依赖
- 跨 specialist 的独立事实查询,例如 "比较这两款 + 查两者的售后政策"(V3.1 会放宽 business boundary,此处仅举例)

V3.0 的做法是 Main Agent 顺序发起 4 次 `call_sub_agent`,每次等前一个返回。V3.1 要把它收拢成"**一次决策,受控 fan-out,一次 gather**"。

## 3. 设计:受控 Fan-out,不是 DAG 并行

V3.1 的并行**不是 LangGraph 式静态并行 Supervisor**(Phase 1 / Phase 2 / Phase 3 固定),而是**主 Agent 每步动态决定要不要 fan-out、fan 几个**。串行是 fan-out `n=1` 的特例,V3.0 代码零改动。

### 3.1 新增类型(`app/v3/models/`)

```python
class FanoutBranch(V3Model):
    branch_id: str
    specialist_role: str              # 或 tool_name
    brief: SpecialistBrief            # 与 V3.0 SpecialistBrief 同构
    depends_on: list[str] = []        # V3.1 仅支持 [] (同层独立);跨层依赖留 V3.2

class FanoutDecision(Action):
    kind: Literal["fanout_sub_agents"] = "fanout_sub_agents"
    branches: list[FanoutBranch]      # 长度 ∈ [2, FANOUT_MAX]
    gather_reason: str                # 为什么要合并这批分支

class GatherObservation(Observation):
    kind: Literal["gather"] = "gather"
    branch_observation_ids: list[str] # 每分支的 observation_id,供 Evidence rule 引用
    # 合并语义:相当于一次"多 observation_id 的复合 observation"
```

`FanoutDecision` 是 `Action` 的新成员,加入 discriminated union。`GatherObservation` 是 `Observation` 的新成员。**不新增 action 形态**,fan-out 是 `call_sub_agent` 的批量形式。

### 3.2 约束

- `FANOUT_MAX = 4` — 硬上限,防失控
- 单 turn 内 fan-out 总分支数 ≤ 8(跨多个 fan-out 决策累加),与 `max_steps=8` 呼应
- 分支之间 `depends_on = []` 在 V3.1 硬性要求 — 有依赖的场景继续走串行,V3.2 再放开
- 分支必须是 specialist 或 tool,**不能是另一个 fan-out**(禁止套娃)

### 3.3 Executor 扩展

`serial_executor.py` 引入 `fanout_executor.py`:

```python
async def execute_fanout(branches: list[FanoutBranch], ...) -> GatherObservation:
    # 每分支独立 task,同时 asyncio.gather 调度
    # 任一分支失败 → 该分支 observation 标记 error,其他分支继续
    # 返回 GatherObservation 合并所有成功分支
    ...
```

V3.0 的 `SerialExecutor` 原封不动;V3.1 的 top-level 还是"主 Agent 每步只产生一个 Action",只是当这个 Action 是 `FanoutDecision` 时,单步内 gather 多分支,对外仍是 1 step。

### 3.4 HardeningGate 扩展

**每个分支独立过 gate**,不是合并后再过:

- 分支 schema 校验:每个 `FanoutBranch.brief` 独立过 schema
- 分支 capability whitelist:每个分支的 specialist_role 独立过权限
- 分支 business boundary:每个分支独立过 scope check
- 分支 timeout:默认每分支 `per_branch_timeout`,gather 整体有 `gather_timeout = max(per_branch) * 1.5`

**gather 后的复合 evidence 校验**:
- `reply_to_user` 引用的 `observation_id` 既可以指向单个分支 observation,也可以指向 gather observation
- gather observation 视为"引用 ≥1 个成功分支"的证据,evidence rule 通过

### 3.5 Trace 扩展

从线性链变成**有根树**:

```
TraceRecord:
  root_step_id: 主 decision step
  branches: [
    {branch_id: "b1", task_id, observations, invocations},
    {branch_id: "b2", task_id, observations, invocations},
    ...
  ]
  gather_step_id: 合并点
```

TraceStore 的读取接口向后兼容 — V3.0 的线性视图是树的"单分支退化"。UI 渲染时,branch ≥ 2 走 "拓扑图" 视图,branch = 1 走"时间轴"视图。

### 3.6 与 V3.0 的向后兼容

- V3.0 代码一行不改 — 串行是 `fanout branches.length == 1` 的特例
- V3.0 的 Action union 加新成员,现有 Action 子类向前兼容
- `serial_executor` 保留,`fanout_executor` 新增;runtime 根据 action kind 路由
- V3.0 的 hardening / trace / evidence 规则全部继承,仅在分支边界扩展

## 4. 与典型并行 Supervisor(LangGraph)的本质差异

| 维度 | LangGraph Supervisor | V3.1 受控 Fan-out |
|---|---|---|
| 何时并行 | **流程预设**(构图时决定) | **主 Agent 每步实时决定** |
| 分支数量 | 编译期固定 | 运行时动态(≤ 4) |
| 分支依赖 | 完整 DAG,可跨层 | V3.1 仅同层独立;V3.2 才放跨层 |
| Fallback | 单 Agent 失败整图失败 | 单分支失败其他继续,主 Agent 消费 gather observation 决策 |
| 业务边界 | 靠 prompt | 每分支独立过 HardeningGate |
| 审计 | 无 | 树形 Trace,每分支可追溯 |
| 方法论 | 通用工作流引擎 | 仍是 Anthropic Harness bounded loop |

## 5. 不在 V3.1 范围

- **跨 turn 并行** — 延迟消费场景(比如 "后台给我整理这个文档,我先聊别的")留给 V3.2 的 `BackgroundTask` + `dynamic_fork`
- **Worker 自动认领** — specialist 仍只响应主 Agent 派单(spec §2.3)
- **Agent 间横向通信** — 仍禁止(spec §2.3)
- **分支间依赖** — V3.1 硬要求 `depends_on=[]`;跨分支依赖回退串行
- **Worktree 隔离** — specialist 共享 runtime,文件系统不隔离(spec §8.8)

## 6. 测试策略(实施时)

- 单分支 fan-out(退化为串行)— 回归 V3.0 行为
- 2 分支无依赖 fan-out — gather 后 evidence 合并通过
- 1 分支失败、1 分支成功 — gather observation 标记部分成功,主 Agent 可降级
- 2 分支都失败 — gather observation 全部 error,主 Agent 进入 fallback
- 超限 fan-out(branches.length > 4)— schema 校验拒绝
- 分支中含 specialist 越权调用 — 该分支 gate 拦截,其他分支不受影响

## 7. 实施工作量估算

| 项 | 工作量 |
|---|---|
| 类型层(Action / Observation 扩展) | 1 天 |
| Executor fan-out 实现 + gather 合并 | 2 天 |
| HardeningGate 分支校验扩展 | 1 天 |
| TraceStore 树形结构 + UI 渲染 | 2 天 |
| 测试覆盖(单/多分支 / 部分失败 / 越权) | 1 天 |
| 文档 + spec 更新 | 0.5 天 |
| **合计** | **~7.5 人天** |

## 8. 版本门槛

V3.1 开始实施的前置条件:
- V3.0 通过所有验收(已通过,见 [harness/v3/validation_matrix.json](../harness/v3/validation_matrix.json))
- 至少一个真实 specialist 场景证明"串行是瓶颈"(比如 candidate_analysis 4 维度确认每次 > 10s)
- MainAgent prompt 稳定(不再频繁修改 decision 格式)
