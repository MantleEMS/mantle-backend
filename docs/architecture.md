# Mantle EMS — Architecture Document

## Overview

Mantle EMS is a **multi-tenant, real-time Emergency Management System** for home health agencies. It coordinates incident response through AI-driven SOP execution, real-time communication, and live worker wellness monitoring. The system supports multiple LLM backends (local Ollama, Anthropic Claude, AWS Bedrock) with a scripted fallback mode.

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CLIENT LAYER                                   │
│                                                                         │
│  ┌──────────────────────┐          ┌──────────────────────────────┐     │
│  │   Web Admin Panel    │          │    Mobile Simulator / App    │     │
│  │    (web.html)        │          │        (mobile.html)         │     │
│  │                      │          │                              │     │
│  │  • Incidents         │          │  • SOS Trigger               │     │
│  │  • User Mgmt         │          │  • Monitoring Sessions       │     │
│  │  • SOPs/Facilities   │          │  • WebSocket Chat            │     │
│  │  • Audit Logs        │          │  • Telemetry Submission      │     │
│  └──────────┬───────────┘          └──────────────┬───────────────┘     │
└─────────────┼────────────────────────────────────-┼─────────────────────┘
              │ HTTP REST                            │ HTTP REST + WebSocket
              └──────────────────┬───────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────────┐
│                         API LAYER (FastAPI)                             │
│                         Port 8000 · Uvicorn ASGI                        │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                         ROUTERS                                  │   │
│  │                                                                  │   │
│  │  auth.py         incidents.py      threads.py      actions.py   │   │
│  │  /auth/*         /incidents/*      /incidents/     /incidents/  │   │
│  │                                    {id}/messages   {id}/actions │   │
│  │                                    [WebSocket]                  │   │
│  │                                                                  │   │
│  │  monitoring.py   evidence.py       search.py       config.py    │   │
│  │  /monitoring/*   /evidence/*       /search/*       /orgs/*      │   │
│  │                                                    /facilities/ │   │
│  │                                                    /sops/*      │   │
│  │                                                    /users/*     │   │
│  └──────────────────────────┬───────────────────────────────────────┘  │
│                             │                                           │
│  ┌──────────────────────────▼───────────────────────────────────────┐  │
│  │                        SERVICES                                  │  │
│  │                                                                  │  │
│  │  auth_service    incident_service   thread_service   action_    │  │
│  │                                                      service    │  │
│  │  monitoring_     evidence_service   search_service   retention_ │  │
│  │  service                                             service    │  │
│  └──────────────────────────┬───────────────────────────────────────┘  │
│                             │                                           │
│  ┌──────────────────────────▼───────────────────────────────────────┐  │
│  │                    BACKGROUND TASKS                              │  │
│  │                                                                  │  │
│  │  • Action Urgency Poller (every 1s)                             │  │
│  │    green→amber after 5min · expires after 30min                 │  │
│  │                                                                  │  │
│  │  • LLM Agent (per-incident, async)                              │  │
│  │    Triggered on incident creation, runs SOP steps               │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────┬──────────────────┬────────────────────────┘
                              │                  │
          ┌───────────────────┘                  └─────────────────────┐
          │                                                            │
┌─────────▼──────────────┐                              ┌─────────────▼────┐
│    PERSISTENCE LAYER   │                              │   CACHE / PUB-SUB │
│                        │                              │                   │
│  PostgreSQL 16         │                              │  Redis 7          │
│  Port 5432             │                              │  Port 6379        │
│                        │                              │                   │
│  13 Tables:            │                              │  • Action urgency │
│  • organizations       │                              │    sorted set     │
│  • facilities          │                              │  • WebSocket      │
│  • users               │                              │    pub-sub        │
│  • sops                │                              │  • Session cache  │
│  • incidents           │                              │  • 256MB LRU      │
│  • messages            │                              │    eviction       │
│  • participants        │                              │                   │
│  • actions             │                              └───────────────────┘
│  • evidence            │
│  • monitoring_sessions │
│  • telemetry_events    │
│  • incident_event_log  │
│  • training_samples    │
│                        │
│  File Uploads          │
│  /data/uploads/        │
└────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                          AI / LLM LAYER                                 │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    agent/llm_agent.py                            │  │
│  │              LLMAgent.execute(incident_id)                       │  │
│  │                                                                  │  │
│  │   System Prompt Builder ──► Tool Registry ──► LLM Client        │  │
│  │   (system_prompt.py)         (registry.py)    (llm_client.py)   │  │
│  └──────────────┬───────────────────────────────────┬──────────────┘  │
│                 │                                   │                  │
│   ┌─────────────▼──────────────┐    ┌──────────────▼───────────────┐  │
│   │         TOOLS              │    │       LLM PROVIDERS          │  │
│   │                            │    │                              │  │
│   │  action_tools.py           │    │  • Ollama (local)           │  │
│   │  • alert_commander         │    │    qwen2.5:14b              │  │
│   │  • start_evidence_         │    │    Port 11434               │  │
│   │    collection              │    │                              │  │
│   │  • dispatch_responder      │    │  • Anthropic Claude API     │  │
│   │  • contact_911             │    │    claude-haiku / sonnet    │  │
│   │  • post_thread_message     │    │                              │  │
│   │                            │    │  • AWS Bedrock              │  │
│   │  data_tools.py             │    │    (enterprise)             │  │
│   │  • get_incident_context    │    │                              │  │
│   │  • get_facility_info       │    │  Scripted Fallback          │  │
│   │  • get_nearby_responders   │    │  (no LLM required)          │  │
│   │                            │    └──────────────────────────────┘  │
│   │  adaptive_tools.py         │                                       │
│   │  • propose_step_adaptation │                                       │
│   │  • propose_sop_switch      │                                       │
│   └────────────────────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SERVICES                                │
│                                                                         │
│  Firebase Cloud Messaging (FCM) — Push notifications to mobile devices  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Model

```
organizations
    │
    ├──< facilities (1:N)
    │       • address, risk_flags, cell_coverage, patient_info (JSONB)
    │
    ├──< users (1:N)
    │       • roles: super_admin, org_admin, admin, commander, supervisor,
    │                responder, worker
    │       • status: active, on_duty, off_duty, inactive
    │
    ├──< sops (1:N)
    │       • emergency_type, steps (JSONB), responder_checklist (JSONB)
    │
    └──< incidents (1:N)
            │ • status, severity, commander_id, sop_id
            │ • location (JSONB), ai_assessment (JSONB)
            │
            ├──< messages       (thread/chat)
            ├──< participants   (responders, with dispatch_status)
            ├──< actions        (SOP steps, tiered approval workflow)
            ├──< evidence       (uploaded files, SHA256-hashed)
            └──< incident_event_log  (append-only audit trail)

users
    └──< monitoring_sessions (1:N)
            └──< telemetry_events (1:N)
                    • event_type: location, fall_detected, heart_rate
                    • tiered retention: hot/warm/cold
```

---

## Request Flow: Incident Triggered

```
Mobile/Web Client
       │
       │  POST /api/v1/incidents
       │  { emergency_type, facility_id, ... }
       ▼
  incidents router
       │
       ├── incident_service.create_incident()
       │       • Creates incident record
       │       • Selects matching SOP
       │       • Notifies commander via FCM
       │       • Enqueues action_urgency in Redis
       │
       └── BackgroundTask: agent/router.handle_incident()
               │
               ├── [scripted mode] sop_executor.py
               │       • Iterates SOP steps synchronously
               │       • Creates Action records for each step
               │
               └── [llm mode] llm_agent.execute()
                       │
                       ├── Build system prompt (SOP + incident context)
                       │
                       ├── Loop: LLM generates tool calls
                       │       │
                       │       ├── GREEN tier → execute immediately
                       │       │   (alert_commander, evidence, messages)
                       │       │
                       │       └── RED tier → create pending Action
                       │           (dispatch, 911, notify contacts)
                       │           → Commander must approve/reject
                       │
                       └── Store conversation in training_samples
```

---

## Request Flow: Real-Time WebSocket Chat

```
Client A (Commander)                 Client B (Responder)
      │                                     │
      │  WS /incidents/{id}/ws?token=...    │
      └─────────────────┬───────────────────┘
                        │
                  threads router
                        │
                  ┌─────▼──────┐
                  │   Redis    │
                  │  Pub-Sub   │
                  └─────┬──────┘
                        │  broadcast
                  ┌─────▼──────┐
                  │  All WS    │
                  │  clients   │
                  │  on this   │
                  │  incident  │
                  └────────────┘

Message types:
  • chat        — text from participants
  • system      — automated agent messages
  • location    — real-time GPS updates (incident_event_log)
  • action      — approval requests/decisions
```

---

## Telemetry Retention Policy

```
Telemetry Event Created
        │
        ▼
  HOT  (0–7 days)
  ─────────────────
  Full resolution
  All event types
  location, fall, heart_rate
        │
        ▼
  WARM (7–90 days)
  ─────────────────
  Location: downsampled to 1 per minute
  Other types: retained as-is
        │
        ▼
  COLD (90+ days)
  ─────────────────
  All telemetry_events deleted
  Audit preserved in incident_event_log
```

---

## Action Approval Workflow

```
LLM Agent proposes action
        │
        ├── GREEN tier (low risk)
        │       └─► Execute immediately
        │             (no human approval needed)
        │
        └── RED tier (high risk)
                └─► Create pending Action record
                        │
                        ├── Action Urgency Poller (every 1s)
                        │     green→amber after 5 min
                        │     expires after 30 min
                        │
                        └── Commander reviews
                              │
                              ├── APPROVE → execute action
                              └── REJECT  → log rejection, continue
```

---

## Infrastructure

```
docker-compose.yml
│
├── api (port 8000)
│     Python 3.12 · FastAPI · Uvicorn
│     Volumes: /data/uploads, /logs
│     Depends on: postgres, redis
│
├── postgres (port 5432)
│     postgres:16-alpine
│     Volume: /data/postgres
│     Init: /postgres/*.sql
│
├── redis (port 6379)
│     redis:7-alpine · 256MB LRU · AOF persistence
│     Volume: /data/redis
│
├── pgadmin (port 5050)
│     Database administration UI
│
└── redisinsight (port 5540)
      Redis administration UI

All services: mantle-net bridge network
```

---

## Multi-Tenancy

```
Every data record is scoped to org_id.

Request lifecycle:
  JWT Token → extract org_id + user_id + role
       │
       └── All queries filtered by current user's org_id
             • Org admins: see only their org
             • Super admin: cross-org access
             • Workers: filtered to their org + role permissions
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| API framework | FastAPI + asyncio | Non-blocking I/O for WebSockets and concurrent incident handling |
| ORM | SQLAlchemy 2.0 (async) | Type-safe, async-first ORM with migration support via Alembic |
| Auth | JWT (HS256) with refresh tokens | Stateless; mobile clients get 365-day access tokens |
| Real-time | WebSocket + Redis pub-sub | Redis decouples publishers from subscribers across server instances |
| LLM integration | Multi-provider with scripted fallback | Works offline; swap providers without code changes |
| Action approval | Tiered (green/red) | Automates low-risk actions while requiring human oversight for high-stakes decisions |
| Audit trail | Append-only `incident_event_log` | Legal/compliance — immutable, never downsampled |
| Telemetry retention | Hot/warm/cold tiers | Balances storage cost against operational and compliance needs |
| IDs | UUID | Avoids enumerable IDs across tenants |
| File storage | Local filesystem + SHA256 | Simple; swap for S3 by changing `evidence_service.py` |
