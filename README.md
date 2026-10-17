# Z-Phoenix
 AI-driven resilience platform that combines controlled chaos engineering, telemetry analysis and agentic AI. Instead of waiting for a production failure it
# Z-PHOENIX Project Structure

```text
z-phoenix/
├── app.py                 # Flask app, SocketIO setup, API routes & page routers
├── config.py              # Ports, DB path, Granite/Groq API keys & fallback configs
├── agents.py              # Diagnosis, Recovery, and Validation agent logic with fallbacks
├── attack.py              # Chaos simulation subprocesses and safety limits
├── governance.py          # Approval gates, execution handler, playbook versioning
├── workload.py            # Simulated CICS/Db2 telemetry generator & socket streamer
├── database.py            # SQLite schema initialization and query helpers
├── requirements.txt       # flask, flask-socketio, requests, eventlet, etc.
├── static/
│   ├── css/
│   │   └── style.css      # Enterprise dark theme, status badges, layout grids
│   └── js/
│       ├── socket.js      # WebSocket connection and metric event bindings
│       ├── charts.js      # Chart.js initialization and live 1 Hz stream updating
│       └── app.js         # Navigation, button bindings, agent console rendering
└── templates/
    ├── base.html          # Shell layout (sidebar, topbar, status indicator)
    ├── command_center.html
    ├── chaos_lab.html
    ├── playbooks.html
    └── audit.html

```


## 👥 Team Overview

| Member | Name | Role | Primary Files |
|---|---|---|---|
| M1 | Kishore | AI Diagnostics & Agent Engineer | `agents.py`, `config.py` |
| M2 | Kiran | Backend & Workflow Engineer | `app.py`, `requirements.txt` |
| M3 | Ritu | Database & Governance Engineer | `database.py`, `governance.py` |
| M4 | Meer Mohammed Shoaib | Telemetry, Chaos & Simulation Engineer | `attack.py`, `workload.py` |
| M5 | Nandan | Frontend & Visualization Engineer | `templates/`, `static/css/`, `static/js/` |
| M6 | Rishitha | Integration Automation, QA & Demo Video Lead | `tests/`, `scripts/` |

---

## 🧠 Member 1: Kishore
### AI Diagnostics & Agent Engineer

**Files owned:**
- `agents.py`
- `config.py`

**Responsibilities:**
- Integrate IBM Granite through the selected API provider.
- Implement the Diagnosis, Recovery, and Validation agents.
- Generate structured JSON containing root-cause hypotheses, evidence, confidence estimates, risk levels, and recovery proposals.
- Handle API timeouts, bounded retries, malformed responses, and deterministic fallbacks.
- Ensure AI-generated recovery actions remain proposals and cannot execute commands directly.
- Coordinate response schemas with the backend and governance engineers.

**Definition of Done:**
- Agents return predictable structured results.
- Failures and timeouts do not crash the application.
- Fallback behavior works without the AI provider.
- AI agents cannot bypass governance or approval controls.

---

## ⚙️ Member 2: Kiran
### Backend & Workflow Engineer

**Files owned:**
- `app.py`
- `requirements.txt`

**Responsibilities:**
- Build the Flask application and Flask-SocketIO integration.
- Implement REST API endpoints and consistent response formats.
- Connect telemetry, chaos simulation, AI agents, governance, database operations, and frontend events.
- Orchestrate the complete incident lifecycle.
- Stream telemetry and workflow updates to the frontend.
- Handle errors and return appropriate HTTP status codes.
- Coordinate API contracts with all team members.

**Proposed API Endpoints:**

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Check application readiness |
| GET | `/api/telemetry` | Retrieve current telemetry |
| POST | `/api/attack` | Start a bounded chaos scenario |
| POST | `/api/analyze` | Diagnose an incident |
| POST | `/api/validate` | Validate a recovery proposal |
| POST | `/api/approve` | Record human approval |
| POST | `/api/recover` | Execute authorized recovery |
| GET | `/api/playbooks` | List saved playbooks |
| POST | `/api/replay` | Replay an eligible playbook |
| GET | `/api/audit` | Retrieve audit events |

These endpoints must be agreed upon by the team before integration.

**Definition of Done:**
- The core workflow operates end to end.
- API routes return consistent responses.
- SocketIO streams updates reliably.
- Recovery requests pass through governance checks.
- The application starts using the agreed configuration.

---




## 🗄️ Member 3: Ritu
### Database & Governance Engineer

**Files owned:**
- `database.py`
- `governance.py`

**Responsibilities:**
- Create and maintain the SQLite database schema.
- Store incidents, telemetry snapshots, diagnoses, playbooks, and audit events.
- Implement action allowlists and risk-based governance policies.
- Block unauthorized and unapproved recovery execution.
- Require explicit human approval according to the agreed risk policy.
- Maintain versioned playbooks without overwriting historical versions.
- Record timestamps, actors, actions, targets, risk levels, and relevant evidence.
- Coordinate database interfaces with the backend and AI engineers.

**Core Database Tables:**

| Table | Purpose |
|---|---|
| `incidents` | Incident details, telemetry, diagnosis, timestamps, and status |
| `playbooks` | Recovery steps, versions, validation results, and approval status |
| `audit_log` | Recorded AI, system, and human actions |

**Definition of Done:**
- Database initialization works reliably.
- Incidents and playbooks persist correctly.
- Unapproved execution attempts are rejected.
- Playbook version history is preserved.
- Governance decisions are covered by tests.

---




## ⚡ Member 4: Meer Mohammed Shoaib
### Telemetry, Chaos & Simulation Engineer

**Files owned:**
- `attack.py`
- `workload.py`

**Responsibilities:**
- Collect real host CPU and RAM metrics using a suitable library such as `psutil`.
- Generate simulated CICS transaction throughput (TPS) and Db2 lock-wait metrics when no real workload is connected.
- Implement controlled fault scenarios:
  - `CPU_RUNAWAY`: bounded CPU-load simulation.
  - `DB2_LOCK_CONTENTION`: simulated transaction queue or lock-wait contention.
- Ensure telemetry changes visibly when a fault is injected and returns toward baseline after recovery.
- Enforce strict resource, duration, worker-count, and intensity limits.
- Implement a reliable `stop_chaos()` emergency kill switch.
- Terminate only processes created by the chaos engine.
- Automatically stop fault workers after the configured maximum duration.
- Capture before, during, and after metrics for recovery validation.
- Coordinate telemetry formats and function interfaces with the backend and QA engineers.

**Safety Requirements:**
- Never run unrestricted host-wide stress tests.
- Never kill arbitrary system processes.
- Track and clean up all chaos worker processes.
- Use conservative defaults suitable for the available LinuxONE resources.
- Ensure the emergency stop remains available if a scenario fails.
- Clearly label simulated CICS/Db2 metrics in the dashboard and demo.

**Definition of Done:**
- Fault scenarios start and stop predictably.
- Telemetry reflects the injected fault and recovery.
- Resource and duration caps are enforced.
- The kill switch works reliably.
- No orphaned chaos workers remain.
- The LinuxONE host remains responsive during testing.

---




## 🎨 Member 5: Nandan
### Frontend & Visualization Engineer

**Files owned:**
- `templates/`
- `static/css/style.css`
- `static/js/socket.js`
- `static/js/charts.js`
- `static/js/app.js`

**Template Files:**
- `base.html`: Shared layout, navigation, and status indicator.
- `command_center.html`: System health and AI workflow dashboard.
- `chaos_lab.html`: Fault controls and emergency stop.
- `playbooks.html`: Saved playbooks and replay controls.
- `audit.html`: Audit history and event details.

**Responsibilities:**
- Build a polished, responsive enterprise-style dashboard.
- Implement the Command Center, Chaos Lab, Playbook Library, and Audit Log.
- Display live CPU, RAM, TPS, and lock-wait metrics.
- Use Chart.js for live telemetry and before/during/after comparisons.
- Display the diagnosis, recovery proposal, validation, approval, and recovery stages.
- Connect UI controls to the agreed backend endpoints.
- Implement loading, success, failure, and approval-required states.
- Display playbook versions, replay results, and audit events.
- Clearly label simulated CICS/Db2 data.

**Suggested Visual Semantics:**
- 🔴 Red: Critical incident or failure.
- 🟠 Amber: Degraded state or approval required.
- 🟢 Green: Healthy state or successful validation.
- 🟣 Purple: AI activity.
- 🔵 Cyan: Transaction throughput and telemetry.

**Definition of Done:**
- All pages are navigable and connected to the backend.
- Charts update smoothly using application events.
- Buttons perform their intended actions.
- Loading and error states are handled.
- Simulated and real metrics are clearly distinguished.

---




## 🧪 Member 6: Rishitha
### Integration Automation, QA & Demo Video Lead

**Files owned:**
- `tests/`
- `scripts/`

**Test Files:**
- `tests/test_golden_path.py`: Complete recovery lifecycle test.
- `tests/test_failure_modes.py`: AI failures, rejected approvals, and unsafe execution tests.
- `tests/test_safety_caps.py`: Resource-limit and process-cleanup tests.

**Script Files:**
- `scripts/run_10x_suite.py`: Ten consecutive full workflow runs.
- `scripts/verify_readiness.py`: Environment and service readiness checks.
- `scripts/record_demo_checklist.md`: Backup demo recording checklist.

**Responsibilities:**
- Test the complete Golden Path:

  `Baseline → Inject → Detect → Diagnose → Validate → Approve → Recover → Store → Replay`

- Write integration tests for the backend API and database.
- Test AI provider timeouts and malformed responses.
- Verify rejected approvals and unauthorized recovery attempts are blocked.
- Test failed validation, failed recovery, and chaos-process cleanup.
- Build a repeatable test runner that summarizes passes, failures, and diagnostics.
- Measure playbook replay performance against the approximately two-second target.
- Check dependencies, application health, port 8080, SQLite tables, and AI configuration.
- Coordinate with teammates to reproduce and report integration defects.
- Record and verify a backup demo video before the deadline.

**Definition of Done:**
- Golden Path tests cover the integrated workflow.
- Failure-mode tests confirm unsafe actions are blocked.
- Safety tests verify resource caps and process cleanup.
- The ten-run suite produces a clear, repeatable report.
- Readiness checks identify missing configuration and unavailable services.
- The backup video is playable and accessible offline.

**Note:** Ten successful runs and the replay performance target must be demonstrated through actual testing, not assumed.

---

## 🏗️ System Architecture

```text
Frontend: templates/ + static/
              |
              v
app.py: Flask REST API + SocketIO
              |
              +--> workload.py
              |    Host and simulated telemetry
              |
              +--> attack.py
              |    Bounded chaos injection
              |
              +--> agents.py
              |    AI diagnosis and recovery proposals
              |
              +--> governance.py
              |    Validation and approval enforcement
              |
              +--> database.py
              |    Incidents, playbooks and audit logs
              |
              +--> tests/ and scripts/
                   Integration, safety and readiness checks
```

This is the intended logical architecture. Integration must be tested before claiming every connection is complete.

---

## 🔐 Shared Safety & Collaboration Rules

1. AI agents propose recovery actions; they must not directly execute arbitrary commands.
2. Recovery execution must pass validation, governance checks, and the required human approval.
3. Chaos simulations must obey configured resource and duration limits.
4. The kill switch must terminate only processes created by the chaos engine.
5. Record meaningful AI, system, and human actions in the audit log.
6. Never commit API keys, passwords, tokens, or `.env` files to GitHub.
7. Do not commit virtual environments, cache directories, generated databases, or private logs.
8. Do not modify another member's owned files without prior coordination.
9. Use feature branches and pull requests rather than having everyone push directly to `main`.
10. Label CICS/Db2 telemetry as simulated unless it comes from a real connected workload.
11. Keep a known-good version and a verified backup demo ready before judging.

---

## 🚀 Recommended Integration Plan

1. **Agree on interfaces:** API routes, telemetry payloads, agent JSON schemas, database fields, and governance states.
2. **Build the first Golden Path:** One bounded fault, diagnosis, validation, explicit approval, recovery, and saved playbook.
3. **Connect the dashboard:** Stream host metrics and clearly labelled simulated workload metrics.
4. **Implement audit and replay:** Persist events and replay only eligible, validated, approved playbooks.
5. **Test failures and safety:** Verify timeouts, denied actions, cleanup, and recovery behavior.
6. **Run the ten-cycle suite:** Fix failures and preserve the test report.
7. **Verify deployment readiness:** Check dependencies, configuration, database, port, and service health.
8. **Record the backup demo:** Verify that the recording is playable offline and reflects the actual application.

---

## ✅ Definition of Team Success

Z-PHOENIX is ready to present when the team can demonstrate a complete controlled failure-and-recovery cycle, explain the evidence behind the diagnosis, show that human approval is enforced, replay a previously validated playbook, and provide an audit trail.

All results must be reported honestly, distinguishing completed features from targets and simulated behavior.

---

**Z-PHOENIX — Practice failure safely. Learn recovery. Be ready when it matters.**

## Backend API and local operation

Install the packages in `requirements.txt`, then start the service with `python app.py`. The default listener is configured by `config.py` for the project demo environment. The SQLite database is initialized on startup; set `Z_PHOENIX_DB` to choose a different database file.

### Configuration

| Variable | Purpose | Default behavior |
|---|---|---|
| `Z_PHOENIX_APPROVAL_TOKEN` | Shared operator credential required by approval, rejection, and recovery requests. Send it as `X-Z-Phoenix-Approval-Token`. | Unset disables approvals and recovery (HTTP 503). |
| `Z_PHOENIX_ENABLE_CHAOS` | Enables the bounded CPU simulation through the chaos endpoints. | Disabled. |
| `Z_PHOENIX_SECRET_KEY` | Flask session signing key. | Random per process; set a persistent secret for deployments that use sessions. |
| `Z_PHOENIX_ALLOWED_ORIGINS` | Comma-separated Socket.IO origins trusted for cross-origin clients. | Same-origin only. |
| `Z_PHOENIX_DB` | SQLite database path. | Project-local SQLite database. |
| `GROQ_API_KEY`, `HF_API_TOKEN` | Optional credentials for the configured AI provider. | Deterministic agent fallback is used when unavailable. |

Do not put secrets in source control, request bodies, audit details, or logs. The `actor` field is an operator-supplied audit label; production deployments must connect it to SSO or another identity provider before relying on individual attribution.

### API contracts

All responses are JSON. Errors use `{"ok": false, "error": "...", "status": <HTTP status>}`. JSON request bodies must be objects and are limited to 16 KiB. List limits are bounded to prevent unbounded reads.

| Method and path | Request | Success | Common failures |
|---|---|---|---|
| `GET /api/health` | None | `200`: readiness and database dependency state | `503`: database unavailable |
| `GET /api/telemetry` | Optional | `200`: sample, chaos state, workflow, and per-metric source labels | `500`: unexpected service failure |
| `POST /api/attack` | `{"scenario":"CPU_RUNAWAY","intensity":60}` plus operator token when enabled | `202`: bounded simulation marked `simulation_only` | `400`: unsupported scenario/range; `401`: bad token; `403`: disabled by default; `503`: worker or auth configuration unavailable |
| `POST /api/analyze` | `{}`; analyzes current telemetry | `200`: diagnosis and incident reference | `400`: malformed JSON; `500`: service failure |
| `POST /api/validate` | Optional `{"incident_id":1}` | `200`: PASS and plan-bound `approval_id`; `422`: failed validation | `409`: no plan; `400`: malformed JSON |
| `POST /api/approve` | `{"actor":"operator","incident_id":1,"approval_id":"..."}` plus `X-Z-Phoenix-Approval-Token` | `200`: approval recorded only; includes expiry | `401`: invalid credential; `403`: stale/missing approval; `409`: plan or incident mismatch; `503`: credential not configured |
| `POST /api/recover` | Same actor, incident, and `approval_id` as approval, plus the same header | `200`: simulated recovery, audit and playbook persistence | `401/403`: authentication or approval gate; `409`: validation/plan mismatch; `500`: recovery failure |
| `GET /api/playbooks` | Optional | `200`: approved, validated playbooks with action IDs and matching metadata | `500`: storage unavailable |
| `POST /api/replay` | `{"playbook_id":"PB-001","mode":"safe"}` | `200`: allowlist-checked simulation; `commands_executed` is always `0` | `400`: invalid mode/body; `403`: unapproved playbook; `404`: unknown playbook; `422`: invalid steps |
| `GET /api/recovery_passport/{incident_id}` | Incident identifier | `200`: derived passport containing recorded evidence, diagnosis, approval events, outcome and audit links | `404`: unknown incident |
| `GET /api/audit?limit=100&incident_id=1` | Optional bounded filters (`limit` 1–500) | `200`: filtered audit records with sensitive-key fields omitted | `400`: invalid filters |

`POST /api/approve` records approval only; it never executes recovery. The frontend sends the configured token in `X-Z-Phoenix-Approval-Token`, then makes a separate `POST /api/recover` with the same approval ID, incident ID, actor label, and token. Approval expires after five minutes, is consumed once, and is invalidated by a plan change or service restart. The command-center frontend follows this two-request contract. See [docs/backend-frontend-contract.md](docs/backend-frontend-contract.md) for request examples, response handling, and error behavior.

### Integration boundaries and limitations

- Host CPU/RAM are observed where the platform probe is available. CICS TPS, Db2 lock counts, and fault responses are simulated; the API labels these sources explicitly.
- Chaos injection starts bounded local worker processes when enabled. It is disabled unless `Z_PHOENIX_ENABLE_CHAOS=true` is set.
- Read-only GET routes are unauthenticated in this demo. Put them behind a trusted network boundary or authentication proxy before exposing the service beyond the local rehearsal environment. The emergency chaos stop stays available without a credential so an operator can always stop simulation workers.
- Recovery playbook steps are simulated and audited. This backend does not invoke shell commands or execute AI-produced code.
- `POST /api/replay` is side-effect-free and only reports what the validated steps would do. The older `/api/replay/{playbook_ref}` route requires the operator token and a previously approved playbook, changes only in-process simulation state, and does not execute stored command strings.
- Recovery Passport responses are assembled from incident and audit records rather than a dedicated passport table. New bounded recoveries record before/after telemetry snapshots in the audit log; older incidents without those records are labeled as unavailable.
- Operator authentication is a shared token, not per-user identity. LinuxONE deployment still needs secret provisioning, trusted network/TLS termination, and SSO/identity binding for `actor` values. No live LinuxONE, Db2, or CICS adapter is configured or claimed here.
- The database/governance, telemetry/chaos, AI, and frontend modules remain owned by their assigned teammates. Changes to their adapters or UI contract should be coordinated with those owners.
