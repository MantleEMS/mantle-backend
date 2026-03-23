# Mantle EMS API Documentation

**Base URL:** `http://<host>/api/v1`
**Interactive Docs:** `/docs` (Swagger UI) · `/redoc` (ReDoc)

---

## Authentication

All endpoints (except login and token refresh) require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <access_token>
```

Tokens are JWTs signed with HS256. The access token payload includes `sub` (user ID) and `type: "access"`.

**Token lifetimes**

| Role | Access token | Refresh token |
|------|-------------|---------------|
| `worker`, `responder` | 365 days | 7 days |
| `commander`, `supervisor`, `admin` | 15 minutes | 7 days |

Workers and responders receive long-lived access tokens for uninterrupted mobile app use. The duration is controlled by `MOBILE_ACCESS_TOKEN_EXPIRE_DAYS` (server config).

### Roles

| Role | Permissions |
|------|-------------|
| `worker` | Read incidents, post messages, upload evidence |
| `responder` | Same as worker |
| `supervisor` | Same as worker + view all monitoring sessions in org |
| `commander` | All of above + approve/reject actions, resolve incidents |
| `admin` | Full access |

---

## Auth Endpoints

### POST `/auth/login`

Authenticate a user and receive tokens.

**Request**
```json
{
  "email": "user@example.com",
  "password": "secret"
}
```

**Response `200`**
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "refresh_token": "<jwt>",
  "user": {
    "id": "uuid",
    "name": "Jane Smith",
    "role": "commander",
    "org_id": "uuid"
  }
}
```

**Errors**
- `401` — Invalid credentials

---

### POST `/auth/refresh`

Exchange a refresh token for a new access token.

**Request**
```json
{
  "refresh_token": "<jwt>"
}
```

**Response `200`**
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

**Errors**
- `401` — Invalid or expired refresh token

---

### PUT `/users/me/device`

Register a device for push notifications.

**Auth required**

**Request**
```json
{
  "push_token": "ExponentPushToken[xxxx]",
  "platform": "ios",
  "device_model": "iPhone 15 Pro"
}
```

**Response `200`**
```json
{ "status": "registered" }
```

---

### PATCH `/users/me`

Update the current user's profile (status, location, phone).

**Auth required**

**Request** _(all fields optional)_
```json
{
  "status": "on_duty",
  "last_location": { "lat": 37.7749, "lng": -122.4194 },
  "phone": "+14155550123"
}
```

Valid `status` values: `active`, `on_duty`, `off_duty`

**Response `200`** — [UserOut](#userout)

**Errors**
- `400` — Invalid status value

---

## Incidents

### POST `/incidents`

Trigger a new incident (SOS).

**Auth required**

**Request**
```json
{
  "emergency_type": "workplace_violence",
  "trigger_source": "ui_button",
  "facility_id": "uuid",
  "location": {
    "lat": 37.7749,
    "lng": -122.4194,
    "address": "123 Main St",
    "accuracy_m": 5.0
  },
  "patient_info": {
    "name": "John Doe",
    "conditions": ["diabetes"],
    "allergies": ["penicillin"],
    "meds": ["metformin"],
    "emergency_contact": { "name": "Jane Doe", "phone": "+14155550100" }
  }
}
```

Valid `emergency_type` values: `workplace_violence`, `medical`, `other`, `generic`
Valid `trigger_source` values: `ui_button`, `voice`, `pendant`, `ai_detected`, `commander`

**Response `201`** — [IncidentOut](#incidentout)

---

### GET `/incidents`

List incidents for the current user's organization (max 100, newest first).

**Auth required**

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `status` | string | Filter by status: `triggered`, `active`, `resolved`, `cancelled` |

**Response `200`** — `IncidentOut[]`

---

### GET `/incidents/{incident_id}`

Get full incident details including participants, messages, and pending actions.

**Auth required**

**Response `200`** — [IncidentDetailOut](#incidentdetailout)

**Errors**
- `404` — Incident not found or not in user's org

---

### POST `/incidents/{incident_id}/resolve`

Resolve and close an incident.

**Auth required · Commander/Admin only**

**Request**
```json
{
  "resolution_note": "Situation de-escalated, patient transported."
}
```

**Response `200`**
```json
{
  "status": "resolved",
  "resolved_at": "2026-03-17T14:30:00Z"
}
```

**Errors**
- `403` — Commander role required
- `404` — Incident not found

---

## Actions

### GET `/incidents/{incident_id}/actions`

List all actions for an incident.

**Auth required**

**Response `200`** — [ActionOut[]](#actionout)

---

### POST `/incidents/{incident_id}/actions/{action_id}/approve`

Approve a pending action.

**Auth required · Commander/Admin only**

**Request** _(optional)_
```json
{
  "modifier": { "additional_responders": 2 }
}
```

**Response `200`**
```json
{
  "status": "approved",
  "approved_at": "2026-03-17T14:25:00Z"
}
```

**Errors**
- `403` — Commander role required
- `404` — Action not found

---

### POST `/incidents/{incident_id}/actions/{action_id}/reject`

Reject a pending action.

**Auth required · Commander/Admin only**

**Request** _(optional)_
```json
{
  "reason": "Situation already under control."
}
```

**Response `200`**
```json
{ "status": "rejected" }
```

**Errors**
- `403` — Commander role required
- `404` — Action not found

---

## Messages

### POST `/incidents/{incident_id}/messages`

Post a message to the incident thread.

**Auth required**

**Request**
```json
{
  "message_type": "text",
  "content": "Responder en route, ETA 3 minutes.",
  "metadata": {}
}
```

**Response `201`** — [MessageOut](#messageout)

---

### GET `/incidents/{incident_id}/messages`

List messages for an incident thread (max 200).

**Auth required**

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `after` | datetime (ISO 8601) | Return messages after this timestamp |
| `limit` | integer | Max messages to return (default 200) |

**Response `200`** — `MessageOut[]`

---

## Evidence

### POST `/incidents/{incident_id}/evidence`

Upload a file as evidence for an incident.

**Auth required**
**Content-Type:** `multipart/form-data`
**Max file size:** 100 MB

**Form Fields**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | Yes | The file to upload |
| `file_type` | string | Yes | `photo`, `audio`, `video`, `document` |
| `metadata` | string | No | JSON string with extra metadata (default `{}`) |

**Response `201`** — [EvidenceOut](#evidenceout)

**Errors**
- `400` — Invalid file_type or invalid metadata JSON

---

### GET `/incidents/{incident_id}/evidence`

List all evidence for an incident.

**Auth required**

**Response `200`** — `EvidenceOut[]`

---

## Monitoring (Lone Worker / Vigilant Mode)

Workers in high-risk mode can start a monitoring session. The app submits telemetry (location, fall detection, heart rate, speed, etc.) against that session. A `fall_detected` event automatically escalates the session by creating an incident.

Commanders and supervisors can view all active sessions for their org; workers see only their own.

---

### POST `/monitoring/sessions`

Start a monitoring session (activates vigilant/high-risk mode).

**Auth required**

**Request**
```json
{
  "check_in_interval_seconds": 300,
  "metadata": { "task": "home visit", "facility_id": "uuid" }
}
```

`check_in_interval_seconds` — optional; if set, the expected check-in interval for supervisory awareness. `metadata` — arbitrary key/value bag.

**Response `201`** — [MonitoringSessionOut](#monitoringsessionout)

---

### POST `/monitoring/sessions/{session_id}/end`

End an active monitoring session.

**Auth required** · Worker (own session) or Commander/Admin

**Request**
```json
{
  "reason": "manual"
}
```

Valid `reason` values: `manual`, `timeout`, `panic`

**Response `200`** — [MonitoringSessionOut](#monitoringsessionout)

**Errors**
- `400` — Session is not active
- `403` — Not the session owner and not commander/admin
- `404` — Session not found

---

### GET `/monitoring/sessions`

List monitoring sessions.

**Auth required**

Workers see only their own sessions. Commanders, admins, and supervisors see all sessions in the org.

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `status` | string | Filter by status: `active`, `ended`, `escalated` |
| `user_id` | UUID | Filter by worker (commander/admin only) |

**Response `200`** — `MonitoringSessionOut[]`

---

### GET `/monitoring/sessions/{session_id}`

Get a single monitoring session.

**Auth required** · Worker (own) or Commander/Admin/Supervisor

**Response `200`** — [MonitoringSessionOut](#monitoringsessionout)

**Errors**
- `403` — Access denied
- `404` — Session not found

---

### POST `/monitoring/sessions/{session_id}/telemetry`

Submit a batch of telemetry events for an active session.

**Auth required** · Session owner only

**Request**
```json
{
  "events": [
    {
      "event_type": "location",
      "data": { "lat": 37.7749, "lng": -122.4194, "accuracy_m": 5.0 },
      "recorded_at": "2026-03-17T14:25:00Z"
    },
    {
      "event_type": "heart_rate",
      "data": { "bpm": 92 },
      "recorded_at": "2026-03-17T14:25:01Z"
    },
    {
      "event_type": "speed",
      "data": { "kmh": 3.2 },
      "recorded_at": "2026-03-17T14:25:02Z"
    },
    {
      "event_type": "fall_detected",
      "data": { "confidence": 0.97, "location": { "lat": 37.7749, "lng": -122.4194 } },
      "recorded_at": "2026-03-17T14:25:03Z"
    }
  ]
}
```

Valid `event_type` values: `location`, `fall_detected`, `heart_rate`, `speed`, `custom`

`recorded_at` is the **client-side** timestamp (ISO 8601). The server records `received_at` separately.

**Fall detection auto-escalation:** if any event has `event_type: "fall_detected"`, the session is immediately escalated — an incident is created (emergency_type `medical`, trigger_source `ai_detected`) and the session status is set to `escalated`. The response includes the new incident ID.

**Response `200`** — [SubmitTelemetryResponse](#submittelemetryresponse)

**Errors**
- `400` — Session is not active, or no events provided
- `403` — Not the session owner
- `404` — Session not found

---

### GET `/monitoring/sessions/{session_id}/telemetry`

Retrieve telemetry events for a session.

**Auth required** · Session owner or Commander/Admin/Supervisor

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `event_type` | string | Filter by event type |
| `after` | datetime (ISO 8601) | Return events recorded after this time |
| `limit` | integer | Max events to return (default 200, max 1000) |

**Response `200`** — `TelemetryEventOut[]`

---

## Configuration

### GET `/orgs`

List all organizations.

**Auth required**

**Response `200`** — `OrganizationOut[]`

---

### GET `/orgs/{org_id}`

Get organization details.

**Auth required**

**Response `200`** — [OrganizationOut](#organizationout)

---

### GET `/facilities`

List facilities for the current user's organization.

**Auth required**

**Response `200`** — `FacilityOut[]`

---

### GET `/facilities/{facility_id}`

Get facility details.

**Auth required**

**Response `200`** — [FacilityOut](#facilityout)

---

### GET `/sops`

List Standard Operating Procedures.

**Auth required**

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `emergency_type` | string | Filter by emergency type |

**Response `200`** — `SOPOut[]`

---

### GET `/sops/{sop_id}`

Get SOP details including steps.

**Auth required**

**Response `200`** — [SOPOut](#sopout)

---

### GET `/users`

List users in the current user's organization.

**Auth required**

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `role` | string | Filter by role |
| `status` | string | Filter by status |

**Response `200`** — `UserOut[]`

---

### GET `/users/{user_id}`

Get user details.

**Auth required**

**Response `200`** — [UserOut](#userout)

---

## Search & Audit

### GET `/search`

Full-text search across incidents.

**Auth required**

**Query Parameters**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `q` | string | Yes | Search query |
| `from_date` | datetime | No | Filter: incidents after this date |
| `to_date` | datetime | No | Filter: incidents before this date |
| `emergency_type` | string | No | Filter by type |
| `severity_min` | integer | No | Minimum severity level (1–5) |

**Response `200`**
```json
{
  "results": [
    {
      "incident_id": "uuid",
      "incident_number": "INC-20260317-001",
      "emergency_type": "medical",
      "status": "resolved",
      "initiated_at": "2026-03-17T10:00:00Z",
      "matches": [{ "field": "messages", "excerpt": "..." }]
    }
  ],
  "total": 42
}
```

---

### GET `/audit`

Query the audit log with pagination.

**Auth required**

**Query Parameters**

| Param | Type | Description |
|-------|------|-------------|
| `incident_id` | UUID | Filter by incident |
| `event_type` | string | Filter by event type |
| `from_date` | datetime | Filter: events after this date |
| `to_date` | datetime | Filter: events before this date |
| `page` | integer | Page number (default 1) |
| `page_size` | integer | Results per page (default 20) |

**Response `200`**
```json
{
  "events": [
    {
      "id": "uuid",
      "org_id": "uuid",
      "incident_id": "uuid",
      "event_type": "incident.triggered",
      "actor_type": "human",
      "actor_id": "uuid",
      "detail": {},
      "created_at": "2026-03-17T10:00:00Z"
    }
  ],
  "total": 150,
  "page": 1,
  "page_size": 20
}
```

---

## WebSocket API

### `WS /incidents/{incident_id}/ws`

Real-time incident channel: receives live updates and sends location/heartbeat.

**Connection URL**
```
ws://<host>/api/v1/incidents/{incident_id}/ws?token=<access_token>&last_seq=0
```

**Query Parameters**

| Param | Required | Description |
|-------|----------|-------------|
| `token` | Yes | JWT access token |
| `last_seq` | No | Resume from this message sequence number |

**Close Codes**

| Code | Reason |
|------|--------|
| `4001` | Invalid or expired token |
| `4004` | Incident not found or access denied |

---

### Server → Client Messages

#### `snapshot` _(sent immediately on connect)_
Full current state of the incident.
```json
{
  "type": "snapshot",
  "incident": { ... },
  "participants": [ ... ],
  "messages": [ ... ],
  "pending_actions": [ ... ]
}
```

#### `participant.location`
Real-time location update for a participant.
```json
{
  "type": "participant.location",
  "user_id": "uuid",
  "lat": 37.7749,
  "lng": -122.4194,
  "updated_at": "2026-03-17T14:25:30Z"
}
```

#### `message` _(new thread messages)_
```json
{
  "type": "message",
  "data": { ... }
}
```

#### `action` _(action status changes)_
```json
{
  "type": "action",
  "data": { ... }
}
```

---

### Client → Server Messages

#### `heartbeat`
Must be sent every 30 seconds to maintain presence.
```json
{ "type": "heartbeat" }
```

#### `location`
Broadcast the current user's GPS location to all participants.
```json
{
  "type": "location",
  "lat": 37.7749,
  "lng": -122.4194,
  "accuracy_m": 4.5
}
```

---

## Schemas

### IncidentOut
```json
{
  "id": "uuid",
  "org_id": "uuid",
  "incident_number": "INC-20260317-001",
  "status": "active",
  "emergency_type": "medical",
  "trigger_source": "ui_button",
  "severity": 3,
  "facility_id": "uuid",
  "sop_id": "uuid",
  "commander_id": "uuid",
  "initiated_by": "uuid",
  "location": { "lat": 37.7749, "lng": -122.4194, "address": "123 Main St", "accuracy_m": 5.0 },
  "patient_info": { "name": "John Doe", "conditions": [], "allergies": [], "meds": [], "emergency_contact": {} },
  "ai_assessment": {},
  "initiated_at": "2026-03-17T10:00:00Z",
  "resolved_at": null,
  "resolved_by": null,
  "created_at": "2026-03-17T10:00:00Z",
  "updated_at": "2026-03-17T10:01:00Z"
}
```

### IncidentDetailOut
```json
{
  "incident": { ... },
  "participants": [ ... ],
  "messages": [ ... ],
  "pending_actions": [ ... ]
}
```

### ActionOut
```json
{
  "id": "uuid",
  "incident_id": "uuid",
  "sop_step": 1,
  "tier": "green",
  "action_type": "dispatch_responder",
  "status": "pending",
  "description": "Dispatch nearest available responder.",
  "assigned_to": "uuid",
  "approved_by": null,
  "approved_at": null,
  "executed_at": null,
  "detail": {},
  "created_at": "2026-03-17T10:00:00Z",
  "updated_at": "2026-03-17T10:00:00Z"
}
```

**`tier` values:** `green` (auto-executes), `amber` (escalated, needs approval), `red` (requires immediate commander approval)
**`action_type` values:** `begin_recording`, `alert_commander`, `dispatch_responder`, `contact_911`, `notify_emergency_contact`, `resolve_incident`
**`status` values:** `pending` → `approved` / `rejected` → `executed` / `failed` / `expired`

### MessageOut
```json
{
  "id": "uuid",
  "incident_id": "uuid",
  "sender_id": "uuid",
  "sender_type": "human",
  "message_type": "text",
  "content": "Responder en route.",
  "meta": {},
  "seq": 42,
  "created_at": "2026-03-17T10:05:00Z"
}
```

### EvidenceOut
```json
{
  "id": "uuid",
  "incident_id": "uuid",
  "uploaded_by": "uuid",
  "file_type": "photo",
  "file_name": "scene.jpg",
  "file_size_bytes": 204800,
  "sha256_hash": "abc123...",
  "duration_seconds": null,
  "mime_type": "image/jpeg",
  "meta": {},
  "created_at": "2026-03-17T10:10:00Z"
}
```

### UserOut
```json
{
  "id": "uuid",
  "org_id": "uuid",
  "email": "user@example.com",
  "name": "Jane Smith",
  "phone": "+14155550123",
  "role": "commander",
  "status": "on_duty",
  "qualifications": ["cpr", "first_aid"],
  "medical_flags": [],
  "device_info": { "platform": "ios", "push_token": "..." },
  "last_location": { "lat": 37.7749, "lng": -122.4194 },
  "created_at": "2026-03-17T09:00:00Z",
  "updated_at": "2026-03-17T10:00:00Z"
}
```

### OrganizationOut
```json
{
  "id": "uuid",
  "name": "Acme Healthcare",
  "slug": "acme-healthcare",
  "settings": {},
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

### FacilityOut
```json
{
  "id": "uuid",
  "org_id": "uuid",
  "name": "Downtown Clinic",
  "facility_type": "hospital",
  "address": { "street": "123 Main St", "city": "San Francisco", "state": "CA" },
  "risk_flags": ["low_cell_coverage"],
  "cell_coverage": "fair",
  "nearest_hospital": { "name": "SF General", "distance_km": 1.2 },
  "notes": "Elevator key required for floors 3+.",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

**`facility_type` values:** `patient_home`, `office`, `hospital`, `snf`, `other`
**`cell_coverage` values:** `good`, `fair`, `poor`, `none`, `unknown`

### MonitoringSessionOut
```json
{
  "id": "uuid",
  "org_id": "uuid",
  "user_id": "uuid",
  "status": "active",
  "check_in_interval_seconds": 300,
  "last_check_in": "2026-03-17T14:25:00Z",
  "started_at": "2026-03-17T14:20:00Z",
  "ended_at": null,
  "end_reason": null,
  "incident_id": null,
  "meta": { "task": "home visit" },
  "created_at": "2026-03-17T14:20:00Z",
  "updated_at": "2026-03-17T14:25:00Z"
}
```

**`status` values:** `active`, `ended`, `escalated`
**`end_reason` values:** `manual`, `timeout`, `panic`, `escalated`, `superseded`

### TelemetryEventOut
```json
{
  "id": "uuid",
  "session_id": "uuid",
  "user_id": "uuid",
  "org_id": "uuid",
  "event_type": "location",
  "data": { "lat": 37.7749, "lng": -122.4194, "accuracy_m": 5.0 },
  "recorded_at": "2026-03-17T14:25:00Z",
  "received_at": "2026-03-17T14:25:00Z"
}
```

**`event_type` values:** `location`, `fall_detected`, `heart_rate`, `speed`, `custom`

### SubmitTelemetryResponse
```json
{
  "accepted": 4,
  "escalated": false,
  "incident_id": null
}
```

When a `fall_detected` event triggers auto-escalation:
```json
{
  "accepted": 1,
  "escalated": true,
  "incident_id": "uuid"
}
```

### SOPOut
```json
{
  "id": "uuid",
  "org_id": "uuid",
  "name": "Medical Emergency Response",
  "sop_code": "MED-001",
  "emergency_type": "medical",
  "description": "Step-by-step protocol for medical emergencies.",
  "steps": [
    {
      "step": 1,
      "title": "Assess Scene",
      "description": "Ensure area is safe before approaching.",
      "action_type": "dispatch_responder",
      "tier": "green",
      "auto_execute": true
    }
  ],
  "responder_checklist": ["Gloves", "AED", "First aid kit"],
  "is_active": true,
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```
