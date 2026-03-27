"""
Prometheus metrics for Mantle EMS.

Exposes counters, gauges, and histograms for key business events
and system health. Scraped at GET /metrics.
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Incidents ──────────────────────────────────────────────────────
incidents_created = Counter(
    "ems_incidents_created_total",
    "Incidents created",
    ["emergency_type", "trigger_source"],
)
incidents_resolved = Counter(
    "ems_incidents_resolved_total",
    "Incidents resolved",
)

# ── Messages ───────────────────────────────────────────────────────
messages_created = Counter(
    "ems_messages_created_total",
    "Thread messages created",
    ["sender_type"],
)

# ── Actions ────────────────────────────────────────────────────────
actions_created = Counter(
    "ems_actions_created_total",
    "Actions created",
    ["tier"],
)
actions_approved = Counter("ems_actions_approved_total", "Actions approved")
actions_rejected = Counter("ems_actions_rejected_total", "Actions rejected")
actions_expired = Counter("ems_actions_expired_total", "Actions expired by urgency poller")
actions_escalated = Counter("ems_actions_escalated_total", "Actions escalated green→amber")

# ── Agent / SOP ────────────────────────────────────────────────────
agent_runs = Counter(
    "ems_agent_runs_total",
    "Agent executions (SOP handling)",
    ["agent_type", "status"],  # agent_type: scripted|llm, status: success|timeout|error
)
agent_duration = Histogram(
    "ems_agent_duration_seconds",
    "Time spent executing agent for an incident",
    ["agent_type"],
    buckets=[1, 2, 5, 10, 20, 30, 60, 120],
)
thread_agent_runs = Counter(
    "ems_thread_agent_runs_total",
    "Thread agent invocations on human messages",
    ["status"],  # success|error
)

# ── WebSockets ─────────────────────────────────────────────────────
ws_connections = Gauge(
    "ems_ws_connections_active",
    "Currently active WebSocket connections",
)

# ── Monitoring sessions ────────────────────────────────────────────
monitoring_sessions_started = Counter(
    "ems_monitoring_sessions_started_total",
    "Monitoring sessions started",
)
monitoring_escalations = Counter(
    "ems_monitoring_escalations_total",
    "Monitoring sessions escalated to incidents (e.g. fall detection)",
)
