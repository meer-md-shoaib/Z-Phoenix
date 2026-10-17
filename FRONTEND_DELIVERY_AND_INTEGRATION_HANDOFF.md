# Z-PHOENIX — Frontend Delivery & Backend Integration Handoff Specification

> **Document Version:** 1.0  
> **Author:** Nandan (Member 5 — Frontend & Visualization Engineer)  
> **Project:** Z-PHOENIX (IBM Datathon 2026 — Wildcard Track)  
> **Date:** October 2026  
> **Status:** **COMPLETE & READY FOR INTEGRATION**  

---

## 1. Executive Summary

As the **Frontend & Visualization Engineer (Member 5 / M2 Frontend)**, I have completed the entire user interface and client-side integration architecture for **Z-PHOENIX**. 

The frontend implements the complete **Golden Path** resilience workflow:
$$\text{Baseline} \longrightarrow \text{Chaos Inject} \longrightarrow \text{Detect} \longrightarrow \text{Diagnose} \longrightarrow \text{Validate} \longrightarrow \text{Human Approval} \longrightarrow \text{Recover} \longrightarrow \text{Store / Replay}$$

The UI is built using **HTML5, Vanilla CSS3, JavaScript (ES6+), Chart.js, and Flask/Jinja2 templates**. It runs cleanly in **standalone demo/mock mode** right out of the box when the backend is offline, and **automatically switches to live connected mode** as soon as the Flask API and Socket.IO servers come online.

---

## 2. Inventory of Delivered Files

All files are placed in their exact designated directories, leaving all backend-owned modules (`app.py`, `agents.py`, `attack.py`, `database.py`, `governance.py`, `workload.py`) untouched:

```text
z-phoenix/
├── templates/
│   ├── base.html                 # Core application shell, sidebar, topbar, modal, toasts
│   ├── command_center.html       # Command Center (KPIs, live Chart.js stream, Golden Path)
│   ├── dashboard.html            # Alias/canonical template for Command Center
│   ├── chaos_lab.html            # Chaos Lab (fault injection controls, emergency kill-switch)
│   ├── experiments.html          # Alias/canonical template for Chaos Lab
│   ├── agents.html               # AI Agent Console (Granite diagnosis, proposal, validation, approval)
│   ├── playbooks.html            # Playbook Library (versioning, similarity match, replay)
│   ├── incidents.html            # Incidents & Recovery Passport (before/during/after snapshots)
│   └── audit.html                # Audit Log (read-only immutable trail with multi-filtering)
│
└── static/
    ├── css/
    │   └── style.css             # Enterprise dark design system, semantic tokens, responsive layout
    └── js/
        ├── base.js               # API adapter, health check, demo auto-toggle, toasts, Socket.IO
        ├── dashboard.js          # Live Chart.js telemetry, KPI cards, incident banner, timeline
        ├── experiments.js        # Fault scenario selection, duration/intensity sliders, kill-switch
        ├── agents.js             # 4-stage agent console & human-in-the-loop approval gate
        ├── playbooks.js          # Playbook cards, similarity bars, modal viewer, replay handler
        ├── incidents.js          # Incident list & Recovery Passport 3-stage snapshot renderer
        ├── audit.js              # Read-only audit trail renderer with actor/action/risk filtering
        ├── socket.js             # Compatibility bridge for Socket.IO connection
        ├── charts.js             # Compatibility bridge for Chart.js helpers
        └── app.js                # Compatibility bridge for application UI helpers
```

---

## 3. Completed Roles & Responsibilities

### 3.1 Design System & Layout (`base.html`, `style.css`)
- **Enterprise Dark Theme:** Tailored dark palette (`#0a0c10` background, `#111827` cards, `#1f2937` borders) reflecting a premium IBM Z product.
- **Strict Semantic Color Semantics:**
  - 🔴 **Red (`#ef4444`):** Critical anomaly, threshold breach, active incident, kill-switch.
  - 🟠 **Amber (`#f59e0b` / `#eab308`):** Degraded state, human approval gate, warning notices.
  - 🟢 **Green (`#22c55e`):** Healthy baseline, successful validation, successful recovery.
  - 🟣 **Purple (`#a855f7`):** IBM Granite AI activity, reasoning in progress.
  - 🔵 **Cyan (`#38bdf8`):** Simulated CICS transaction throughput, host telemetry info.
- **Responsive Layout:** Tested from 1920px widescreen down to 560px mobile viewports with an accessible off-canvas hamburger navigation drawer.
- **Accessibility & UX:** Visible keyboard focus rings, semantic HTML5 tags, screen-reader aria labels, modal dialogs with Escape/backdrop click dismiss, and an animated toast notification system.

### 3.2 Command Center (`command_center.html`, `dashboard.js`)
- **Metric KPI Cards:** Live host CPU load (%), host RAM usage (GB), simulated CICS TPS, and simulated Db2 lock wait counters.
- **Live Telemetry Stream:** Dual-axis Chart.js line graph plotting host CPU % (left axis, 0–100%) against simulated TPS (right axis, 0–200 tx/s). 60-second sliding history updated without canvas flickering (`update('none')`).
- **Honest Mainframe Labeling:** Host CPU and RAM are badged with `HOST METRIC`; CICS TPS and Db2 lock metrics are badged with `SIMULATED` and equipped with warning tooltips so hackathon judges are never misled.
- **Golden Path Stage Tracker:** Visual 8-step indicator tracking the incident lifecycle from Baseline to Replay.
- **Active Incident Banner:** Top-of-page warning alert triggered during faults with a direct link to the AI Agent Console.

### 3.3 Chaos Lab (`chaos_lab.html`, `experiments.js`)
- **Fault Scenarios:** Selectable cards for:
  1. `CPU Runaway Job` (`cpu_spike`) — Bounded CPU exhaustion.
  2. `Db2 Lock Contention` (`db2_lock_contention`) — Transaction queue lock chain.
  3. `Memory Pressure` (`memory_pressure`) — Memory allocation capped at 3.5 GB.
- **Guard-Rail Controls:** Interactive duration slider (10s–120s, default 30s) and intensity selector (Low/Medium/High).
- **Emergency Kill-Switch:** Prominent red kill-switch button that confirms and dispatches `/api/attack/stop` to terminate chaos workers.
- **Host Safety Notice:** Prominent banner informing judges that faults are strictly bounded by sandbox limits and cannot crash the LinuxONE host.

### 3.4 AI Agent Console (`agents.html`, `agents.js`)
- **Progressive 4-Stage Gated Flow:**
  1. **Diagnosis Agent:** Renders root cause hypothesis, supporting evidence, confidence score meter (0–100%), risk level pill, estimated blast radius, and deterministic fallback notices.
  2. **Recovery Agent:** Renders recovery proposal as structured, numbered actions with plain-language descriptions, rollback conditions, and allowlisted action IDs (no unformatted raw JSON dumps).
  3. **Validation Agent:** Executes sandbox replay simulation and reports `PASS` or `FAIL`.
  4. **Human Approval Gate:** Explicit authorization gate requiring an operator's approval. Recovery execution remains disabled in the UI until validation passes and human approval is confirmed.
  5. **Recovery Execution:** Gated execute button that invokes the allowlisted recovery plan and returns the system to baseline.

### 3.5 Playbook Library (`playbooks.html`, `playbooks.js`)
- **Versioned Fixes:** Renders playbook cards with ID (`PB-001`), version number (`v3`), trigger signature, steps count, validation status, approving operator, and timestamp.
- **Similarity Match Scoring:** Visual percentage bar showing signature match confidence against incoming anomalies.
- **One-Click Replay:** Dedicated replay action enabled strictly for `APPROVED` or `VALIDATED` playbooks, demonstrating target ~2-second recovery without re-running AI diagnosis.

### 3.6 Incidents & Recovery Passport (`incidents.html`, `incidents.js`)
- **Incident History Master List:** Displays active and resolved incidents with fault classification and timestamps.
- **Recovery Passport Comparison View:** Side-by-side snapshot comparison cards:
  - 📊 **Baseline Snapshot:** Telemetry prior to the fault.
  - 🔥 **During Fault Snapshot:** Peak anomaly values (CPU spike, TPS collapse, lock contention).
  - ✅ **Post-Recovery Snapshot:** Telemetry proving recovery back to baseline.
- **Lifecycle Timeline:** Step-by-step audit of the incident from detection to resolution.

### 3.7 Audit Log (`audit.html`, `audit.js`)
- **Immutable Read-Only Interface:** Strictly displays recorded actions with timestamp, actor (`system`, `user`, `agent`), action type, target/details, and risk rating.
- **Zero Modification Controls:** Conforms to audit integrity by providing no edit, delete, or modify actions.
- **Filter Toolbar:** Instant client-side filtering by actor, action category, and risk level.

---

## 4. What the Frontend Expects from the Backend Team

The frontend integration adapter is centralized in [`static/js/base.js`](file:///c:/Users/nanda/Desktop/Z-Phoenix/static/js/base.js). Below are the exact contracts and formats expected from each backend team member.

### 4.1 From Member 2: Kiran (Backend & Workflow Engineer — `app.py`)

#### Flask Page Routing
The Flask app should render the Jinja2 templates using standard routes:
```python
@app.route('/')
@app.route('/command-center')
def command_center():
    return render_template('command_center.html', active_page='dashboard')

@app.route('/chaos-lab')
def chaos_lab():
    return render_template('chaos_lab.html', active_page='experiments')

@app.route('/agents')
def agents():
    return render_template('agents.html', active_page='agents')

@app.route('/playbooks')
def playbooks():
    return render_template('playbooks.html', active_page='playbooks')

@app.route('/incidents')
def incidents():
    return render_template('incidents.html', active_page='incidents')

@app.route('/audit')
def audit():
    return render_template('audit.html', active_page='audit')
```

#### REST API Endpoints

| Method | Endpoint | Expected Request Body | Expected Response JSON | Notes |
|---|---|---|---|---|
| `GET` | `/api/health` | None | `{"status": "ok", "version": "1.0", "host": "linuxone"}` | When 200 OK, frontend switches from `DEMO MODE` to `CONNECTED`. |
| `GET` | `/api/telemetry` | None | See Section 4.4 | Polled every 2s if Socket.IO is disconnected. |
| `POST` | `/api/attack` | `{"scenario": "cpu_spike", "duration": 30, "intensity": "medium"}` | `{"status": "ok", "message": "Fault started", "incident_id": "INC-001"}` | Triggers chaos fault in sandbox. |
| `POST` | `/api/attack/stop` | `{}` | `{"status": "ok", "message": "Kill switch executed"}` | Emergency stop for chaos processes. |
| `GET` | `/api/attack/status` | None | `{"active": false, "result": "Fault completed"}` | Polled during faults. |
| `POST` | `/api/analyze` | `{"incident_id": "INC-001"}` | See Section 4.2 | Triggers Granite diagnosis & recovery proposal. |
| `POST` | `/api/validate` | `{"incident_id": "INC-001", "proposal": {...}}` | See Section 4.2 | Sandbox replay validation. |
| `POST` | `/api/approve` | `{"incident_id": "INC-001", "approved": true}` | `{"status": "ok", "message": "Approval recorded"}` | Human approval gate. |
| `POST` | `/api/recover` | `{"incident_id": "INC-001"}` | `{"status": "ok", "success": true, "message": "Recovery complete"}` | Executes allowlisted recovery. |
| `GET` | `/api/playbooks` | None | `{"playbooks": [...]}` (See Section 4.3) | List saved playbooks. |
| `POST` | `/api/replay` | `{"playbook_id": "PB-001"}` | `{"status": "ok", "success": true, "message": "Replay applied in 1.8s"}` | Replay validated fix. |
| `GET` | `/api/incidents` | Query: `?limit=20` | `{"incidents": [...]}` (See Section 4.3) | Incident history & snapshots. |
| `GET` | `/api/audit` | Query: `?limit=100` | `{"events": [...]}` (See Section 4.3) | Immutable audit log. |

#### Real-Time Socket.IO Events
The frontend connects via `io()` on the same host/port and listens for:
1. `telemetry`: Emitted at 1 Hz with the payload in Section 4.4.
2. `agent_event`: Emitted during AI progression:
   ```json
   {
     "stage": "diagnose", // baseline | chaos_inject | detect | diagnose | validate | approve | recover | replay
     "data": { ... }
   }
   ```
3. `incident_update`: Emitted when system state changes:
   ```json
   {
     "state": "healthy", // healthy | degraded | incident | recovering | approval
     "incident_id": "INC-001",
     "message": "Anomaly detected"
   }
   ```

#### Error Handling Format
On non-2xx errors, the frontend expects a JSON error payload:
```json
{
  "error": "Human readable explanation of why the action failed"
}
```

---

### 4.2 From Member 1: Kishore (AI Diagnostics & Agents — `agents.py`)

#### Schema for `POST /api/analyze`
```json
{
  "incident_id": "INC-20261017-001",
  "root_cause": "Runaway batch job (PID 4821) consuming 94% CPU, creating Db2 lock chain on DSNDB07.SYSLGRNX, cascading to 312 lock waits.",
  "evidence": "CPU 94% for 47s | TPS dropped from 118 to 8 | Db2 locks 312 (threshold 50)",
  "confidence_score": 0.91,
  "risk_level": "medium",
  "blast_radius": "Single runaway process + 5 Db2 transactions; host OS stable.",
  "recovery_steps": [
    { "action": "kill -15 4821", "description": "Gracefully terminate runaway batch job (SIGTERM)." },
    { "action": "SET CURRENT DEGREE = 1", "description": "Reduce Db2 query parallelism to release locks." },
    { "action": "ROLLBACK", "description": "Roll back uncommitted transactions holding locks." }
  ],
  "rollback_conditions": "If CPU does not drop below 40% within 30s, revert to checkpoint.",
  "allowlisted_action_ids": ["kill-15", "db2-rollback", "cics-restart"],
  "is_fallback": false
}
```
*Note: If the Granite API times out or fails, please set `"is_fallback": true` with a deterministic template so the live demo never stalls!*

#### Schema for `POST /api/validate`
```json
{
  "incident_id": "INC-20261017-001",
  "passed": true,
  "result": "pass",
  "details": "Sandbox replay: CPU normalised to 23% within 18s. TPS recovered to 112. Db2 locks cleared."
}
```

---

### 4.3 From Member 3: Ritu (Database & Governance — `database.py`, `governance.py`)

#### Playbooks Schema for `GET /api/playbooks`
```json
{
  "playbooks": [
    {
      "id": "PB-001",
      "name": "CPU Runaway Job",
      "version": 3,
      "trigger_signature": "cpu_spike",
      "status": "APPROVED",
      "validation_result": "pass",
      "approved_by": "operator",
      "match_score": 0.97,
      "steps_count": 4,
      "description": "Terminates runaway process and resets Db2 contention.",
      "created_at": "2026-10-17T10:15:00Z"
    }
  ]
}
```

#### Audit Log Schema for `GET /api/audit`
```json
{
  "events": [
    {
      "ts": "2026-10-17T10:02:10Z",
      "actor": "system",
      "action": "chaos.inject",
      "target": "cpu_spike · PID 4821",
      "risk": "medium",
      "details": "Fault started: cpu_spike, 30s, intensity=high"
    },
    {
      "ts": "2026-10-17T10:03:59Z",
      "actor": "user",
      "action": "playbook.approve",
      "target": "INC-20261017-001",
      "risk": "medium",
      "details": "Human approval recorded by operator"
    }
  ]
}
```

#### Incident History & Recovery Passport Schema for `GET /api/incidents`
```json
{
  "incidents": [
    {
      "id": "INC-20261017-001",
      "fault_type": "cpu_spike",
      "status": "resolved",
      "started_at": "2026-10-17T10:02:10Z",
      "resolved_at": "2026-10-17T10:04:52Z",
      "diagnosis": "Runaway batch job PID 4821 consuming 94% CPU + Db2 lock chain.",
      "validation_result": "pass",
      "approved_by": "operator",
      "recovery_result": "success",
      "playbook_id": "PB-001",
      "recovery_passport_id": "RP-001",
      "baseline": { "cpu": 24.2, "tps": 118, "db2_locks": 6, "ram": 1.9 },
      "during":   { "cpu": 94.0, "tps": 8,   "db2_locks": 312, "ram": 2.8 },
      "after":    { "cpu": 22.5, "tps": 112, "db2_locks": 4,  ram: 1.9 }
    }
  ]
}
```

---

### 4.4 From Member 4: Meer Mohammed Shoaib (Telemetry, Chaos & Workload — `attack.py`, `workload.py`)

#### Telemetry Payload Format (Socket.IO `telemetry` event or `GET /api/telemetry`)
```json
{
  "cpu": 24.5,
  "ram": 1.9,
  "ram_total": 4.0,
  "tps": 114.2,
  "db2_locks": 7,
  "system_state": "healthy",
  "incident_text": "",
  "incident_ts": null,
  "golden_path_stage": "baseline"
}
```
*Field Guidelines:*
- `cpu`: Real host CPU % (`float`, 0.0 to 100.0).
- `ram`: Real host RAM used in GB (`float`).
- `ram_total`: Total host RAM in GB (`float`, e.g., 4.0).
- `tps`: Simulated CICS transactions per second (`float` or `int`, ~110 at baseline, collapsing to <15 during faults).
- `db2_locks`: Simulated Db2 lock contention wait counter (`int`, ~5 at baseline, spiking to >200 during contention).
- `system_state`: String enum: `"healthy"` | `"incident"` | `"degraded"` | `"recovering"` | `"approval"`.
- `golden_path_stage`: String enum: `"baseline"` | `"chaos_inject"` | `"detect"` | `"diagnose"` | `"validate"` | `"approve"` | `"recover"` | `"replay"`.

#### Chaos Injection Parameters (`POST /api/attack`)
The chaos engine will receive:
- `scenario`: `"cpu_spike"` | `"db2_lock_contention"` | `"memory_pressure"`
- `duration`: Integer in seconds (10 to 120)
- `intensity`: String: `"low"` | `"medium"` | `"high"`

---

### 4.5 For Member 6: Rishitha (Integration QA & Demo Video Lead)

#### Verification Protocol & Demo Run Guide
To rehearse the 3-minute Golden Path demo or test the integration:
1. **Baseline Phase (0:00–0:30):** Open Command Center (`/command-center`). Confirm host CPU ~25%, TPS ~114 tx/s, Db2 locks ~6. Connection badge displays `CONNECTED`.
2. **Chaos Phase (0:30–1:00):** Navigate to Chaos Lab (`/chaos-lab`). Select `CPU Runaway Job`, set duration to 30s, click **Start Fault**. Observe CPU spike to ~94%, TPS drop to ~8 tx/s, and Db2 locks spike to >300. Notice the topbar changes to `⚠ INCIDENT ACTIVE`.
3. **Diagnosis & Proposal Phase (1:00–1:40):** Navigate to AI Agents (`/agents`). Click **Diagnose Incident**. Watch Granite reason over telemetry, output root cause hypothesis, confidence score (91%), and structured remediation actions.
4. **Validation Phase (1:40–2:00):** Click **Run Validation**. Validation agent replays the fix in the sandbox and confirms `✓ VALIDATION PASSED`.
5. **Human Approval Gate (2:00–2:20):** Notice the amber approval banner activates. Click **Approve & Enable Recovery**. Confirm operator prompt. Status transitions to `APPROVED`.
6. **Recovery Execution Phase (2:20–2:40):** Click **Execute Recovery**. Allowlisted actions execute. System state transitions back to `HEALTHY`. Telemetry normalizes on Command Center.
7. **Replay Demonstration (2:40–3:00):** Open Playbook Library (`/playbooks`). Locate `PB-001` (match score 97%). Click **Replay**. Observe instant remediation in ~1.8s, fulfilling the core value proposition for judges!
8. **Audit Trail Verification:** Open Audit Log (`/audit`) to confirm 100% of the lifecycle events were recorded immutably.

---

## 5. Verification & Quality Assurance Summary

- **JS Syntax Testing:** All 10 JavaScript files validated using `node -c` with **zero syntax errors**.
- **Security Check:** All dynamic values rendered via `ZP.escapeHtml()`. No `eval()`, no inline event attributes, and no credential exposure in client assets.
- **State Integrity:** Loading spinners, empty cards, stale data detection (warns after 15s without telemetry), and error banners implemented on every single page.
- **Fail-Safe Offline Mode:** If backend services are unreachable, the entire dashboard remains visually intact and interactive in demonstration mode without breaking.

---

*This document is ready to be shared with Kishore, Kiran, Ritu, Meer Mohammed Shoaib, and Rishitha to guide the backend integration sprint.*
