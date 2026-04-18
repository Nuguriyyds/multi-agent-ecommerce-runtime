from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from statistics import mean
from typing import Any


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _metric_values(metric: dict[str, Any]) -> dict[str, Any]:
    values = metric.get("values", metric)
    if isinstance(values, dict):
        return values
    return {}


def _pick_metric_value(metric: dict[str, Any], field: str, default: float = 0.0) -> float:
    values = _metric_values(metric)
    return float(values.get(field, default) or default)


def _pick_optional_metric_value(metric: dict[str, Any], field: str) -> float | None:
    values = _metric_values(metric)
    if field not in values or values.get(field) is None:
        return None
    return float(values[field])


def _iter_submetrics(metric: dict[str, Any]) -> list[dict[str, Any]]:
    submetrics = metric.get("submetrics", []) or []
    if isinstance(submetrics, dict):
        return list(submetrics.values())
    return list(submetrics)


def extract_endpoint_metrics(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    endpoint_metrics: dict[str, dict[str, Any]] = {}

    def ensure(endpoint: str) -> dict[str, Any]:
        return endpoint_metrics.setdefault(
            endpoint,
            {
                "http_reqs": {"count": 0.0, "rate": 0.0},
                "http_req_failed": {"rate": 0.0},
                "duration_ms": {"avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": None, "max": 0.0},
            },
        )

    for submetric in _iter_submetrics(metrics.get("http_reqs", {})):
        endpoint = (submetric.get("tags") or {}).get("endpoint")
        if not endpoint:
            continue
        bucket = ensure(endpoint)
        bucket["http_reqs"]["count"] = _pick_metric_value(submetric, "count")
        bucket["http_reqs"]["rate"] = _pick_metric_value(submetric, "rate")

    for submetric in _iter_submetrics(metrics.get("http_req_failed", {})):
        endpoint = (submetric.get("tags") or {}).get("endpoint")
        if not endpoint:
            continue
        bucket = ensure(endpoint)
        bucket["http_req_failed"]["rate"] = _pick_metric_value(submetric, "rate")

    for submetric in _iter_submetrics(metrics.get("http_req_duration", {})):
        endpoint = (submetric.get("tags") or {}).get("endpoint")
        if not endpoint:
            continue
        bucket = ensure(endpoint)
        bucket["duration_ms"] = {
            "avg": _pick_metric_value(submetric, "avg"),
            "p50": _pick_metric_value(submetric, "med"),
            "p95": _pick_metric_value(submetric, "p(95)"),
            "p99": _pick_optional_metric_value(submetric, "p(99)"),
            "max": _pick_metric_value(submetric, "max"),
        }

    return dict(sorted(endpoint_metrics.items()))


def extract_custom_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    custom_metrics: dict[str, Any] = {
        "v1": {"agent_latency_ms": {}, "agent_degraded_ratio": {}},
        "v2": {},
    }

    for name, metric in metrics.items():
        if name.startswith("v1_agent_latency_") and name.endswith("_ms"):
            stage_name = name[len("v1_agent_latency_") : -len("_ms")]
            custom_metrics["v1"]["agent_latency_ms"][stage_name] = {
                "avg": _pick_metric_value(metric, "avg"),
                "p50": _pick_metric_value(metric, "med"),
                "p95": _pick_metric_value(metric, "p(95)"),
                "p99": _pick_optional_metric_value(metric, "p(99)"),
            }
        elif name.startswith("v1_agent_degraded_"):
            stage_name = name[len("v1_agent_degraded_") :]
            custom_metrics["v1"]["agent_degraded_ratio"][stage_name] = _pick_metric_value(metric, "rate")
        elif name == "v2_reply_ready":
            custom_metrics["v2"]["reply_ready_ratio"] = _pick_metric_value(metric, "rate")
        elif name == "v2_needs_clarification":
            custom_metrics["v2"]["needs_clarification_ratio"] = _pick_metric_value(metric, "rate")
        elif name == "v2_fallback_used":
            custom_metrics["v2"]["fallback_used_ratio"] = _pick_metric_value(metric, "rate")
        elif name == "v2_refresh_triggered":
            custom_metrics["v2"]["refresh_trigger_ratio"] = _pick_metric_value(metric, "rate")
        elif name == "v2_message_reported_latency_ms":
            custom_metrics["v2"]["message_reported_latency_ms"] = {
                "avg": _pick_metric_value(metric, "avg"),
                "p50": _pick_metric_value(metric, "med"),
                "p95": _pick_metric_value(metric, "p(95)"),
                "p99": _pick_optional_metric_value(metric, "p(99)"),
            }

    if not custom_metrics["v1"]["agent_latency_ms"] and not custom_metrics["v1"]["agent_degraded_ratio"]:
        custom_metrics.pop("v1")
    if not custom_metrics["v2"]:
        custom_metrics.pop("v2")
    return custom_metrics


def extract_k6_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary:
        return {"available": False}

    metrics = summary.get("metrics", {})

    def pick(metric_name: str, field: str, default: float = 0.0) -> float:
        return _pick_metric_value(metrics.get(metric_name, {}), field, default)

    return {
        "available": True,
        "http_reqs": {
            "count": pick("http_reqs", "count"),
            "rate": pick("http_reqs", "rate"),
        },
        "http_req_failed": {
            "rate": pick("http_req_failed", "rate"),
        },
        "http_req_duration_ms": {
            "avg": pick("http_req_duration", "avg"),
            "p50": pick("http_req_duration", "med"),
            "p95": pick("http_req_duration", "p(95)"),
            "p99": _pick_optional_metric_value(metrics.get("http_req_duration", {}), "p(99)"),
            "max": pick("http_req_duration", "max"),
        },
        "iterations": {
            "count": pick("iterations", "count"),
            "rate": pick("iterations", "rate"),
        },
        "endpoint_metrics": extract_endpoint_metrics(metrics),
        "custom_metrics": extract_custom_metrics(metrics),
    }


def aggregate_v2_database(database: Path | None) -> dict[str, Any]:
    if database is None or not database.exists():
        return {"available": False}

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        events = connection.execute(
            """
            SELECT event_id, event_type, status, retry_count, error, payload
            FROM events
            ORDER BY created_at ASC
            """
        ).fetchall()
        tasks = connection.execute(
            """
            SELECT task_scope, manager_name, worker_name, tool_name, status, latency_ms, input, output
            FROM task_records
            ORDER BY created_at ASC
            """
        ).fetchall()
        snapshots = connection.execute(
            """
            SELECT scene, COUNT(*) AS snapshot_count
            FROM recommendation_snapshots
            GROUP BY scene
            ORDER BY scene ASC
            """
        ).fetchall()
    finally:
        connection.close()

    refresh_events = [row for row in events if row["event_type"] == "recommendation_refresh"]
    refresh_completed = [row for row in refresh_events if row["status"] == "completed"]
    refresh_failed = [row for row in refresh_events if row["status"] == "failed"]
    pending_backlog = sum(1 for row in refresh_events if row["status"] == "pending")

    conversation_outputs = []
    background_latencies: list[float] = []
    worker_latencies: dict[str, list[float]] = {}
    for row in tasks:
        if row["task_scope"] == "background" and row["latency_ms"] is not None:
            background_latencies.append(float(row["latency_ms"]))
        worker_name = row["worker_name"]
        if worker_name:
            worker_latencies.setdefault(worker_name, []).append(float(row["latency_ms"] or 0.0))
        if (
            row["task_scope"] == "conversation"
            and row["worker_name"] is None
            and row["tool_name"] is None
            and row["status"] == "completed"
            and row["output"]
        ):
            conversation_outputs.append(json.loads(row["output"]))

    def read_terminal_state(payload: dict[str, Any]) -> str | None:
        terminal_state = payload.get("terminal_state")
        if terminal_state is not None:
            return str(terminal_state)
        return payload.get("agent_details", {}).get("terminal_state")

    def read_refresh_triggered(payload: dict[str, Any]) -> bool:
        if "projection_event_id" in payload:
            if payload.get("projection_event_id"):
                return True
            executed_steps = payload.get("executed_steps", [])
            return "profile.request_projection" in executed_steps
        if "refresh_event_id" in payload:
            if payload.get("refresh_event_id"):
                return True
            executed_steps = payload.get("executed_steps", [])
            return "recommendation.request_refresh" in executed_steps
        return bool(payload.get("recommendation_refresh_triggered"))

    terminal_states = [read_terminal_state(payload) for payload in conversation_outputs]
    refresh_triggered = [read_refresh_triggered(payload) for payload in conversation_outputs]
    conversation_total = max(len(conversation_outputs), 1)

    return {
        "available": True,
        "events": {
            "total": len(events),
            "recommendation_refresh_total": len(refresh_events),
            "recommendation_refresh_completed": len(refresh_completed),
            "recommendation_refresh_failed": len(refresh_failed),
            "recommendation_refresh_success_rate": round(len(refresh_completed) / max(len(refresh_events), 1), 4),
            "pending_backlog": pending_backlog,
        },
        "conversation": {
            "total_completed": len(conversation_outputs),
            "reply_ready_ratio": round(terminal_states.count("reply_ready") / conversation_total, 4),
            "needs_clarification_ratio": round(terminal_states.count("needs_clarification") / conversation_total, 4),
            "fallback_used_ratio": round(terminal_states.count("fallback_used") / conversation_total, 4),
            "refresh_trigger_ratio": round(sum(refresh_triggered) / conversation_total, 4),
        },
        "background": {
            "task_count": len(background_latencies),
            "avg_latency_ms": round(mean(background_latencies), 2) if background_latencies else 0.0,
        },
        "snapshots": {
            "total": sum(int(row["snapshot_count"]) for row in snapshots),
            "by_scene": {
                str(row["scene"]): int(row["snapshot_count"])
                for row in snapshots
            },
        },
        "workers": {
            worker_name: {
                "avg_latency_ms": round(mean(latencies), 2) if latencies else 0.0,
                "count": len(latencies),
            }
            for worker_name, latencies in sorted(worker_latencies.items())
        },
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "label": args.label,
        "mode": args.mode,
        "k6": extract_k6_metrics(load_json(args.k6_summary)),
        "v2_runtime": aggregate_v2_database(args.database if args.mode == "v2" else None),
    }


def render_markdown(report: dict[str, Any]) -> str:
    def format_value(value: Any) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    lines = [
        f"# Baseline Report: {report['label']}",
        "",
        f"- mode: `{report['mode']}`",
        "",
        "## k6",
    ]
    k6 = report["k6"]
    if not k6.get("available"):
        lines.append("- unavailable")
    else:
        lines.extend(
            [
                f"- http_reqs.count: `{k6['http_reqs']['count']}`",
                f"- http_reqs.rate: `{k6['http_reqs']['rate']}`",
                f"- http_req_failed.rate: `{k6['http_req_failed']['rate']}`",
                f"- http_req_duration.p50_ms: `{k6['http_req_duration_ms']['p50']}`",
                f"- http_req_duration.p95_ms: `{k6['http_req_duration_ms']['p95']}`",
                f"- http_req_duration.p99_ms: `{format_value(k6['http_req_duration_ms']['p99'])}`",
            ]
        )
        if k6["endpoint_metrics"]:
            lines.append("- endpoint_metrics:")
            for endpoint, values in k6["endpoint_metrics"].items():
                lines.append(
                    "  "
                    f"`{endpoint}` count={values['http_reqs']['count']} rate={values['http_reqs']['rate']} "
                    f"p95={values['duration_ms']['p95']} p99={format_value(values['duration_ms']['p99'])} "
                    f"failed={values['http_req_failed']['rate']}"
                )
        custom_metrics = k6.get("custom_metrics", {})
        if "v1" in custom_metrics:
            lines.append("- v1_agent_metrics:")
            for stage_name, latency in sorted(custom_metrics["v1"]["agent_latency_ms"].items()):
                degraded_ratio = custom_metrics["v1"]["agent_degraded_ratio"].get(stage_name, 0.0)
                lines.append(
                    "  "
                    f"`{stage_name}` avg={latency['avg']} p95={latency['p95']} degraded={degraded_ratio}"
                )
        if "v2" in custom_metrics:
            lines.append("- v2_message_metrics:")
            for key, value in sorted(custom_metrics["v2"].items()):
                lines.append(f"  `{key}`={format_value(value)}")

    lines.append("")
    lines.append("## v2_runtime")
    runtime = report["v2_runtime"]
    if not runtime.get("available"):
        lines.append("- unavailable")
    else:
        lines.extend(
            [
                f"- reply_ready_ratio: `{runtime['conversation']['reply_ready_ratio']}`",
                f"- needs_clarification_ratio: `{runtime['conversation']['needs_clarification_ratio']}`",
                f"- fallback_used_ratio: `{runtime['conversation']['fallback_used_ratio']}`",
                f"- refresh_trigger_ratio: `{runtime['conversation']['refresh_trigger_ratio']}`",
                f"- refresh_success_rate: `{runtime['events']['recommendation_refresh_success_rate']}`",
                f"- pending_backlog: `{runtime['events']['pending_backlog']}`",
                f"- snapshot_total: `{runtime['snapshots']['total']}`",
                f"- background_avg_latency_ms: `{runtime['background']['avg_latency_ms']}`",
            ]
        )

    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a local perf baseline report.")
    parser.add_argument("--mode", choices=("v1", "v2"), required=True)
    parser.add_argument("--label", default="local-baseline")
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--k6-summary", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    report = build_report(args)

    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.output_md is not None:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(render_markdown(report), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
