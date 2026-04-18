# Baseline Report: local-v2.1

- mode: `v2`

## k6
- http_reqs.count: `525.0`
- http_reqs.rate: `17.375140905359945`
- http_req_failed.rate: `0.0`
- http_req_duration.p50_ms: `201.5113`
- http_req_duration.p95_ms: `736.5607`
- http_req_duration.p99_ms: `876.6537199999996`
- v2_message_metrics:
  `fallback_used_ratio`=0.0
  `message_reported_latency_ms`={"avg": 89.61708714282611, "p50": 76.46815000043716, "p95": 183.11377499958326, "p99": 206.23353900082293}
  `needs_clarification_ratio`=0.0
  `refresh_trigger_ratio`=0.5
  `reply_ready_ratio`=1.0

## v2_runtime
- reply_ready_ratio: `1.0`
- needs_clarification_ratio: `0.0`
- fallback_used_ratio: `0.0`
- refresh_trigger_ratio: `0.5`
- refresh_success_rate: `1.0`
- pending_backlog: `0`
- snapshot_total: `743`
- background_avg_latency_ms: `45.66`
