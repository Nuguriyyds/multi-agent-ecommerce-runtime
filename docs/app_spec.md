# 智能电商 Agent 平台 V3 — App Spec

> 这份文档是 V3 的初始化产品规格。  
> 它的作用不是直接下发编码任务，而是定义系统目标、功能范围、版本边界和预期结果，作为后续技术设计、任务拆解和状态组织的上游输入。

## 1. 产品目标

V3 的目标不是继续演化 V2 的“电商推荐闭环”，而是建立一套**中心化 Main-Agent 平台**。

这套平台的核心特征是：

- 用户只和一个主 agent 交互。
- 主 agent 是唯一负责人和最终决策者。
- 主 agent 可以多步观察、决策、行动，再决定是否继续调用能力或回复用户。
- 子 agent、worker、tool 都只是执行手段，不是新的用户入口。

V3 的首个 first-party reference scenario 是**电商导购助手**。  
电商场景的作用是约束平台，而不是重新把系统定义成推荐系统或画像系统。

V3.0 的产品重点是：

- 证明主 agent 的 `observe -> decide -> act` 核心循环成立。
- 证明平台具备 runtime、trace、guardrails、capability registry 和多 agent 执行位。
- 证明业务能力可以以后通过 MCP 或其他 provider 方式可插拔扩展。

## 2. 系统形态

### 2.1 角色关系

V3 采用中心化架构：

```text
User
  -> Main Agent
  -> Sub-Agent Tool / Tool Provider
  -> External Capability (MCP / local provider / adapter)
```

### 2.2 Main Agent

- Main Agent 是唯一用户入口。
- Main Agent 负责理解用户输入、读取上下文、选择下一步 action、判断何时终止。
- Main Agent 可以直接调用普通 tool，也可以调用 `sub-agent tool`。
- Main Agent 对每一步 action 的结果负责，不能把最终决策外包给子 agent。
- V3.0 采用**主 agent 显式派单**模型，所有 tool / sub-agent 调用都必须由 Main Agent 明确发起。

### 2.3 Sub-Agent

- Sub-Agent 是**主 agent 专属的高级工具**。
- Sub-Agent 不直接与用户交互。
- Sub-Agent 之间不直接通信。
- Sub-Agent 返回结构化 observation，不直接输出最终用户回复。
- Sub-Agent 只能响应主 agent 发起的调用，不能自行抢任务、不能自行拉起其他 agent。

平台层允许三种子 agent 形态：

- `fixed_specialist`
- `persistent_teammate`
- `dynamic_fork`

版本要求：

- `V3.0` 必做：`fixed_specialist`
- `V3.1` 预留：`persistent_teammate`
- `V3.2` 预留：`dynamic_fork`

### 2.4 Tool / Provider

- Tool 是最小执行能力单元。
- Provider 是 tool 或 sub-agent 的承载接口。
- Provider 可以是本地实现、内部 adapter，或未来的外部 MCP 接入。
- Main Agent 不直接依赖业务代码细节，只依赖 registry 暴露的能力元数据和结构化结果。

## 3. 首个业务场景：电商导购助手

V3.0 的首个业务场景固定为**对话式电商导购助手**。

### 3.1 用户价值

用户希望通过自然语言完成以下事情：

- 表达购买需求
- 被追问和澄清
- 查找候选商品
- 比较多个商品
- 获得购买建议或推荐理由

### 3.2 首批用户任务

V3.0 限定的首批任务：

1. 表达需求  
   例如预算、品类、品牌偏好、使用场景、排斥项。
2. 主 agent 追问与澄清  
   当信息不足时，先缩小范围，而不是直接给低质量结果。
3. 查找候选商品  
   由主 agent 调度能力提供者拿到候选结果。
4. 做商品比较  
   比较差异、优缺点、适用人群。
5. 给购买建议 / 推荐理由  
   结合需求和比较结果，生成结构化建议与文本回复。

### 3.3 当前不纳入 V3.0 主线

以下能力不写入 V3.0 主线：

- 首页推荐闭环
- 用户画像投影
- feedback 学习
- recommendation snapshot
- 购物车闭环
- 下单、支付、履约

这些能力后续可以通过 MCP/provider 追加，而不是成为 V3.0 的初始化负担。

## 4. 一期范围

### 4.1 做

- 中心化 Main-Agent Loop
- 会话状态与单轮 trace
- 两层受控 memory 与 memory hardening
- 中心化 Agent Team 与 Team Protocol
- 权限系统、Hook 系统、系统提示词与上下文压缩
- 有限 action 集合与 step cap
- tool registry / sub-agent registry
- `fixed_specialist` 形态的子 agent
- 最小 first-party 导购参考能力
- 受控 fallback、clarification、timeout、schema 校验
- `POST /api/v3/sessions`、`POST /api/v3/sessions/{session_id}/messages`、`GET /api/v3/sessions/{session_id}/turns/{turn_number}/trace`

### 4.2 不做

- 多顶层 manager / 多产品入口平台
- persistent teammate 的完整上下文治理
- dynamic fork 子 agent
- Worktree 隔离
- 完整后台任务系统与正式定时调度
- 插件市场、热安装与不可信远程插件执行
- 推荐闭环、画像、快照、feedback 系统
- 强依赖真实 MCP server
- 订单、加购、支付等完整电商链路

## 5. 核心循环

V3.0 的核心循环不是脱离业务的通用 planner，而是围绕“对话式电商导购”构建的 **bounded agentic loop**。

它的产品前提固定为：

- 主 agent 每一步都要基于最新 observation 决定下一步 action。
- 主 agent 的决策空间受到 action 白名单、`max_steps`、capability whitelist 和 provider 边界约束。
- 一轮 `/messages` 可以依次经过多个导购任务节点，但每一步都由主 agent 显式派单。
- 这份核心循环规格是后续系统加固、runtime 和多 agent 设计的上游约束。

V3.0 的基础循环固定为：

```text
observe
  -> decide
  -> act
  -> observe
  -> decide again
  -> reply / ask_clarification / fallback
```

### 5.1 导购任务到 Agent Loop 的映射

导购场景下，一轮主循环不是只处理一个抽象意图，而是会在以下 5 类任务节点之间推进：

#### 5.1.1 需求表达

- 主 agent 读取：用户原始需求、会话历史摘要、已知约束、当前 capability 列表。
- 允许采取的 `Action`：`ask_clarification`、`reply_to_user`、`call_sub_agent`、`fallback`。当用户已经明确要求推荐且约束足够时，也可转入 `call_tool` 进入商品查找。
- 何时调用 tool：只有当需求已具备可检索硬约束，并且用户明确要求找商品时。
- 何时调用 sub-agent：需要把松散描述整理成可执行 shopping brief，或需要先归纳偏好与排斥项时。
- 何时继续下一步观察：收到需求整理结果、缺失槽位或约束冲突后，主 agent 继续判断是进入澄清还是进入商品查找。
- 何时终止并回复用户：本轮只需要确认理解、复述当前需求，或明确提出下一步澄清问题时。
- 何时进入 fallback：请求超出导购范围、超出支持品类，或需求本身不可安全处理时。

#### 5.1.2 澄清需求

- 主 agent 读取：当前缺失槽位、冲突约束、上一轮 observation、用户最新补充。
- 允许采取的 `Action`：`ask_clarification`、`reply_to_user`、`fallback`。
- 何时调用 tool：V3.0 默认不在澄清节点直接调用商品工具。
- 何时调用 sub-agent：V3.0 默认不在澄清节点调用 sub-agent，避免把澄清职责外包成厚中间层。
- 何时继续下一步观察：用户补充新信息后，主 agent 重新回到“需求表达”或推进到“查找候选商品”。
- 何时终止并回复用户：已经形成一个具体、可回答的澄清问题，或已经足以确认进入下一阶段时。
- 何时进入 fallback：用户拒绝补充关键条件、请求持续冲突，或无法再安全缩小范围时。

#### 5.1.3 查找候选商品

- 主 agent 读取：结构化 shopping brief、硬约束、上一轮澄清结果、当前可用检索能力。
- 允许采取的 `Action`：`call_tool`、`call_sub_agent`、`ask_clarification`、`reply_to_user`、`fallback`。
- 何时调用 tool：需要检索候选商品、读取商品事实、过滤明显不符合约束的结果时。
- 何时调用 sub-agent：需要更高层的检索策略、候选整理方式或结果解释框架时。
- 何时继续下一步观察：收到候选列表、空结果、约束冲突或排序线索后，主 agent 继续判断是补问、重试、进入比较，还是直接给出初步建议。
- 何时终止并回复用户：已经拿到足够候选，并且用户本轮只需要“先给我几款”或“先列出可选项”时。
- 何时进入 fallback：检索能力不可用、重复检索仍无可用结果，或当前能力无法产生可信候选时。

#### 5.1.4 比较商品

- 主 agent 读取：候选商品集合、用户关心的比较维度、已有商品 observation。
- 允许采取的 `Action`：`call_tool`、`call_sub_agent`、`ask_clarification`、`reply_to_user`、`fallback`。
- 何时调用 tool：需要补充商品差异、核心参数、优缺点或适用场景等事实信息时。
- 何时调用 sub-agent：需要生成更高层的比较框架、权衡解释或推荐依据时。
- 何时继续下一步观察：比较结果仍不完整、比较维度不明确，或候选不足以形成结论时。
- 何时终止并回复用户：已经能够说明差异、优缺点、适用人群和推荐倾向时。
- 何时进入 fallback：比较对象不足、商品事实严重缺失，或比较结论无法被当前 observation 支撑时。

#### 5.1.5 给购买建议

- 主 agent 读取：澄清后的需求、候选与比较 observation、剩余不确定项。
- 允许采取的 `Action`：`reply_to_user`、`ask_clarification`、`call_sub_agent`、`fallback`。当证据明显不足时，可回退到“查找候选商品”或“比较商品”并触发新的 `call_tool`。
- 何时调用 tool：只有在建议所依赖的事实明显不足、必须补充证据时。
- 何时调用 sub-agent：需要综合多条 observation 生成结构化推荐理由、购买建议或注意事项时。
- 何时继续下一步观察：用户追问“为什么”、要求改变约束，或当前建议缺少足够证据时。
- 何时终止并回复用户：已经能给出推荐结论、推荐理由、关键权衡和必要提醒时。
- 何时进入 fallback：现有证据不足以支持可靠建议，或请求超出当前导购边界时。

### 5.2 有限 Action 集合与业务含义

`Action` 的产品语义固定为：

- `reply_to_user`：基于当前 observation 给出最终用户可见回复，并结束本轮。
- `ask_clarification`：提出最小必要澄清问题，并结束本轮等待用户下一条消息。
- `call_tool`：调用普通工具获取事实、候选、比较结果或其他结构化 observation。
- `call_sub_agent`：调用 `fixed_specialist` 等主 agent 专属高级能力，返回结构化 observation，不直接生成最终用户回复。
- `fallback`：在请求超界、能力不可用、证据不足或无法安全继续时受控退出本轮。

Main Agent 不能执行集合外动作。

### 5.3 继续条件、终止条件与任务转移

一轮导购对话可以在多个任务节点之间推进，但必须满足以下规则：

- 只有在得到新的 observation、用户补充信息，或主 agent 明确切换到下一个任务节点后，循环才能继续。
- `ask_clarification`、`reply_to_user`、`fallback` 都会终止当前轮次，不允许在用户不可见的情况下继续隐藏调用。
- `需求表达` 常见地推进到 `澄清需求` 或 `查找候选商品`。
- `澄清需求` 常见地回到 `需求表达`，或直接推进到 `查找候选商品`。
- `查找候选商品` 常见地推进到 `比较商品` 或 `给购买建议`。
- `比较商品` 常见地推进到 `给购买建议`，必要时回退到 `查找候选商品`。
- `给购买建议` 默认以 `reply_to_user` 结束；若证据不足，可回退到 `查找候选商品` 或 `比较商品` 补充 observation。

以下任一条件满足时终止本轮：

- 选择 `reply_to_user`
- 选择 `ask_clarification`
- 选择 `fallback`
- 达到 `max_steps`

V3.0 默认 `max_steps = 8`。

### 5.4 决策原则

- 每次 `decide -> act` 前都要经过 hardening gate，而不是裸决策。
- 先判断当前位于哪个导购任务节点，再决定下一步 action。
- 信息不足时优先 `ask_clarification`
- 需要外部能力时选择 `call_tool` 或 `call_sub_agent`
- 已有结果足够时直接 `reply_to_user`
- `call_sub_agent` 是补充性的高级能力位，不替代主 agent 的主循环责任
- 非法 action、step 超限或无法安全继续时进入 `fallback`

## 6. 平台能力

V3.0 必须具备以下平台能力。
其中系统加固的优先级是**导购可信度优先**：平台首先要确保主 agent 不会在边界不清、证据不足或调用失控时乱答。

### 6.1 Capability Registry

平台统一维护能力清单，至少支持：

- tool 的注册、发现、调用
- sub-agent 的注册、发现、调用
- 能力元数据读取
- 主 agent 的权限边界控制

### 6.2 State / Trace / Task

V3.0 需要最小真实状态层：

- `SessionState`
- `LoopState`
- `TraceRecord`
- `TaskRecord`

它们的作用分别是：

- `SessionState`：保存会话级上下文，并显式区分 `session working memory` 与 `durable user memory` 的读取视图
- `LoopState`：保存单轮循环状态、当前任务节点、当前 task 与结构化 observation
- `TraceRecord`：记录每一步 observation、decision、action、result，解释主 agent 为什么继续、终止或 fallback
- `TaskRecord`：记录 turn 内任务事实与主 agent 显式发起的调用事实

### 6.3 Memory Hardening

在 V3.0 中，memory 属于系统加固的一部分，而不是“尽量多记”的上下文缓存。
它的目标是：只记对后续导购决策有帮助、且不会污染系统判断的信息。

memory 在 V3.0 中主要解决以下问题：

- 防止上下文污染
- 防止未确认推断被“记成事实”
- 防止过期偏好覆盖当前真实需求
- 防止主 agent 把商品事实、推荐结论或运行时中间产物错误写入长期记忆

#### 6.3.1 两层记忆模型

V3.0 固定采用两层受控记忆：

- `session working memory`
- `durable user memory`

V3.0 不采用 Claude Code 式多层记忆架构，也不在本期展开 teammate 共享记忆或人格化长期记忆。

#### 6.3.2 `session working memory`

`session working memory` 的作用是保存当前 session 内对导购有帮助的短期上下文。

可写内容：

- 当前预算、品类、品牌偏好、使用场景、排斥项
- 已澄清结论
- 当前 unresolved questions
- 当前候选范围与比较维度
- 用户明确修正过的本轮偏好

不写内容：

- 原始整段对话全文
- 未确认推断
- 商品事实原文 payload
- task / invocation 内部执行细节

生命周期规则：

- 只服务当前 session，不承担跨会话记忆职责
- 可被当前轮次的新信息持续修正

#### 6.3.3 `durable user memory`

`durable user memory` 的作用是保存跨会话仍然有价值、且不容易从当前状态现推出来的稳定用户偏好。

可写内容：

- 用户明确表达或明确确认的长期偏好 / 排斥项
- 明确确认的稳定预算带
- 明确确认的品牌长期倾向或长期禁忌

不写内容：

- 单次临时需求
- 会话内试探性表达
- 行为推断
- 商品事实
- 推荐结果
- feedback 学习结果

生命周期规则：

- 跨会话保留
- 始终可被用户新输入覆盖、修正或撤销

#### 6.3.4 写入规则

memory 写入必须满足以下规则：

- 只有结构化、已确认的信息才能写入 memory。
- 推断默认只可参与当前 reasoning，不能直接落 memory。
- `durable user memory` 只允许“用户明确表达 / 明确确认”触发写入。
- 写入前必须经过 `MemoryWriteDecision` 判断，而不是由主 agent 直接落库。

#### 6.3.5 读取与冲突规则

memory 读取必须满足以下规则：

- memory 是导购提示，不是强制真相。
- 当前用户输入优先级高于 `durable user memory`。
- `durable user memory` 只能影响澄清和检索方向，不能覆盖当前显式需求。
- memory 不能替代 tool/provider observation，不能作为商品事实来源。
- 当前用户明确修正时，旧 memory 立即降级、失效或被覆盖。
- 冲突未解决时，主 agent 先澄清，不把冲突内容继续当成稳定记忆使用。

#### 6.3.6 边界规则

V3.0 的 memory 边界固定为：

- 不做行为学习型 memory
- 不做 recommendation / profile projection memory
- 不做多层长期人格记忆
- 不做 teammate 共享记忆

### 6.4 业务边界加固

V3.0 只服务“对话式电商导购”主循环，不承担完整电商事务系统职责。平台必须强制以下业务边界规则：

- V3.0 只负责导购，不负责下单、支付、账户、售后、客服流程。
- 超出导购边界的请求不能伪装成导购回答，必须澄清、拒绝或 `fallback`。
- 主 agent 不能把用户请求隐式改写成别的业务任务。
- `business scope check` 必须在本轮早期完成，并进入后续 hardening gate 判断。

### 6.5 证据与事实加固

导购场景下，商品事实与推荐结论都必须可追溯。平台必须强制以下规则：

- 没有 tool/provider observation 支撑的内容，不能作为商品事实进入回复。
- 价格、库存、参数、商品特征等信息默认都属于“工具事实优先”范围，不允许主 agent 用常识补全。
- 比较结论必须能回溯到候选商品 observation，不能凭常识脑补。
- 购买建议必须建立在“需求已澄清 + 候选已检索/比较 + evidence sufficiency 达标”之上。
- 证据不足时，只允许继续补查、`ask_clarification` 或 `fallback`，不允许硬给建议。
- 回复中要区分“事实”“推断”“建议”，不能把推断说成已验证事实。

### 6.6 执行控制加固

主循环中的每一步调用都必须受到执行约束，不能因为 agentic 决策而绕过平台边界：

- `Action` 不仅有白名单，还要做任务节点合法性检查。
- `澄清需求` 节点默认不能直接做商品事实承诺。
- 进入 `reply_to_user` 之前，必须通过最小可答性检查：业务边界合法、证据足够、无未处理关键冲突。
- `call_tool` / `call_sub_agent` 都必须经过 capability whitelist、schema 校验、timeout 和 `max_steps` 约束。
- `Observation` 必须结构化，且必须能区分正常结果与错误结果。
- 非法 capability、非法 action、step 超限或未通过 hardening gate 的决策都必须被拦截。
- sub-agent 仍是主 agent 显式派单的高级工具，不能自动认领、不能隐式串联其他 agent。

### 6.7 降级与可解释性加固

平台必须支持受控降级，并让 trace 能解释“为什么没继续答”：

- provider 失败、超时、空结果、冲突结果都要生成结构化 error observation。
- 主 agent 必须先消费 error observation，再决定重试、澄清、降级或 `fallback`。
- 单个 provider 失败不能让整轮崩溃，但也不能让主 agent 假装拿到了结果。
- `fallback` 必须是有原因的受控退出，不是模糊搪塞。
- `guardrail hit reason` 和 `fallback reason` 必须进入 trace，可解释本轮命中了哪条硬化规则。

### 6.8 权限系统

V3.0 需要最小但明确的权限系统，用来约束“谁能调什么能力”，而不是把权限判断散落在各层实现里。

权限系统的核心目标是：

- 防止主 agent、specialist、tool 之间越权调用
- 防止 specialist 在受限自主下越过自身边界
- 防止 hook、prompt 或上下文注入间接提升权限

V3.0 的权限规则固定为：

- 用户不能直接调用 tool 或 specialist。
- 主 agent 拥有最高调度权限，但仍受 capability whitelist 和业务边界约束。
- specialist 只能调用分配给自身角色的 tools，不能调用其他 specialist，不能直接升级成主 agent。
- tool 不能反向触发主 agent 决策，也不能拉起新的 specialist。
- 权限判断必须在调用前完成，不能在调用后补判。

### 6.9 Hook 系统

V3.0 需要一层受控 Hook 系统，用来在不破坏主循环边界的前提下扩展观测与治理能力。

Hook 系统的定位固定为：

- 用于观测、记录、审计、轻量 enrich
- 不用于重写主循环产品语义
- 不允许绕过 hardening gate
- 不允许把 hook 写成隐藏业务编排层

V3.0 的 hook 触发点至少包括：

- turn start
- decision created
- task created
- invocation before
- invocation after
- memory write proposed
- fallback triggered
- turn finished

Hook 系统必须满足：

- hook 可以读上下文，但默认不能直接改写最终用户回复
- hook 可以附加结构化元信息，但不能绕过权限系统
- hook 的失败不能拖垮主循环，只能进入受控降级或被忽略

### 6.10 系统提示词

V3.0 需要明确的系统提示词体系，但它必须是受控 prompt 架构，而不是任意拼接的文本堆。

系统提示词的目标是：

- 固化主 agent 和 specialist 的角色边界
- 固化业务边界、证据规则、memory 规则和权限规则
- 让 prompt 成为稳定策略层，而不是临时补丁层

V3.0 的 prompt 层级固定为：

- platform prompt
- scenario prompt
- role prompt
- task brief
- current turn context

规则固定为：

- 平台层 prompt 优先级最高，定义通用边界与 hardening 原则
- 场景层 prompt 约束电商导购场景
- 角色层 prompt 区分 main agent 与 specialist 角色职责
- task brief 只补当前任务目标，不重写平台边界
- specialist 不能自行改写平台 prompt，只能消费分配给自己的 brief

### 6.11 上下文压缩

V3.0 需要受控的上下文压缩机制，但它的目标不是省 token 本身，而是防止上下文膨胀损害决策质量。

上下文压缩必须解决：

- 长会话导致的无关信息堆积
- 关键信息被后续轮次淹没
- 未确认推断在压缩后被误写成稳定事实
- guardrail 相关冲突在压缩后丢失

V3.0 的压缩规则固定为：

- 压缩结果必须是结构化 `ContextPacket`，而不是松散自然语言大摘要
- 压缩必须保留：已确认偏好、未解决冲突、当前候选范围、比较维度、未完成澄清问题、当前 memory 冲突状态
- 压缩不能把推断升级为事实
- 压缩不能删除仍影响 hardening gate 的关键信息
- 原始全文不是长期默认输入；主循环优先读取压缩后的当前可用上下文视图

### 6.12 技能系统

V3.0 的能力体系包含三层：tool（原子操作）、sub-agent（高级能力位）、以及 skill（可复用的多步组合流程）。

技能系统的定位固定为：

- skill 是**预定义的复合工作流**，将多个 tool/sub-agent 调用编排成一个可复用的流程。
- skill 不是新的用户入口，也不是新的 agent 角色；它是主 agent 可选的执行加速路径。
- skill 的存在不改变主 agent 的最终决策权——主 agent 可以选择走 skill 加速，也可以选择逐步推理。

skill 与 tool、sub-agent 的区别：

| 层级 | 粒度 | 示例 |
|------|------|------|
| tool | 单次原子操作 | 商品检索、商品详情查询 |
| sub-agent | 自主调工具的高级能力位 | candidate_analysis_specialist |
| skill | 预编排的多步流程 | "标准导购流"= 解析需求 → 检索 → 比较 → 生成建议 |

V3.0 的技能系统要求：

- skill 通过 `CapabilityRegistry` 注册，和 tool / sub-agent 共享同一个发现与调用框架。
- skill 执行过程中的每一步仍受 hardening gate、权限系统、timeout 和 `max_steps` 约束。
- skill 内部的每步 invocation 仍进入 `TraceRecord`，不能因为走了 skill 就跳过 trace。
- skill 不能绕过主 agent 的终止条件；如果 skill 执行中触发 `fallback` 条件，主 agent 仍可中断。

V3.0 的边界：

- V3.0 不要求丰富的 skill 库，只要求平台具备 skill 注册与执行框架。
- 首版可以只定义一个参考 skill（如"标准导购流"），用于验证框架成立。
- skill 的编辑、组合、热更新不在 V3.0 范围内。
- 后续版本可以通过插件系统注册新 skill，扩展常见导购模式。

### 6.13 错误恢复

§6.7 定义了 provider 层面的降级规则（tool/sub-agent 失败后怎么 fallback）。本节补充**系统层面的错误恢复策略**，覆盖 LLM 调用失败、主循环中途异常、部分完成 turn 的状态处理。

#### 6.13.1 错误分类

V3.0 的错误分为三类：

- **provider 错误**：tool 或 sub-agent 调用失败、超时、返回非法格式。处理方式见 §6.7。
- **LLM 错误**：主 agent 或 specialist 的 LLM 调用本身失败（超时、返回非法格式、拒绝响应）。
- **系统错误**：runtime 内部异常（状态写入失败、task board 损坏、未预期的代码异常）。

#### 6.13.2 LLM 调用失败

LLM 是主循环 `decide` 步骤的执行引擎，它的失败会直接阻断主循环推进。

处理规则：

- LLM 超时或返回非法格式时，runtime 生成 `LLMErrorObservation`，进入和 provider 错误相同的消费流程。
- LLM 调用允许重试，但重试次数受系统级上限约束（V3.0 默认最多 2 次重试）。
- 重试仍然失败时，主 agent 进入 `fallback`，向用户返回"系统暂时无法处理"的受控回复。
- LLM 返回的 action 不在白名单内时，视为非法 action，不重试，直接进入 `fallback`。

#### 6.13.3 部分完成 turn 的状态处理

如果 turn 在执行到 step N 时出现不可恢复的错误，前面已完成的 step 的 observation 和 trace 不能丢失。

处理规则：

- 已产出的 `TraceRecord` 和 `TaskRecord` 保留，不因为后续失败而回滚。
- 已写入 `session working memory` 的信息保留（它们来自已确认的用户输入，不是错误产物）。
- 未完成的 `TurnTask` 标记为 `failed`，并记录失败原因。
- turn 以 `fallback` 终态结束，`TurnResult` 中包含错误摘要和已完成步骤信息。
- 用户下一轮 `/messages` 时，主 agent 可以读取上一轮的 trace 和 session working memory，从中断处的上下文继续，而不是从零开始。

#### 6.13.4 系统错误

系统错误（runtime 内部非预期异常）的处理规则：

- 捕获异常，记录错误日志和 trace。
- 不尝试自动恢复主循环状态（V3.0 不做 transaction 级状态回滚）。
- 向用户返回通用错误响应，不暴露内部异常细节。
- session 保持有效，用户可以继续发送新消息。

#### 6.13.5 不可重试 vs 可重试

V3.0 的重试规则固定为：

| 错误类型 | 可重试 | 重试上限 |
|---------|--------|---------|
| LLM 超时 | 是 | 2 次 |
| LLM 返回非法格式 | 是 | 2 次 |
| LLM 返回非法 action | 否 | — |
| Provider 超时 | 由 §6.7 决定 | — |
| Provider 返回错误 | 由 §6.7 决定 | — |
| 系统内部异常 | 否 | — |
| 权限校验失败 | 否 | — |
| Hardening gate 拦截 | 否 | — |

## 7. Runtime

V3.0 需要一层明确的同步 turn runtime，用来把主循环从“产品规则”落成“可执行的一轮消息运行时”。
这一层的重点不是 memory/store 设计，而是：主循环如何把一轮用户消息拆成可执行的小任务、按依赖受控推进，并产出 trace 与 task 事实。

### 7.1 Runtime 目标与范围

V3.0 的同步 turn runtime 固定服务以下公开入口：

- `POST /api/v3/sessions`
- `POST /api/v3/sessions/{session_id}/messages`
- `GET /api/v3/sessions/{session_id}/turns/{turn_number}/trace`

范围边界固定为：

- runtime 覆盖同步 turn 执行，不覆盖 background runtime。
- runtime 不展开异步任务调度系统，不定义后台任务编排。
- runtime 依赖 `SessionState`、`TraceRecord`、`TaskRecord` 的读写接口，但不在本轮规格中展开 memory/store 的详细模型。

### 7.2 Runtime 核心职责

V3.0 的同步 turn runtime 至少负责：

- 初始化本轮执行上下文
- 承载 bounded loop 的 step 执行
- 管理 turn 内任务系统
- 加载当前 prompt 层级与压缩后的上下文视图
- 调度 tool / sub-agent invocation
- 在每步前后接入 hardening gate
- 产出 trace / task / terminal result

### 7.3 Turn-scoped TaskBoard

V3.0 的 runtime 采用 `Turn-scoped TaskBoard`：

- 每轮 `/messages` 都创建一个独立任务板。
- 任务板只服务当前 turn，不是全局调度中心。
- 任务板负责记录 step、依赖、状态转移和 invocation 事实。
- 任务板的目标是让一轮执行可解释、可追踪、可阻塞，而不是模拟通用项目管理系统。

### 7.4 任务模型与粒度

V3.0 中的 task 是**可跟踪、可分配、可完成、可阻塞的小工作单元**。

它必须满足：

- 不是整轮 turn
- 不是整段用户需求
- 不是简单 todo 项
- 必须能表达“谁先谁后、谁依赖谁、当前是否 blocked”

推荐的 runtime 任务模型固定为：

- `TurnTask`
- `TaskStatus`
- `depends_on`
- `blocked_reason`
- `invocations`

`TaskStatus` 的首版状态集合固定为：

- `pending`
- `ready`
- `running`
- `blocked`
- `completed`
- `failed`
- `skipped`

### 7.5 单轮串行执行器

V3.0 的 turn executor 固定采用串行执行：

- 主 agent 每次 `decide` 后只发起一个下一步 task。
- executor 每次只执行一个 ready task。
- 依赖关系仍然要被记录，但 V3.0 不做 turn 内并行执行。
- 串行执行的目标优先级高于吞吐量优化，因为 V3.0 首先要保证可控与可解释。

同步 turn runtime 的推荐推进顺序是：

```text
receive message
  -> create TurnRuntimeContext
  -> create TurnTaskBoard
  -> observe current state
  -> decide next action
  -> create next task
  -> run hardening gate
  -> execute one task
  -> record invocation / observation / trace
  -> decide again
  -> reply / clarification / fallback
```

### 7.6 Tool / Sub-Agent Invocation 与 Task 的关系

V3.0 中，task 与 invocation 的关系固定为：

- task 是 runtime 的执行单元。
- tool / sub-agent 调用是 task 内发生的 invocation 事实。
- invocation 不单独升级成全局任务系统。
- 一个 task 可以没有 invocation，例如直接形成 `reply_to_user`、`ask_clarification` 或 `fallback`。
- 一个 task 也可以包含一次受控 invocation，并在返回 observation 后驱动下一次 decision。

### 7.7 Hardening Gate 与 Runtime 的关系

hardening gate 不只是策略说明，而是同步 turn runtime 中每一步执行前后的必经环节：

- task 创建前检查当前 action 是否允许。
- invocation 前检查 capability whitelist、schema、timeout 和 `max_steps`。
- `reply_to_user` 前执行最小可答性检查。
- 未通过检查时，runtime 必须转入 error observation、`blocked` 或 `fallback` 分支，而不是继续隐藏执行。

### 7.8 Runtime 与 State / Trace / Storage 的边界

V3.0 的 runtime 与状态层的边界固定为：

- runtime 负责在执行过程中读取 `SessionState` 中的 `session working memory` 与 `durable user memory` 视图。
- runtime 负责在每次 decision、task 创建、task 状态变化、invocation 和终态时写入 `TraceRecord`。
- runtime 负责把 turn 内任务事实写入 `TaskRecord`。
- runtime 可以提出 memory 写入请求，但是否允许写入由 memory hardening 规则决定。
- runtime 读取的不是无界全文上下文，而是可用的 memory 视图与压缩后的 `ContextPacket`。
- runtime 可以依赖持久化接口，但本轮规格不展开 SQLite schema、memory projection 或后台存储策略。

### 7.9 后台任务边界

V3.0 的 runtime 主线只覆盖同步 turn 执行，但平台预留最小后台任务边界，用于未来承载脱离当前用户 turn 的延后工作。

在 V3.0 中，后台任务必须满足：

- 不阻塞当前用户回复
- 不直接参与本轮 `reply_to_user`
- 只能由显式事件或系统策略触发
- 必须可追踪、可终止、可归因
- 不能绕过权限系统、hardening gate 和 trace 体系

V3.0 不要求完整后台任务系统，只要求为后续版本保留 `BackgroundTask` 一类平台概念位。

### 7.10 定时调度边界

V3.0 不展开正式定时调度系统，但保留最小的 `SchedulePolicy` 语义占位。

这一层在后续版本中的职责可包括：

- 维护型后台任务触发
- 记忆清理与上下文卫生任务
- 插件健康检查与刷新
- 未来 Worktree / lease 清理

V3.0 不要求：

- cron 级 DSL
- 分布式调度
- 调度可视化管理台
- 时间驱动的业务主线功能

## 8. Multi-Agent Platform

V3.0 的多-agent 平台不是自治团队系统，而是**中心化 Main-Agent 平台上的团队协作层**。
它的目标是把你图里的 `Agent 团队 / 团队协议 / 自主代理` 收成一个可实现、可约束、可解释的首版规格。

### 8.1 V3.0 团队模型

V3.0 的团队模型固定为：

- 用户只与主 agent 交互。
- 主 agent 是唯一负责人和最终决策者。
- 团队成员来自预注册的固定角色池。
- specialist 不直接与用户交互。
- specialist 之间不直接横向通信。
- 团队不是自治组织，而是主 agent 的能力编排面。

### 8.2 `fixed_specialist` 是 V3.0 唯一多-agent 形态

V3.0 的唯一多-agent 形态是 `fixed_specialist`。

它必须满足：

- 可被主 agent 选择调用
- 可在自身边界内自主调工具
- 只返回结构化 observation
- 不返回最终用户回复
- 不拥有独立产品身份
- 不拥有独立最终决策权

V3.0 中的“自主”只允许是**受限自主**：specialist 可在允许边界内自主使用工具，但不能自主决定团队拓扑、不能自主拉起其他 specialist、不能替代主 agent 做最终决策。

### 8.3 Agent Team

`AgentTeam` 是主 agent 当前可调度的 specialist 角色集合。

在 V3.0 中：

- `AgentTeam` 来自固定角色池，而不是动态组队。
- `AgentTeam` 不是会话绑定的自治队伍，而是主 agent 当前可见的 delegation 面。
- 主 agent 负责从角色池中按需选择 specialist，而不是把控制权交给团队自身。

固定角色池可包含首版参考角色，例如：

- `shopping_brief_specialist`
- `candidate_analysis_specialist`
- `comparison_specialist`
- `recommendation_rationale_specialist`

### 8.4 Team Protocol

V3.0 的团队协作协议固定为：

1. 主 agent 发起 `TeamTask`
2. specialist 接收结构化 `SpecialistBrief`
3. specialist 在边界内自主调用允许的 tools
4. specialist 返回 `TeamTaskResult` / `SpecialistObservation`
5. 主 agent 决定是否继续调度、切换 specialist、回退到 tool、回复用户或 `fallback`

推荐的协议链路是：

```text
Main Agent
  -> create TeamTask
  -> send SpecialistBrief
  -> specialist internal tool use (optional)
  -> SpecialistObservation
  -> Main Agent decide adopt / retry / switch / reply / fallback
```

### 8.5 与 Runtime 的绑定

多-agent 在 V3.0 中不是独立执行系统，而是同步 turn runtime 的一部分：

- specialist 调用在 runtime 中仍然是 task 内 invocation。
- specialist task 仍受 `Turn-scoped TaskBoard` 管理。
- 如果 specialist 内部继续调工具，这些工具调用仍受 capability whitelist、schema、timeout、step cap 和 hardening gate 约束。
- `TaskRecord` 需要能区分主 agent task、specialist task、specialist 内部 invocation。
- `TraceRecord` 必须能解释为什么选择某个 specialist、specialist 做了什么、为什么采纳或不采纳其 observation。

### 8.6 与 Hardening 的绑定

specialist 不得绕过已有的系统加固规则：

- specialist 不能绕过业务边界规则。
- specialist 不能绕过证据规则和商品事实规则。
- specialist 不能绕过 memory 规则。
- specialist 内部工具结果不能直接当最终用户回复。
- specialist 返回的 observation 仍要经过主 agent 的最小可答性检查。

### 8.7 V3.0 边界与后续升级

V3.0 的多-agent 边界固定为：

- `s17 自主代理`：只允许“受限自主”，即 specialist 可自主调工具，但不能自主决定团队拓扑。
- `s18 Worktree 隔离`：明确不属于 V3.0 主线。
- `s19 MCP 与插件`：保持为 provider / MCP-compatible 扩展位，不写成当前多-agent 协议前提。

版本升级位固定为：

- `V3.0`：`fixed_specialist` + 中心化团队
- `V3.1`：`persistent_teammate`，引入会话级持续协作成员
- `V3.2`：`dynamic_fork`，引入临时派生 agent

### 8.8 Worktree 隔离边界

`Worktree` 隔离只在更强的代码执行 agent、动态派生 agent 或高风险插件运行场景中才有必要。

因此在 V3.0 中：

- `fixed_specialist` 不要求独立 worktree
- specialist 不拥有独立文件系统视图
- specialist 共享主平台运行时，但仍受权限系统与 capability scope 限制

后续如果引入 `dynamic_fork` 或代码执行型 specialist，worktree 隔离至少要解决：

- 文件系统状态隔离
- 进程级执行隔离
- 凭据与密钥可见性隔离
- 插件可见性隔离
- forked agent 生命周期回收

## 9. 扩展模型

### 9.1 Provider 抽象

平台通过以下抽象暴露能力：

- `ToolProvider`
- `SubAgentProvider`

如有需要，可在技术设计层引入 `WorkerProvider` 作为内部执行适配层，但不要求成为首版用户可见概念。

### 9.2 MCP 与插件口径

MCP 是 V3 的推荐外部能力接入方式，但不是首版唯一实现前提。

V3.0 的要求是：

- capability 接口设计兼容 MCP 风格的 schema / metadata / invocation 模式
- 平台可通过内部 adapter 挂载本地能力
- 后续商品推荐、用户画像、更多业务功能可通过 MCP/provider 方式接入
- 插件是本地能力打包与注册方式，不替代 provider / registry / hardening 体系

V3.0 不要求：

- 必须接真实 MCP server
- 必须实现完整的 MCP discovery / transport / lifecycle
- 必须有插件市场或插件热安装

### 9.3 插件模型

V3.0 中的插件模型固定为“能力包”，而不是独立产品入口。

插件可以声明的内容包括：

- provider 绑定
- role prompt 扩展
- hook 注册
- policy metadata

插件必须满足：

- 通过 `CapabilityRegistry` 注册，而不是绕过 registry 直接生效
- 受权限系统、hardening gate、trace 和 prompt 分层约束
- 不能直接暴露新的用户主入口
- 不能在 V3.0 中引入不可信远程执行

### 9.4 First-Party 参考能力

为了避免首个电商场景成为空壳，V3.0 允许存在最小的 first-party 参考能力：

- 商品检索能力
- 商品比较能力
- 一个固定角色的 `shopping_specialist` 子 agent

`shopping_specialist` 在 V3.0 中的定位是主 agent 可选调用的高级能力位，不是自治队友，也不是会自动认领任务的 worker。
它返回的 observation 仍然必须受相同的业务边界规则、证据规则和 hardening gate 约束，不能绕过主循环硬化层。

推荐的链路是：

```text
Main Agent
  -> product search / compare tools
  -> shopping_specialist (fixed_specialist, optional)
  -> structured observation
  -> Main Agent reply
```

V3.0 默认不做 worker 自动认领、persistent teammate 或自主协作调度；更强的能力后续通过 MCP/provider 扩展。

## 10. 公开接口与核心类型

### 10.1 首版公开 API

- `GET /health`
- `POST /api/v3/sessions`
- `POST /api/v3/sessions/{session_id}/messages`
- `GET /api/v3/sessions/{session_id}/turns/{turn_number}/trace`

接口语义：

- `/messages` 是唯一主交互入口
- `/trace` 是一期必备 debug / review 能力
- 首版不提供推荐快照、画像、feedback 等 V2 风格 API

### 10.2 核心平台类型

首版必须定义以下核心类型：

| 类型 | 作用 |
|------|------|
| `SessionState` | 会话级上下文 |
| `LoopState` | 单轮主循环状态 |
| `AgentDecision` | 主 agent 当前决策结果 |
| `Action` | 当前选择的有限动作 |
| `Observation` | 工具或子 agent 返回的结构化观察 |
| `TraceRecord` | 记录 observation / decision / action / result |
| `TaskRecord` | 记录 turn 内任务事实 |
| `MemoryLayer` | 记忆层级标识 |
| `MemoryEntry` | 结构化记忆条目 |
| `MemorySource` | 记忆来源与确认方式 |
| `MemoryStatus` | 记忆当前状态 |
| `MemoryWriteDecision` | 本轮记忆写入是否允许 |
| `PermissionPolicy` | 平台权限规则 |
| `PermissionDecision` | 当前调用是否被允许 |
| `HookPoint` | hook 触发点 |
| `HookEvent` | hook 收到的结构化事件 |
| `HookResult` | hook 的处理结果 |
| `PromptRegistry` | prompt 分层注册表 |
| `PromptLayer` | 当前 prompt 所在层级 |
| `ContextPacket` | 压缩后的结构化上下文包 |
| `CompressionPolicy` | 上下文压缩策略 |
| `TurnRuntimeContext` | 一轮消息的执行上下文 |
| `TurnTaskBoard` | 当前 turn 的任务系统 |
| `TurnTask` | 最小可执行任务单元 |
| `TaskStatus` | task 的状态枚举 |
| `InvocationRecord` | task 内发生的 tool / sub-agent 调用事实 |
| `HardeningGateResult` | 当前 step 是否允许继续、阻塞或降级 |
| `TurnResult` | 当前 turn 的终态结果 |
| `BackgroundTask` | 脱离当前 turn 的后台工作单元 |
| `BackgroundTaskStatus` | 后台任务状态 |
| `SchedulePolicy` | 定时触发与维护任务策略 |
| `AgentTeam` | 主 agent 当前可调度的 specialist 角色集合 |
| `AgentRole` | specialist 的稳定角色标识 |
| `TeamTask` | 主 agent 发起的 specialist 工作单元 |
| `TeamTaskResult` | specialist 完成团队任务后的结果封装 |
| `SpecialistBrief` | 主 agent 发送给 specialist 的结构化 brief |
| `SpecialistObservation` | specialist 返回的结构化 observation |
| `DelegationPolicy` | 主 agent 选择 specialist 的调度策略 |
| `PluginManifest` | 插件声明与边界元数据 |
| `PluginCapability` | 插件暴露的能力集合 |
| `CapabilityRegistry` | 统一管理可用能力 |
| `ToolProvider` | 普通工具能力接口 |
| `SubAgentProvider` | 子 agent 能力接口 |
| `SkillDefinition` | 技能的声明、步骤编排与元数据 |
| `SkillExecutionContext` | 技能执行时的运行上下文 |
| `ErrorCategory` | 错误分类（provider / LLM / system） |
| `RetryPolicy` | 可重试错误的重试策略与上限 |
| `LLMErrorObservation` | LLM 调用失败时的结构化错误观察 |

这些类型在 V3.0 中还要满足以下语义：

- `Observation` 必须带来源、状态和可归因语义，能区分正常结果与错误结果。
- `SessionState` 必须明确区分 `session working memory` 与 `durable user memory` 的读取视图。
- `LoopState` 不仅保存当前任务节点，还要关联当前 task、ready task 和 blocked task。
- `AgentDecision` 必须能表达为什么继续调用、为什么澄清、为什么 `fallback`，以及对应的下一步任务意图。
- `TraceRecord` 必须能记录 decision、task 创建、task 状态变化、invocation、guardrail 命中、memory 读取、memory 拒写原因和终态。
- `TaskRecord` 记录的是 turn 内任务事实，而不是泛化 todo；它只记录主 agent 显式发起的调用与状态变化，不把未执行的想法伪装成事实。
- `MemoryLayer` 在 V3.0 中固定为 `session working memory` 与 `durable user memory` 两层。
- `MemoryEntry` 只能保存结构化、可归因、已确认的信息，不保存原始整段对话全文。
- `MemorySource` 必须标识来源与确认方式；推断来源默认不可直接写入长期层。
- `MemoryStatus` 至少要能表达 `active / superseded / revoked / conflicted` 一类状态变化。
- `MemoryWriteDecision` 用于表达 allow / deny / replace / revoke 一类写入判断及原因。
- `PermissionPolicy` 用于定义 main agent、specialist、tool 各自可调用的能力边界。
- `PermissionDecision` 用于表达 allow / deny 以及拒绝原因，且必须发生在调用前。
- `HookPoint` 用于定义受控 hook 生命周期节点，不等于任意业务注入位。
- `HookEvent` 必须是结构化事件，不能把整套内部状态不加约束地暴露给 hook。
- `HookResult` 只能表达观测、附加元数据或受控 enrich，不能直接改写最终用户回复。
- `PromptRegistry` 用于维护 platform / scenario / role / task brief 的 prompt 分层。
- `PromptLayer` 用于表达 prompt 的责任边界和优先级，而不是自由拼接片段。
- `ContextPacket` 是压缩后的结构化上下文视图，不是原始全文替代品。
- `CompressionPolicy` 用于表达何时压缩、保留什么、什么信息禁止压掉。
- `TurnRuntimeContext` 用于承载当前 turn 的执行输入、loop 状态、task board 和终态结果。
- `TurnTaskBoard` 必须是 turn-scoped，而不是全局任务系统。
- `TurnTask` 必须支持 `depends_on`、`blocked_reason` 和 `invocations` 语义。
- `InvocationRecord` 用于表达 task 内的调用事实，而不是独立的全局任务。
- `HardeningGateResult` 必须能表达“允许继续 / 阻塞 / 降级 / fallback”，并可包含 memory write allow / deny 的结果说明。
- `TurnResult` 至少覆盖 `reply`、`clarification`、`fallback` 三类终态。
- `BackgroundTask` 是脱离当前 turn 的延后工作单元，不得成为当前用户回复的隐式前置条件。
- `BackgroundTaskStatus` 用于表达后台任务的生命周期，而不要求 V3.0 实现完整后台任务系统。
- `SchedulePolicy` 用于表达未来定时触发策略，不等于完整调度引擎。
- `AgentTeam` 用于表达主 agent 当前可调用的固定角色池，不表示自治团队状态。
- `AgentRole` 是 specialist 的稳定能力身份，不等于独立产品人格。
- `TeamTask` 不是用户需求本身，而是主 agent 委派给某个 specialist 的一小块工作。
- `TeamTaskResult` 用于表达 specialist 完成结果、状态和 observation 摘要。
- `SpecialistBrief` 必须是结构化 brief，而不是原始整段用户对话转发。
- `SpecialistObservation` 是 `Observation` 的 team-member 语义特化，只能返回给主 agent。
- `DelegationPolicy` 用于表达主 agent 为什么选择某个 specialist、什么时候优先 specialist、什么时候回退到 tool。
- `PluginManifest` 用于声明插件带来的 provider / hook / prompt / policy 扩展边界。
- `PluginCapability` 表示插件暴露出来、且仍需经过 registry 和权限系统约束的能力集合。
- `SubAgentProvider` 在 V3.0 中不仅是单个子 agent 接口，也承担团队成员能力接口语义。
- `SkillDefinition` 用于声明技能的步骤编排、所需 capability、适用条件和元数据。V3.0 只要求框架成立，不要求丰富的技能库。
- `SkillExecutionContext` 用于承载技能执行过程中的中间状态，使技能内每一步仍可被 trace 和 hardening gate 覆盖。
- `ErrorCategory` 用于区分 provider 错误、LLM 错误和系统错误，驱动不同的恢复策略。
- `RetryPolicy` 用于表达可重试错误的最大重试次数和退避策略，而不是让每层实现自行决定重试行为。
- `LLMErrorObservation` 是 `Observation` 的错误特化，用于表达 LLM 调用失败的结构化信息（超时、非法格式、非法 action），使主循环可以像消费 provider error observation 一样消费 LLM 错误。

如需在技术设计中补内部概念，V3.0 只补语义，不扩公开接口：

- `evidence sufficiency`
- `business scope check`
- `memory read view`
- `capability scope`
- `prompt layer`
- `context packet`
- `guardrail hit reason`
- `fallback reason`

以下类型明确不在 V3.0 展开：

- `PersistentTeammateState`
- `DynamicForkPolicy`
- `SharedTeamMemory`
- `WorktreeLease`

### 10.3 `SubAgentProvider` 语义

`SubAgentProvider` 必须满足：

- 只有主 agent 能调用
- 返回结构化 observation
- 不直接输出最终用户回复
- 支持不同 lifecycle mode
- `V3.0` 只要求 `fixed_specialist`
- 可在自身边界内自主调工具，但不得绕过主 agent 的最终决策权

### 10.4 明确排除的 Legacy 接口

V3 主规格中明确排除以下 legacy/V2 接口：

- `/recommend`
- `/api/v2/*`
- recommendation snapshot / projection / feedback loop API

## 11. 版本路线与验收结果

### 11.1 V3.0

V3.0 的定位是：**中心化 Main-Agent 平台首版 + 电商导购首场景**。

必须成立的能力：

- 导购场景驱动的 bounded agentic 多步主循环
- 两层受控 memory
- 权限系统、Hook 系统、系统提示词与上下文压缩
- 同步 turn runtime + turn-scoped TaskBoard
- 中心化 Agent Team + Team Protocol
- MCP-compatible provider / plugin 扩展位
- tool / sub-agent 调度
- trace / task / guardrails
- `fixed_specialist` 形态的子 agent
- 导购场景下的澄清、查找、比较、建议
- 技能系统框架（skill 注册与执行，至少一个参考 skill）
- 系统级错误恢复（LLM 失败处理、部分完成 turn 的状态保持、可重试/不可重试分类）
- 不会在无证据、超边界或未通过硬化检查时乱答

### 11.2 V3.1

预留升级方向：

- `persistent_teammate`
- 如果要支持任务自动认领或半自治协作，应归入这一层
- 引入会话级持续协作成员
- 更丰富的 provider 生命周期管理
- 更复杂的对话协作模式

### 11.3 V3.2

预留升级方向：

- `dynamic_fork`
- 如果要支持动态分叉、临时派生子 agent，应归入这一层
- Worktree 隔离等更强执行隔离能力
- 更强的多 agent 协作策略
- 更复杂的外部能力编排

### 11.4 验收结果

读完本规格后，AI 或工程实现者应该能明确回答：

- V3 是什么产品
- 为什么 V3 不等于 V2 推荐系统
- 电商场景在 V3 中的地位
- 主 agent、sub-agent、tool/provider 的边界
- 主 agent 在导购场景里要完成哪些任务
- 每类任务下允许哪些 action，以及何时追问、何时调工具、何时给结论
- 为什么 V3.0 仍是中心化调度，而不是 worker 自动认领
- 什么情况下主 agent 可以给购买建议
- 什么情况下只能追问、补查或 `fallback`
- 为什么没有工具 observation 时不能写商品事实
- 什么信息只能存在 `session working memory`
- 什么信息才有资格进入 `durable user memory`
- 为什么推断不能直接写入长期记忆
- 为什么 memory 不能替代商品事实 observation
- 权限系统限制了谁能调用什么
- Hook 系统能做什么、不能做什么
- 为什么系统提示词必须分层而不是随意拼接
- 为什么上下文压缩不能把推断压成事实
- 为什么 V3.0 的 runtime 主线不等于完整后台任务系统
- MCP 和插件在 V3.0 中分别是什么定位
- 为什么 Worktree 隔离不属于 V3.0 主线
- 一轮 `/messages` 在 runtime 中如何启动、推进、终止
- 什么是 task，什么不是 task
- 为什么 V3.0 要用 turn-scoped TaskBoard，而不是全局任务系统
- 为什么 V3.0 先采用串行执行
- tool / sub-agent 调用和 task 的关系是什么
- V3.0 的多-agent 到底是不是中心化架构
- 主 agent 和 specialist 的职责边界
- 为什么 specialist 可以自主调工具，但仍不等于自治队友
- 为什么 specialist 之间不能横向通信
- V3.0 和 `persistent_teammate` / `dynamic_fork` 的差异

V3.0 的产品验收标准：

- 用户能通过自然语言表达购物需求
- 主 agent 能通过澄清缩小需求范围
- 主 agent 能调用参考能力做商品查找、比较、建议
- 主 agent 能在 `继续调用 / 澄清 / 回复 / fallback` 之间做决策
- `fixed_specialist` 在 V3.0 中是高级工具位，而不是自治队友
- 主 agent 能从固定角色池中选择 specialist
- specialist 能接收结构化 brief 并返回结构化 observation
- specialist 可在自身边界内调用工具
- specialist 内部工具调用仍受 hardening gate 约束
- 主 agent 可基于 specialist observation 继续调度、追问、回复或 `fallback`
- specialist 不能直接生成最终用户回复
- specialist 之间不能直接通信或互相拉起
- 用户明确说“我预算 3000 左右”时，信息可写入 `session working memory`
- 用户明确说“我长期不用某品牌”时，信息可写入 `durable user memory`
- 用户一句试探性表达不会直接进入 `durable user memory`
- 用户后续修正偏好时，旧 `durable user memory` 会被覆盖、降级或失效
- 当前输入和 `durable user memory` 冲突时，系统会先澄清，不直接沿用旧记忆
- 没有 tool observation 时，memory 不会被拿来当商品事实
- 单轮候选和比较结论不会被错误写成长期用户记忆
- specialist 不能调用越权 tool，也不能横向拉起其他 specialist
- hook 失败不会拖垮主循环，也不能绕过 hardening gate
- 平台 prompt、场景 prompt、角色 prompt、task brief 的责任边界清晰
- 压缩后的上下文仍保留已确认偏好、未解决冲突和当前候选范围
- 上下文压缩不会把未确认推断写成已确认事实
- V3.0 不要求完整后台任务系统，但后台任务占位不会污染同步 turn runtime 口径
- 插件注册后仍需经过 registry、权限系统和 hardening 约束
- Worktree 隔离不会被误当成 V3.0 当前必做能力
- 用户消息进入后，runtime 能创建 turn context 和 task board
- 主 agent 每次只推进一个 ready task
- 依赖未满足时，task 可以进入 `blocked`
- tool / sub-agent 调用会作为 invocation 挂在 task 下
- hardening gate 能拦截非法 action、非法 capability 和 reply 前证据不足
- provider 失败会生成 error observation，并驱动后续澄清、降级或 `fallback`
- step 超限时，runtime 会受控结束，不继续隐藏执行
- 用户需求不完整时，主 agent 会先澄清而不是直接推荐
- 比较所需事实不足时，主 agent 不会编造结论
- 检索结果为空时，主 agent 会说明无结果或需要调整条件，而不是虚构候选
- 用户请求下单、支付、售后等超边界事务时，系统会按超边界请求处理
- 工具超时或报错时，主 agent 会产生结构化降级，不崩溃、不假成功
- 非法 capability、非法 action、step 超限时会进入受控 `fallback`
- `shopping_specialist` 返回结果也必须受证据规则约束，不能绕过主循环硬化层
- 子 agent 只返回结构化结果，不直接面向用户
- trace 能完整解释一轮主循环
- trace 能解释 memory 的读取、拒写、覆盖和冲突处理
- trace 能解释团队调度路径、specialist 选择理由以及 observation 采纳逻辑
- trace 能看出本轮命中了哪条 hardening 规则，以及为什么继续、终止、澄清或 `fallback`
- 业务能力未来可通过 MCP/provider 扩展，而不改主循环定义
- skill 通过 CapabilityRegistry 注册，执行过程中每一步仍受 hardening gate 和 trace 约束
- skill 不能绕过主 agent 的终止条件，主 agent 可在 skill 执行中途中断
- LLM 调用失败时，系统生成 LLMErrorObservation 并进入错误消费流程，不让主循环静默中断
- LLM 重试最多 2 次，仍失败则进入 fallback 向用户返回受控回复
- LLM 返回非法 action 时不重试，直接 fallback
- turn 中途失败时，已产出的 trace 和 session working memory 保留，未完成 task 标记为 failed
- 系统内部异常不暴露给用户，session 保持有效

## 12. 参考对话示例

以下示例展示导购主循环在真实对话中的运行方式。每个示例同时标注用户视角（对话内容）和系统视角（任务节点、观察、决策、action）。

这些示例的作用是：

- 验证第 5 节的任务节点规则和 action 集合在具体场景下是否自洽
- 为后续技术设计和 prompt 编写提供具象参照
- 不穷举所有路径，只覆盖三类典型模式

### 12.1 示例 A — Happy Path：降噪耳机导购

#### Turn 1

**用户消息：** "帮我看看 3000 左右的降噪耳机"

**系统内部：**

- 任务节点：`需求表达`
- 主 agent 观察：预算 ≈ 3000，品类 = 降噪耳机。缺失：使用场景、品牌偏好、排斥项。
- 决策：信息不足以形成有效检索约束，优先澄清。
- Action：`ask_clarification`
- Memory 写入：将"预算 ≈ 3000""品类 = 降噪耳机"写入 `session working memory`（已确认的硬约束）。

**主 agent 回复：** "好的，3000 左右的降噪耳机。你主要在什么场景用——通勤、办公、还是运动？有没有特别想要或不想要的品牌？"

**本轮终止。** 等待用户下一条消息。

---

#### Turn 2

**用户消息：** "通勤用，不要 Beats"

**系统内部：**

- 任务节点：`需求表达` → 判断是否进入 `查找候选商品`
- 主 agent 观察：使用场景 = 通勤，排斥品牌 = Beats。结合 session working memory：预算 ≈ 3000，品类 = 降噪耳机。
- 决策：硬约束已足够（品类 + 预算 + 场景 + 排斥），用户已表达找商品意图 → 进入查找。
- Action：`call_tool`（商品检索工具）
- 调用参数：品类=耳机，子类=降噪，价格范围=2500-3500，场景=通勤，排除品牌=Beats
- Memory 写入：将"场景 = 通勤""排斥 = Beats"追加到 `session working memory`。

**Tool 返回 observation：** 4 款候选商品（Sony WH-1000XM5、Bose QC Ultra、Sennheiser Momentum 4、Apple AirPods Max）

**系统内部（继续循环，未终止）：**

- 任务节点：`查找候选商品` → 判断是否进入 `比较商品`
- 主 agent 观察：4 款候选，价格都在范围内，均为降噪耳机，均非 Beats。候选质量足够，用户没说"先列出来就行"。
- 决策：候选数量适中，直接进入比较可以给用户更有价值的回复。
- Action：`call_sub_agent`（candidate_analysis_specialist）
- SpecialistBrief：基于 4 款候选 + 用户约束（通勤降噪），按降噪效果、佩戴舒适度、续航、便携性比较。

**Specialist 返回 SpecialistObservation：** 结构化比较结果（各维度评分 + 差异说明）

**系统内部（继续循环）：**

- 任务节点：`比较商品` → 判断是否进入 `给购买建议`
- 主 agent 观察：比较结果完整，有 tool observation 支撑，无未解决冲突。
- 决策：evidence sufficiency 达标，可以给出建议。
- Action：`reply_to_user`
- Hardening gate 检查：✓ 业务边界合法，✓ 商品事实来自 tool observation，✓ 比较结论可追溯到候选数据，✓ 无未处理关键冲突。

**主 agent 回复：**

"帮你比较了 4 款 3000 价位的通勤降噪耳机：

| | Sony XM5 | Bose QC Ultra | Sennheiser M4 | AirPods Max |
|---|---|---|---|---|
| 降噪 | 最强 | 很强 | 较强 | 很强 |
| 佩戴 | 轻便舒适 | 最舒适 | 偏紧 | 偏重 |
| 续航 | 30h | 24h | 60h | 20h |
| 便携 | 可折叠 | 可折叠 | 可折叠 | 不可折叠 |

**建议：** 通勤场景下 Sony XM5 综合最优——降噪最强、重量轻、续航够用、可折叠好收纳。如果你特别在意佩戴舒适度，Bose QC Ultra 是第二选择。

需要了解哪款的更多细节吗？"

**本轮终止。** 该 turn 共执行 3 步（call_tool → call_sub_agent → reply_to_user），未触及 max_steps=8。

---

#### Turn 3

**用户消息：** "Sony 和 Bose 音质差别大吗？"

**系统内部：**

- 任务节点：`比较商品`（用户追问特定维度）
- 主 agent 观察：用户想知道 Sony XM5 和 Bose QC Ultra 的音质差异。上一轮的比较维度没有覆盖音质。
- 决策：需要补充音质相关的商品事实。
- Action：`call_tool`（商品详情工具，查询 Sony XM5 和 Bose QC Ultra 的音质相关参数和评价）

**Tool 返回 observation：** 两款耳机的音质参数和用户评价摘要

**系统内部（继续循环）：**

- 任务节点：`比较商品` → `给购买建议`
- 主 agent 观察：音质差异数据已获取，有 tool observation 支撑。
- 决策：可以直接回复，不需要再调 specialist。
- Action：`reply_to_user`

**主 agent 回复：** "音质差异主要在风格上：Sony XM5 低频更厚，Bose QC Ultra 声场更宽、中频更清晰。通勤听流行/摇滚 Sony 更讨喜，听人声/播客 Bose 更通透。降噪开启后两者音质都不会明显下降。如果你主要听什么类型的音乐可以告诉我，我帮你选。"

**本轮终止。**

---

### 12.2 示例 B — 澄清密集路径：送礼场景

#### Turn 1

**用户消息：** "帮我选个礼物"

**系统内部：**

- 任务节点：`需求表达`
- 主 agent 观察：无品类、无预算、无对象、无场景。缺失关键信息，无法形成任何检索约束。
- 决策：信息严重不足，优先澄清。不调用任何 tool。
- Action：`ask_clarification`

**主 agent 回复：** "好的，帮你选礼物。先了解一下：送给谁？大概什么预算？"

**本轮终止。**

---

#### Turn 2

**用户消息：** "送女朋友的，生日礼物"

**系统内部：**

- 任务节点：`澄清需求`
- 主 agent 观察：对象 = 女朋友，场景 = 生日。仍然缺失：预算、品类方向、对方喜好。
- 决策：仍然信息不足，继续澄清。
- Action：`ask_clarification`
- Memory 写入：将"对象 = 女朋友""场景 = 生日礼物"写入 `session working memory`。

**主 agent 回复：** "生日礼物，明白了。预算大概多少？有没有她比较喜欢的品类方向——比如数码、美妆、饰品？"

**本轮终止。**

---

#### Turn 3

**用户消息：** "1000-2000 吧，她喜欢听歌"

**系统内部：**

- 任务节点：`澄清需求` → 判断是否转入 `查找候选商品`
- 主 agent 观察：预算 = 1000-2000，兴趣线索 = 听歌。结合 session working memory：对象=女朋友，场景=生日。"听歌"可以关联到耳机品类，但用户没有明确说要买耳机。
- 决策：有了可检索方向（耳机），但这是主 agent 的推断而非用户明确要求。先确认方向再检索。
- Action：`ask_clarification`
- Memory 写入：将"预算 = 1000-2000""兴趣 = 听歌"写入 `session working memory`。注意："品类 = 耳机"是推断，不写入 memory，只作为当前 reasoning 参考。

**主 agent 回复：** "1000-2000，她爱听歌。你是想送耳机之类的数码产品，还是其他方向？"

**本轮终止。**

---

#### Turn 4

**用户消息：** "对，耳机吧"

**系统内部：**

- 任务节点：`需求表达` → `查找候选商品`
- 主 agent 观察：品类 = 耳机（用户明确确认），预算 = 1000-2000，场景 = 送女朋友生日礼物（颜值/设计可能重要）。
- 决策：约束已足够进入检索。
- Action：`call_tool`（商品检索工具）
- 调用参数：品类=耳机，价格范围=1000-2000，附加偏好=颜值/设计
- Memory 写入：将"品类 = 耳机"写入 `session working memory`（已由用户确认）。

**Tool 返回 observation：** 候选列表

**后续流程与示例 A 的比较→建议路径类似，不再展开。**

---

### 12.3 示例 C — Fallback：超出导购边界

#### Turn 1

（假设前面已经完成了一轮导购，主 agent 推荐了 Sony WH-1000XM5。）

**用户消息：** "就这个了，帮我下单"

**系统内部：**

- 任务节点：进入 `business scope check`
- 主 agent 观察：用户请求"下单"，这是购买执行动作。
- Hardening gate 检查：V3.0 的业务边界规则 → 只负责导购，不负责下单、支付、账户、售后。"下单"超出边界。
- 决策：超出导购边界，不能伪装成导购回答。
- Action：`fallback`
- Fallback reason：`business_scope_violation — 用户请求下单，超出 V3.0 导购边界`
- Trace 记录：命中业务边界规则，fallback reason 写入 TraceRecord。

**主 agent 回复：** "目前我只能帮你做导购咨询——找商品、比较和给建议。下单需要你到电商平台直接购买。要不要我帮你再看看其他选项，或者对这款有其他问题？"

**本轮终止。**

---

#### Turn 2

**用户消息：** "那这款手机有没有售后保障？"

**系统内部：**

- 任务节点：进入 `business scope check`
- 主 agent 观察：用户询问"售后保障"，这属于售后服务范畴。
- Hardening gate 检查：售后不在 V3.0 导购范围内。
- 决策：不能用常识补全售后信息（违反证据规则），也不在导购边界内。
- Action：`fallback`
- Fallback reason：`business_scope_violation — 售后保障不在 V3.0 导购范围`

**主 agent 回复：** "售后保障的具体政策我这边查不到，建议你到品牌官网或购买渠道确认。关于这款耳机的产品参数、和其他型号的比较，我可以继续帮你。"

**本轮终止。**

---

#### Turn 3

**用户消息：** "帮我看看这款和 Bose 的区别"

**系统内部：**

- 任务节点：`比较商品`（回到正常导购流程）
- 主 agent 观察：用户回到导购范围内的比较请求。business scope check 通过。
- Action：`call_tool`（商品比较）
- 正常流程继续。

**说明：** fallback 不是会话终止，是单轮受控退出。用户下一条消息如果回到导购范围，主循环正常恢复。

## 13. 非功能性需求

V3.0 首版以验证核心循环为主，非功能性需求取实用下限，不过度约束技术选型。

### 13.1 响应延迟

- V3.0 不设硬性延迟上限。
- 首版目标是跑通主循环，不为延迟优化牺牲可控性和可解释性。
- 后续版本如需延迟约束，应在 runtime 层引入 step-level timeout 和 provider-level timeout，而不是在产品规格层硬编码总延迟。

### 13.2 Session 生命周期

- 每个 session 最多 20 轮 turn。
- 空闲超过 30 分钟无新消息则 session 过期。
- 达到 20 轮上限时，主 agent 在最后一轮强制以 `reply_to_user` 总结当前导购状态（已确认需求、已推荐商品、未完成事项），不 silent fail。
- Session 过期后，后续 `/messages` 请求返回 session expired 错误，不自动续期。
- `session working memory` 随 session 过期一起清除。
- `durable user memory` 不受 session 过期影响。

### 13.3 并发模型

- V3.0 只支持单用户 demo 模式，不考虑多用户并发。
- 同一时刻只有一个活跃 session。
- 不要求并发安全、session 隔离或多租户支持。
- 后续版本如需多用户，应在 runtime 层引入 session 隔离和并发控制，而不是在首版引入不必要的复杂度。

### 13.4 Mock 商品数据

- 首版 mock 商品目录覆盖 2 个品类：手机和耳机。
- 每个品类约 15-25 条商品，合计约 30-50 条。
- 每条商品至少包含以下字段：名称、品牌、价格、品类、子品类、核心参数（因品类而异）、评分、简要描述。
- Mock 数据需要具备足够差异性，能支撑检索、过滤、比较和建议场景的验证。
- Mock 数据以静态内存结构提供，不依赖外部数据库。
- 后续可通过 MCP/provider 方式替换为真实商品数据源。
