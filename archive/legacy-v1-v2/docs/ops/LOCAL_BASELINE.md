# 本地压测与部署基线

## 先回答两个问题

### 需要现在就做部署压测吗

需要做压测，但顺序应该是：

1. 先在本地单机把 baseline 跑通。
2. 再把同一套场景搬到服务器复现。
3. 暂时不要把 app 和 worker 硬拆到两台服务器。

原因是当前 V2 仍然依赖 SQLite + 独立 background worker 共享同一个数据库文件。HTTP app 和 worker 现在应部署在同一台机器上。

### 现在推荐的部署拓扑是什么

- `2C8G` 机器：运行 `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` 和 `python scripts/v2/background_worker.py`
- `2C4G` 机器：运行 `k6` 发压，或做日志与结果采集

如果后续真的要把 app 与 worker 分到不同机器，必须先把 SQLite 换成网络数据库，再单列改造任务系统，不能和这次仓库整理混做。

## 本地启动

### 启动 HTTP 服务

```bash
source .venv/Scripts/activate
```

If the shell is not activated, replace `python` below with `./.venv/Scripts/python.exe`。

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 启动 V2 background worker

```bash
python scripts/v2/background_worker.py --database .tmp/v2_runtime/v2.sqlite3 --poll-interval 0.5
```

## 本地功能验证

先跑功能，再跑 baseline：

```bash
python harness/v2/bootstrap.py
pytest tests -k "test_v2_" -q
pytest tests/v1 -q
python smoke_test_v2.py
python smoke_test.py
```

## k6 场景

### V1 steady scenario

```bash
k6 run perf/k6/v1-steady.js ^
  -e BASE_URL=http://127.0.0.1:8000 ^
  -e SUMMARY_PATH=perf/results/v1-k6-summary.json
```

### V2 mixed scenario

```bash
k6 run perf/k6/v2-mixed.js ^
  -e BASE_URL=http://127.0.0.1:8000 ^
  -e SUMMARY_PATH=perf/results/v2-k6-summary.json
```

V2.2 当前测的链路是：

```text
create session
  -> message turn 1（advisory）
  -> message turn 2（advisory / preference stable）
  -> GET /recommendations（pure snapshot read）
  -> POST /feedback-events
```

说明：

- `/messages` 不再把同步画像写入和同步推荐构建塞进实时链路。
- `/recommendations` 不再现场生成 snapshot；miss / expired 只会返回空值或旧值，并异步入队 `homepage` refresh。
- 如果要测后台投影吞吐，重点看 SQLite 中的 `events`、`task_records` 和 `recommendation_snapshots` 聚合结果，而不是把读接口时延当成构建时延。

## 报告生成

### V1 基线报告

```bash
python scripts/perf/build_baseline_report.py ^
  --mode v1 ^
  --label local-v1 ^
  --k6-summary perf/results/v1-k6-summary.json ^
  --output-json perf/results/v1-baseline.json ^
  --output-md perf/results/v1-baseline.md
```

### V2 基线报告

```bash
python scripts/perf/build_baseline_report.py ^
  --mode v2 ^
  --label local-v2 ^
  --database .tmp/v2_runtime/v2.sqlite3 ^
  --k6-summary perf/results/v2-k6-summary.json ^
  --output-json perf/results/v2-baseline.json ^
  --output-md perf/results/v2-baseline.md
```

## 量化口径

### V1

- 总体吞吐与延迟：`http_reqs.rate`、`http_req_duration` 的 p50/p95/p99、`http_req_failed.rate`
- 行为指标：各 stage 的 `degraded ratio`
- 执行开销：`user_profile`、`product_rec_coarse`、`product_rec_ranked`、`inventory`、`marketing_copy` 的平均耗时

V1 这些指标来自 `perf/k6/v1-steady.js` 中注入的自定义 k6 metrics。

### V2 HTTP

- `v2_create_session`
- `v2_message_turn_1`
- `v2_message_turn_2`
- `v2_recommendations`
- `v2_feedback`

每个 endpoint 都统计：

- request count / RPS
- p50 / p95 / p99
- 非 2xx 比例

### V2 行为

- `reply_ready_ratio`
- `needs_clarification_ratio`
- `fallback_used_ratio`
- `refresh_trigger_ratio`
- `recommendation_refresh_success_rate`
- `pending_backlog`
- `snapshot_total`
- `snapshots.by_scene`
- `background_avg_latency_ms`

V2.2 语义说明：

- `refresh_trigger_ratio` 是历史指标名，当前表示“消息轮次触发后台投影链路的比例”。
- `recommendation_refresh_success_rate` 表示后台 `recommendation_refresh` 事件成功率。
- `snapshot_total` 和 `snapshots.by_scene` 现在应主要观察 `homepage`，`default` 只保留兼容读取语义，不再是默认投影目标。

V2 HTTP 指标来自 k6 summary，V2 行为指标来自 SQLite 中的 `events`、`task_records`、`recommendation_snapshots` 聚合结果。

## 看报告时要注意什么

### `/messages`

- V2.2 的核心目标是把聊天链路收敛为导购、澄清、偏好收集。
- 如果 `v2_message_turn_2` 的延迟明显下降，通常说明同步画像写入和同步推荐构建已成功移出热路径。
- 如果聊天接口仍慢，优先看 worker 数、tool 调用数、`task_records` 写入频率和 SQLite 锁竞争。

### `/recommendations`

- 这个接口现在只读快照。
- 高时延不应再被解释为“现场生成 snapshot 太慢”，而应优先排查数据库读取、序列化和 backlog 导致的 stale/miss 比例。

### background

- `pending_backlog` 持续升高，说明后台消费速度跟不上入队速度。
- `recommendation_refresh_success_rate` 下降，说明真正的瓶颈在后台投影链路。
- `background_avg_latency_ms` 应与 `v2_recommendations` 分开分析，避免把后台构建成本误算到读接口上。

## 阶段边界

- 第一阶段目标是建立统一场景、统一统计口径、统一报告格式。
- 当前不设置硬性性能阈值 gate，先形成可复现 baseline。
- 第二阶段再把同一套脚本搬到 `2C8G` + `2C4G` 环境复现。
