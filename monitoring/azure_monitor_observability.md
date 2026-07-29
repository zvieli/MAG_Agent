# MAG_Agent Azure Monitor Observability

This file contains KQL snippets for App Insights Workbooks and Azure Monitor alerts.
The application emits structured observability logs with the `[OBS]` prefix.

## Base Parser

```kusto
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| extend event = tostring(obs.event)
| extend trace_id = tostring(obs.trace_id)
| extend doc_id = tostring(obs.doc_id)
| extend instance_id = tostring(obs.instance_id)
```

## Pipeline Failures

Alert: critical when count > 0 over 5 minutes.

```kusto
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| where tostring(obs.event) == "pipeline.failed"
| summarize failures=count() by bin(timestamp, 5m)
```

## Pipeline Throughput

Dashboard tile.

```kusto
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| where tostring(obs.event) in ("pipeline.started", "pipeline.completed", "pipeline.failed")
| summarize count() by event=tostring(obs.event), bin(timestamp, 1h)
```

## Pipeline Phase Durations

Dashboard tile for p50/p95/p99 activity durations.

```kusto
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| where tostring(obs.event) in (
    "pipeline.chunking.completed",
    "pipeline.extraction.chunk_completed",
    "pipeline.merge.completed",
    "pipeline.final_indexing.completed"
)
| extend duration_ms = todouble(obs.duration_ms)
| summarize p50=percentile(duration_ms, 50), p95=percentile(duration_ms, 95), p99=percentile(duration_ms, 99) by event=tostring(obs.event), bin(timestamp, 1h)
```

## Query Failures And Latency

Alert: warning when failures > 3 over 10 minutes, critical when p95 > SLA.

```kusto
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| where tostring(obs.event) in ("query.completed", "query.failed")
| summarize
    total=count(),
    failures=countif(tostring(obs.event) == "query.failed"),
    p95_duration_ms=percentile(todouble(obs.duration_ms), 95)
  by bin(timestamp, 10m)
```

## Health And Dependency Probes

Alert: critical when health status is `Unhealthy`; warning when any required dependency is unhealthy.

```kusto
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| where tostring(obs.event) == "health.checked"
| summarize checks=count(), unhealthy=countif(tostring(obs.status) == "Unhealthy") by bin(timestamp, 5m)
```

```kusto
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| where tostring(obs.event) == "metric" and tostring(obs.metric_name) == "dependency_probe_latency_ms"
| summarize p95_latency_ms=percentile(todouble(obs.metric_value), 95) by dependency=tostring(obs.dependency), status=tostring(obs.status), bin(timestamp, 5m)
```

## Protocol Completeness

Alert: warning when a completed pipeline has critical missing evidence.

```kusto
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| where tostring(obs.event) == "protocol_completeness.generated"
| summarize
    avg_coverage=avg(todouble(obs.coverage_percent)),
    critical_missing=max(toint(obs.critical_missing)),
    critical_review_required=max(toint(obs.critical_review_required))
  by doc_id=tostring(obs.doc_id), bin(timestamp, 1h)
```

## LLM Cost Controls & Token Trajectory

Dashboard tiles for token usage and estimated USD cost breakdown per step and model.

```kusto
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| where tostring(obs.event) startswith "trajectory."
| summarize
    total_prompt_tokens=sum(toint(obs.tokens_in)),
    total_completion_tokens=sum(toint(obs.tokens_out)),
    total_cost_usd=sum(todouble(obs.cost_usd))
  by model_name=tostring(obs.model_name), step=tostring(obs.trajectory_step), bin(timestamp, 1h)
```

## Agent Trajectory Trace Tree

Detailed step-by-step breakdown of an agent investigation trajectory.

```kusto
let target_trace_id = "request-REPLACE-ME";
traces
| where message startswith "[OBS] "
| extend obs = parse_json(substring(message, 6))
| where tostring(obs.trace_id) == target_trace_id or message has target_trace_id
| project timestamp, step=tostring(obs.trajectory_step), node=tostring(obs.node), tool=tostring(obs.tool_name), status=tostring(obs.tool_status), cost=todouble(obs.cost_usd), duration_ms=todouble(obs.duration_ms), error=tostring(obs.error)
| order by timestamp asc
```

## Trace Lookup

Use this when a user provides a frontend trace id.

```kusto
let target_trace_id = "pipeline-REPLACE-ME";
traces
| where message has target_trace_id
| order by timestamp asc
```

