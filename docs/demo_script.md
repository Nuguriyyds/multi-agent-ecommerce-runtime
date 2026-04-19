# V3 演示视频脚本(2-3 分钟)

> 飞书 AI 校园挑战赛初筛用。目标:让评委在 3 分钟内看明白 V3 不是玩具推荐系统,是可迁移到 IM 办公协同的**可审计动态 Agent Runtime**。

## 拍摄前准备

1. 打开终端,在项目根目录运行:
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
2. 浏览器打开 `http://127.0.0.1:8000/ui`
3. 准备好录屏工具(OBS / Windows Xbox Game Bar / QuickTime),分辨率 1920x1080
4. 事先打开:
   - README.md(第 2 个 Chrome tab)
   - docs/v3_1_fanout_design.md(第 3 个 Chrome tab)
   - docs/project_profile.md(第 4 个 Chrome tab)

## 脚本 — 分段计时

### [0:00 – 0:20] 开场与定位

**画面:** README 顶部标题 + 架构 mermaid 图

**旁白(建议脚本):**
> "这是 V3 — 一个按 Anthropic Harness 方法论构建的**可审计动态 Agent Runtime**。
> 不同于 LangGraph 风格的并行 Supervisor,V3 的主 Agent 每一步**实时**选择下一个 action,
> 每一步都过硬化检查、每一次 observation 都进 trace。
> 电商导购只是首个验证场景 — 同一套 runtime 可迁移到 IM 办公协同。"

### [0:20 – 0:50] Happy Path 对话演示

**画面:** UI 页面,左边聊天框,右边三 tab 面板

**操作:**
1. 点击左上角"演示模式"切换(如果有预置脚本)
2. 在聊天框输入:`完整演示:3000 左右通勤降噪耳机,不要 Beats`
3. 按回车

**旁白:**
> "用户一条指令,主 Agent 开始多步循环 —
> 先调 shopping_brief_specialist 结构化需求,
> 再调 catalog_search 工具拿候选,
> 然后调 candidate_analysis 做对比,
> 最后 recommendation_rationale 生成推荐理由。
> 一共 4 个 specialist 和 3 次工具调用,**全部串行、全部可追溯**。"

**画面重点:** 右侧 Trace tab 实时刷出 decision / invocation / observation 的时间轴

### [0:50 – 1:20] 显式偏好档案 + revoke 演示

**操作:**
1. 切到右侧 **"我的偏好"** tab
2. 画面停留 2 秒,指出其中的 "预算 3000 / 场景 通勤 / 排斥 Beats"
3. 点击 "场景 通勤" 条目的 × 按钮
4. 切回 **"为你推荐"** tab,展示卡片刷新
5. 切到 **"Trace"** tab,展示 `memory_write decision=revoke` 条目

**旁白(这段是视频核心):**
> "这里是 V3 和 LangGraph 风格项目的**根本差异** —
> V3 没有隐式 RFM 画像,只有**对话中用户明确说过**的偏好档案。
> 每条偏好对用户可见、可撤销,撤销直接触发 memory revoke + trace 审计。
> 这是企业合规场景(GDPR / 内部审计)的硬需求 — AI 知道我什么,必须对我透明。"

### [1:20 – 1:40] HardeningGate 越界拦截演示

**操作:**
1. 切回聊天框
2. 输入:`好的,就这个了,帮我下单`
3. 按回车

**画面重点:**
- Main Agent 回复"目前我只能帮你做导购咨询..."
- 右侧 Trace tab 显示 `fallback, reason=business_scope_violation`

**旁白:**
> "用户让下单,超出导购边界。HardeningGate 识别 business boundary 违规,
> Main Agent 不走 LLM 生成,直接进入受控 fallback。
> trace 里可以清楚看到**为什么 AI 拒绝** — 这也是 IM 办公场景需要的权限审计能力。"

### [1:40 – 2:10] 差异化对比表

**画面:** 切到 README,滚动到 "与传统 Multi-Agent 方案的对比" 小节

**旁白(可以静音让画面说话,或读关键行):**
> "横向对比典型并行 Supervisor:V3 是动态 bounded loop、原生审计、写入 gate、
> 显式偏好档案、MCP 抽象。**每一行都是方法论选择,不是功能取舍。**"

### [2:10 – 2:35] 迁移到课题二 IM 办公协同

**画面:** 切到 README 的 "迁移到飞书课题二" 小节(或 project_profile 的同款表)

**旁白(视频最重要的 40 秒):**
> "V3 的每个组件都能一对一映射到 IM 办公协同场景 —
> Main Agent Loop 对应 IM 消息入口的多步决策,
> Specialist Pool 对应文档摘要 / 日程冲突 / 任务派发,
> HardeningGate 对应 IM 权限边界,
> TraceStore 对应企业操作审计,
> MCP Client 对应飞书 OpenAPI 接入,
> Durable Memory + 写入 Gate 对应跨会话工作习惯记忆。
> **这不是理论可迁移 — 是组件级的结构映射。**"

### [2:35 – 2:50] V3.1 Fan-out Roadmap

**画面:** 切到 docs/v3_1_fanout_design.md,滚动到 "与 LangGraph 的本质差异" 对比表

**旁白:**
> "并行能力不是没有,而是设计在 V3.1 里 —
> 受控 fan-out,每步由主 Agent 动态决定,每分支独立过 gate,trace 保持树形可审计。
> 这是 IM 办公协同场景需要的并行能力位。"

### [2:50 – 3:00] 收尾

**画面:** 返回 README 底部 "作者作品组合" 小节

**旁白:**
> "作者另一个项目 InfiniteChat 是 994 RPS、P99 80ms 的分布式 IM 后端。
> 这两个项目组合:**IM 系统工程能力 × Agent Runtime 工程能力**,
> 天然契合飞书课题二:基于 IM 的办公协同智能助手。谢谢。"

---

## 拍摄 tips

- 语速适中,不要追求快 — 初筛评委更看重"听得清、看得懂"
- 每段切换画面后停 1-2 秒让观众看清
- trace 刷出的时候不要快进 — 让 `decision` / `invocation` 文字能看清
- 背景建议纯色或项目截图,不要用动态壁纸
- 第一次拍不好没关系,核心是偏好 tab 撤销那段 + IM 迁移映射表这段要清晰
- 视频文件格式建议 MP4 H.264,时长 2:30-3:00 最佳

## 如果需要压缩到 90 秒版本

按优先级砍:
1. 砍 Happy Path 的部分 specialist 细节(0:20-0:50 压到 0:20)
2. 砍 V3.1 Fan-out Roadmap(2:35-2:50 整段砍掉)
3. 必保:偏好 tab + revoke demo(差异化核心) + IM 迁移映射(课题契合)
