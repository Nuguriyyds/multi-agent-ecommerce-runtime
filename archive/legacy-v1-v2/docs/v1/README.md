# 智能电商推荐系统 V1

Multi-Agent 架构的电商推荐系统。4 个 AI Agent 协同工作：理解用户、推荐商品、校验库存、生成文案。

## 它解决什么问题

传统电商三大痛点：
- 推荐了缺货商品 → 本系统库存实时校验，缺货自动剔除
- 营销文案千篇一律 → 本系统根据用户画像生成个性化文案
- 推荐/文案/库存各自为战 → 本系统统一编排，结果实时互相影响

## 架构

```
POST /api/v1/recommend {"user_id": "u003", "num_items": 5}
  │
  ▼
FastAPI + trace_id 中间件 + A/B 实验分组
  │
  ▼
Supervisor 三阶段编排
  │
  ├── Phase 1 并行 ─┬── 画像 Agent (LLM)  → 用户分群/偏好/价格区间
  │                  └── 推荐 Agent 粗召回   → 15 个候选商品
  │
  ├── Phase 2 并行 ─┬── 推荐 Agent 精排 (LLM) → Top-5 排序
  │                  └── 库存 Agent            → 过滤缺货 + 低库存预警
  │
  ├── 中间处理: 库存过滤 + 截断
  │
  └── Phase 3 串行 ── 文案 Agent (LLM) → 5 条个性化营销文案
  │
  ▼
RecommendationResponse (商品 + 文案 + 库存状态 + 画像 + 实验组 + 延迟)
```

## 四个 Agent

| Agent | 调 LLM | 做什么 | 失败时 |
|-------|--------|--------|--------|
| 画像 Agent | 是 | 分析用户行为数据，输出分群标签和偏好 | 走冷启动（推荐热门商品） |
| 推荐 Agent | 精排时是 | 粗召回候选 + LLM 个性化精排 | 降级到规则排序 |
| 库存 Agent | 否 | 校验库存，剔除缺货，标记低库存 | 返回缓存数据 |
| 文案 Agent | 是 | 按用户分群选模板，LLM 生成个性化文案 | 返回商品默认描述 |

## 技术栈

- **Python 3.11+** / **FastAPI** / **Pydantic v2**
- **openai SDK** (AsyncOpenAI) — LLM 调用，API key 为空时自动走 mock
- **asyncio.gather()** — Agent 并行执行
- **标准库 logging** — 结构化 JSON 日志 + trace_id
- 无外部依赖：Redis/Milvus/MySQL 全部 mock，开箱即用

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# Windows / Git Bash
source .venv/Scripts/activate
python -m pip install -r requirements.txt

# or run directly through the repo interpreter
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 启动（mock 模式，不需要 LLM API key）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 推荐请求
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

curl -X POST http://localhost:8000/api/v1/recommend \
  -H "Content-Type: application/json" \
  -d '{"user_id": "u003", "num_items": 5}'

# 健康检查
curl http://localhost:8000/health

# 查看指标
curl http://localhost:8000/api/v1/metrics
```

接真实 LLM（可选）：
```bash
# .env
ECOM_LLM_API_KEY=your-api-key
ECOM_LLM_BASE_URL=https://api.minimax.chat/v1
ECOM_LLM_MODEL=MiniMax-M2.7
```

## 运行测试

```bash
python -m pytest tests/v1 -q
```

41 项测试覆盖：
- 每个 Agent 的正常/降级/边界场景
- Supervisor 编排的并行时序和降级策略
- A/B 引擎的一致性分桶和 Thompson Sampling 收敛
- API 层的 trace_id、冷启动、完整响应格式
- 端到端冒烟测试（5 个用户 × 3 个场景）
- 可观测性（metrics 采集、JSON 日志格式）

## API

### POST /api/v1/recommend

请求：
```json
{
  "user_id": "u003",
  "num_items": 5,
  "scene": "homepage"
}
```

响应（简化）：
```json
{
  "request_id": "e329...",
  "user_id": "u003",
  "profile": {
    "segments": ["high_value"],
    "preferred_categories": ["手机", "耳机"],
    "price_range": [2000, 8000],
    "cold_start": false
  },
  "recommendations": [
    {"id": "sku-iphone-16-pro", "name": "iPhone 16 Pro", "category": "手机", "price": 7999, "score": 12.3}
  ],
  "copies": [
    {"product_id": "sku-iphone-16-pro", "copy_text": "尊享旗舰体验，A18 Pro 芯片..."}
  ],
  "inventory_status": [
    {"product_id": "sku-iphone-16-pro", "available": true, "stock": 28, "low_stock": false}
  ],
  "experiment_group": "treatment",
  "latency_ms": 2.96,
  "agent_details": {
    "user_profile": {"success": true, "degraded": false, "latency_ms": 0.27},
    "product_rec_coarse": {"success": true, "degraded": false, "latency_ms": 0.31},
    "product_rec_ranked": {"success": true, "degraded": false, "latency_ms": 0.45},
    "inventory": {"success": true, "degraded": false, "latency_ms": 0.51},
    "marketing_copy": {"success": true, "degraded": false, "latency_ms": 0.62}
  }
}
```

### GET /api/v1/metrics

```json
{
  "agents": {
    "user_profile": {"calls": 2, "avg_latency_ms": 0.22, "error_rate": 0.0},
    "product_rec": {"calls": 4, "avg_latency_ms": 0.34, "error_rate": 0.0},
    "inventory": {"calls": 2, "avg_latency_ms": 0.46, "error_rate": 0.0},
    "marketing_copy": {"calls": 2, "avg_latency_ms": 0.95, "error_rate": 0.0}
  }
}
```

### GET /health

```json
{"status": "ok"}
```

## 项目结构

```text
app/
├── main.py                           # FastAPI 入口
├── shared/
│   ├── config/settings.py            # Pydantic Settings
│   ├── data/product_catalog.py       # 商品库（17 个 mock 商品）
│   ├── data/inventory_store.py       # 库存数据 + 缓存
│   ├── models/domain.py              # 跨版本领域模型
│   └── observability/                # logging / trace
└── v1/
    ├── agents/                       # V1 四类 Agent
    ├── models/                       # Agent IO / AgentResult
    ├── orchestrator/supervisor.py    # 三阶段编排器
    └── services/                     # LLM / FeatureStore / A/B / Metrics
tests/v1/                             # V1 测试
harness/v1/                           # V1 backlog / progress / validation
scripts/v1/smoke_test.py              # V1 端到端冒烟脚本
smoke_test.py                         # 向后兼容 shim
docs/v1/设计.md                        # V1 技术设计文档
```

说明：

- 推荐使用 `python -m uvicorn app.main:app` 启动；根 `main.py` 仅保留兼容 shim。
- `smoke_test.py` 继续保留，但真实实现位于 `scripts/v1/smoke_test.py`。

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 并行方案 | asyncio.gather | Agent 是 IO 密集（等 LLM），单线程无并发安全问题 |
| LLM SDK | openai (不用 langchain) | 只用 chat completion，不需要 chain/tool/memory 的重抽象 |
| Agent 编排 | 自写 Supervisor (不用 LangGraph) | 4 个 Agent 的简单 DAG，完全可控 |
| 重试 | BaseAgent 自己实现 | 超时→立即降级不重试；普通异常→指数退避 |
| 数据传递 | Pydantic 模型 | 创建时校验类型，错误不会悄悄传播到下游 |
| 外部服务 | 全部 mock | 开箱即用，接口不变，后续可替换真实服务 |

## 预置数据

5 个样本用户（每个对应一种用户分群）：

| user_id | 分群 | 特征 |
|---------|------|------|
| u001 | NEW_USER | 无购买，少量浏览 |
| u002 | ACTIVE | 频繁浏览和购买 |
| u003 | HIGH_VALUE | 高客单价，偏好高端品牌 |
| u004 | PRICE_SENSITIVE | 大量浏览，偏好低价商品 |
| u005 | CHURN_RISK | 曾经活跃，近期无购买 |

17 个 mock 商品，涵盖手机、耳机、平板、配件、穿戴、显示器、家电、家居等品类。
