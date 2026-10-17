"""
Z-PHOENIX :: app.py
==================
Flask + Flask-SocketIO control plane for the Z-PHOENIX resilience and
recovery rehearsal platform (IBM LinuxONE / s390x, port 8080).

  * page routers ............ /  /chaos  /playbooks  /audit
  * REST API ................ /api/*
  * WebSocket emitter ........ 1 Hz telemetry + stage/incident/audit push
  * detection engine ......... threshold breach -> incident -> agent pipeline

The application is started strictly via:

    socketio.run(app, host='0.0.0.0', port=8080)

so that WebSocket emission and REST handling run concurrently.
"""

from __future__ import annotations

import json
import os
import hmac
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Concurrency layer. eventlet (preferred) gives cooperative green threads so
# the 1 Hz emitter keeps flowing while a Granite call waits on its 4 s budget.
# If eventlet is unavailable we degrade gracefully to native threads.
# Chaos child processes import this module as __mp_main__; they must never
# monkey-patch the stdlib or start the server, so they skip all of this.
# ---------------------------------------------------------------------------
_IS_MP_CHILD = __name__ == "__mp_main__"

ASYNC_MODE = os.environ.get("Z_PHOENIX_ASYNC_MODE", "").strip().lower()
if not ASYNC_MODE:
    if _IS_MP_CHILD:
        ASYNC_MODE = "threading"
    else:
        try:
            import eventlet
            eventlet.monkey_patch()
            ASYNC_MODE = "eventlet"
        except Exception:
            ASYNC_MODE = "threading"

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO
from werkzeug.exceptions import HTTPException

import agents
import attack
import config
import database
import governance
import workload
from services.workflow import WorkflowError, WorkflowService

database.init_db()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("Z_PHOENIX_SECRET_KEY") or secrets.token_urlsafe(32)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
app.config["JSON_SORT_KEYS"] = False

socketio = SocketIO(
    app,
    # Same-origin by default; deployments needing cross-origin access must
    # configure an explicit trusted-origin list.
    cors_allowed_origins=(
        [origin.strip() for origin in os.environ["Z_PHOENIX_ALLOWED_ORIGINS"].split(",") if origin.strip()]
        if os.environ.get("Z_PHOENIX_ALLOWED_ORIGINS") else None
    ),
    async_mode=ASYNC_MODE,
    ping_interval=25,
    ping_timeout=60,
    logger=False,
    engineio_logger=False,
)

engine = workload.engine

# ---------------------------------------------------------------------------
# Golden-path stage machine (shared with the frontend stepper)
# ---------------------------------------------------------------------------
STAGE_DEFS = [
    (1, "Baseline", "1 Hz healthy telemetry stream"),
    (2, "Chaos Injection", "Bounded fault, 60 s auto-kill"),
    (3, "Detection", "Threshold breach -> incident"),
    (4, "Diagnosis", "IBM Granite 3.0 8B (read-only)"),
    (5, "Recovery Proposal", "Scripted remediation + risk tier"),
    (6, "Validation", "Sandbox-only PASS / FAIL"),
    (7, "Human Approval Gate", "Bounded autonomy - AI cannot execute"),
    (8, "Store & Fast Replay", "Playbook v(N+1) + ~2 s replay"),
]

_FLOW_LOCK = threading.Lock()
FLOW: Dict[str, Any] = {
    "stages": [
        {"id": sid, "title": title, "detail": detail,
         "status": "active" if sid == 1 else "pending"}
        for sid, title, detail in STAGE_DEFS
    ],
    "incident_id": None,
    "incident": None,
    "pipeline": "idle",
    "diagnosis": None,
    "proposal": None,
    "validation": None,
    "message": "Baseline healthy - 1 Hz stream active",
    "async_mode": ASYNC_MODE,
}


def _flow_copy() -> Dict[str, Any]:
    with _FLOW_LOCK:
        snapshot = json.loads(json.dumps(FLOW))
    incident_id = snapshot.get("incident_id")
    proposal = snapshot.get("proposal")
    validation = snapshot.get("validation") or {}
    gate_ready = any(stage.get("id") == 7 and stage.get("status") == "gate"
                     for stage in snapshot.get("stages", []))
    if (gate_ready
            and incident_id is not None
            and isinstance(proposal, dict)
            and str(validation.get("result", "")).upper() == "PASS"):
        bound_proposal = dict(proposal)
        bound_proposal["validation_result"] = "PASS"
        snapshot["approval_context"] = {
            "incident_id": incident_id,
            "approval_id": WorkflowService.approval_id(incident_id, bound_proposal),
        }
    else:
        snapshot["approval_context"] = None
    return snapshot


def _set_stage(stage_id: int, status: str, message: Optional[str] = None) -> None:
    with _FLOW_LOCK:
        for stage in FLOW["stages"]:
            if stage["id"] == stage_id:
                stage["status"] = status
        if message:
            FLOW["message"] = message
    socketio.emit("stages", _flow_copy())


def _reset_flow(message: str = "Baseline healthy - 1 Hz stream active") -> None:
    with _FLOW_LOCK:
        for stage in FLOW["stages"]:
            stage["status"] = "active" if stage["id"] == 1 else "pending"
        FLOW.update({
            "incident_id": None,
            "incident": None,
            "pipeline": "idle",
            "diagnosis": None,
            "proposal": None,
            "validation": None,
            "message": message,
        })
    socketio.emit("stages", _flow_copy())


def _console(level: str, message: str, data: Any = None) -> None:
    socketio.emit("console", {
        "level": level,
        "message": message,
        "data": data,
        "ts": time.time(),
    })


def _emit_agent(stage: str, payload: Dict[str, Any]) -> None:
    socketio.emit("agent", {"stage": stage, "payload": payload, "ts": time.time()})


def _governance_audit_sink(entry: Dict[str, Any]) -> None:
    socketio.emit("audit", entry)


governance.set_audit_emitter(_governance_audit_sink)
database.set_audit_sink(_governance_audit_sink)

def _current_plan() -> tuple[Optional[int], Optional[Dict[str, Any]]]:
    with _FLOW_LOCK:
        incident_id = FLOW.get("incident_id")
        proposal = FLOW.get("proposal")
        validation = FLOW.get("validation")
    pending = governance.get_pending()
    if proposal is None:
        proposal = pending.get("proposal")
    if incident_id is None:
        incident_id = pending.get("incident_id")
    if proposal is None:
        return incident_id, None
    # The proposal is changed by validation, so bind the returned verdict too.
    result = dict(proposal)
    if validation:
        result["validation_result"] = validation.get("result", "FAIL")
    return incident_id, result


def _complete_workflow(result: Dict[str, Any]) -> None:
    incident_id = result.get("incident_id")
    if incident_id:
        database.resolve_incident(incident_id)
    with _FLOW_LOCK:
        FLOW["pipeline"] = "complete"
        FLOW["incident_id"] = None
        FLOW["incident"] = None
    socketio.emit("incident_cleared", {"id": incident_id, "settled": True})
    socketio.emit("stages", _flow_copy())


workflow_service = WorkflowService(
    current_plan=_current_plan,
    sample=engine.sample,
    complete=_complete_workflow,
    set_stage=_set_stage,
    logger=app.logger,
)


def _approval_auth_error():
    expected = os.environ.get("Z_PHOENIX_APPROVAL_TOKEN", "")
    supplied = request.headers.get("X-Z-Phoenix-Approval-Token", "")
    if not expected:
        return _error("Recovery approval is disabled: operator authentication is not configured.", 503)
    if not supplied or not hmac.compare_digest(supplied, expected):
        return _error("Valid operator authentication is required.", 401)
    return None


def _actor(body: Dict[str, Any]) -> Optional[str]:
    actor = body.get("actor")
    if not isinstance(actor, str):
        return None
    actor = actor.strip()
    return actor if 1 <= len(actor) <= 120 else None


def _chaos_enabled() -> bool:
    return os.environ.get("Z_PHOENIX_ENABLE_CHAOS", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# detection helpers
# ---------------------------------------------------------------------------
def breach_reasons(sample: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    if sample["cpu"] >= config.THRESH_CPU_CRITICAL:
        reasons.append(f"CPU {sample['cpu']:.1f}% >= {config.THRESH_CPU_CRITICAL:.0f}%")
    if sample["tps"] <= config.THRESH_TPS_CRITICAL:
        reasons.append(f"TPS {sample['tps']} <= {config.THRESH_TPS_CRITICAL:.0f}")
    if sample["locks"] >= config.THRESH_LOCKS_CRITICAL:
        reasons.append(f"Db2 lock waits {sample['locks']} >= "
                       f"{config.THRESH_LOCKS_CRITICAL:.0f}")
    return reasons


def healthy(sample: Dict[str, Any]) -> bool:
    return (sample["cpu"] <= config.RECOVERY_CPU_MAX
            and sample["tps"] >= config.RECOVERY_TPS_MIN
            and sample["locks"] <= config.RECOVERY_LOCKS_MAX)


def _bind_allowlisted_playbook(proposal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Discard model-supplied actions and bind a proposal to a stored plan."""
    fault = str(proposal.get("fault_type") or "").upper()
    if fault not in config.SUPPORTED_FAULTS:
        return None
    playbook = database.find_playbook_by_trigger(fault)
    if (not playbook
            or str(playbook.get("status", "")).lower() not in ("verified", "approved", "active")
            or str(playbook.get("validation_result", "")).upper() != "PASS"
            or not playbook.get("approved_by")):
        return None
    try:
        steps = json.loads(playbook.get("steps_json") or "[]")
    except (TypeError, ValueError):
        return None
    if not isinstance(steps, list) or not steps:
        return None
    allowed, _ = governance.check_steps(steps)
    if not allowed:
        return None
    bound = dict(proposal)
    bound.update({
        "matched_playbook_id": playbook["id"],
        "matched_playbook_version": int(playbook["version"]),
        "steps": steps,
        "source": proposal.get("source", "deterministic-fallback"),
    })
    if bound.get("reason"):
        bound["dependency_status"] = "fallback"
        bound.pop("reason", None)
    return bound


def _sanitize_agent_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Remove provider exception text before storage, audit, or HTTP output."""
    safe = dict(result)
    if safe.pop("reason", None):
        safe["dependency_status"] = "fallback"
    return safe


def _open_incident(sample: Dict[str, Any], reasons: List[str]) -> None:
    fault = sample.get("fault") or "UNKNOWN"
    incident_id = database.insert_incident(fault, sample, "ACTIVE")
    summary = {
        "id": incident_id,
        "fault_type": fault,
        "started_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ"),
        "reasons": reasons,
        "snapshot": sample,
        "severity": "CRITICAL",
    }
    with _FLOW_LOCK:
        FLOW["incident_id"] = incident_id
        FLOW["incident"] = summary
        FLOW["pipeline"] = "detecting"
        FLOW["diagnosis"] = None
        FLOW["proposal"] = None
        FLOW["validation"] = None
        # A new incident must not inherit stale stage states from the last run.
        for stage in FLOW["stages"]:
            if stage["id"] == 4:
                stage["status"] = "active"
            elif stage["id"] >= 5:
                stage["status"] = "pending"
    _set_stage(3, "done", f"INCIDENT #{incident_id}: " + "; ".join(reasons))
    _set_stage(4, "active")
    governance.log_audit("detection.engine", "INCIDENT_OPENED",
                         f"incident#{incident_id}", "HIGH",
                         {"fault": fault, "reasons": reasons, "snapshot": sample})
    socketio.emit("incident", summary)
    _console("critical", f"INCIDENT #{incident_id} OPENED - {fault}: "
                         + "; ".join(reasons))
    socketio.start_background_task(_route_incident, incident_id, sample, fault)


def _resolve_incident(incident_id: int) -> None:
    with _FLOW_LOCK:
        pipeline = FLOW["pipeline"]
        settled = pipeline in ("complete", "replayed")
        if FLOW["incident_id"] == incident_id:
            FLOW["incident_id"] = None
            FLOW["incident"] = None
        if settled:
            FLOW["message"] = "Recovered - validated playbook executed"
        else:
            for stage in FLOW["stages"]:
                stage["status"] = "active" if stage["id"] == 1 else "pending"
            FLOW.update({
                "pipeline": "idle",
                "diagnosis": None,
                "proposal": None,
                "validation": None,
                "message": "Metrics restored to baseline",
            })
    # Only a completed, approved execution can establish successful recovery.
    # A kill switch, timeout, or naturally cleared fault is merely closure.
    if settled:
        database.resolve_incident(incident_id)
    else:
        database.expire_incident(incident_id)
    if not settled:
        # The incident died without a validated recovery (kill switch or
        # 60 s auto-kill). Any staged proposal must not remain approvable,
        # otherwise the gate would authorise a fix for a dead incident.
        pending = governance.get_pending()
        if pending.get("state") == governance.GATE_AWAITING:
            governance.clear_pending()
            governance.log_audit("governance", "GATE_EXPIRED",
                                 f"incident#{incident_id}", "LOW",
                                 {"reason": "incident ended before recovery completed"})
    socketio.emit("incident_cleared", {"id": incident_id, "settled": settled})
    socketio.emit("stages", _flow_copy())
    _console("ok" if settled else "warn",
             f"INCIDENT #{incident_id} {'RESOLVED' if settled else 'EXPIRED'} - "
             "telemetry back inside the recovery envelope.")
    if settled:
        socketio.start_background_task(_delayed_reset, 6)


def _delayed_reset(seconds: int) -> None:
    socketio.sleep(seconds)
    with _FLOW_LOCK:
        busy = FLOW["incident_id"] is not None
    if not busy:
        _reset_flow()


# ---------------------------------------------------------------------------
# incident routing: fast replay for repeat faults, AI pipeline otherwise
# ---------------------------------------------------------------------------
def _route_incident(incident_id: int, sample: Dict[str, Any], fault: str) -> None:
    playbook = database.find_playbook_by_trigger(fault)
    repeat = database.has_resolved_incident(fault)
    if playbook is not None and repeat and \
            str(playbook.get("validation_result", "")).upper() == "PASS" and \
            bool(playbook.get("approved_by")):
        _fast_replay(incident_id, playbook)
    else:
        _ai_pipeline(incident_id, sample, fault)


def _fast_replay(incident_id: int, playbook: Dict[str, Any]) -> None:
    with _FLOW_LOCK:
        FLOW["pipeline"] = "replaying"
    _set_stage(4, "skipped", "Bypassing AI - validated playbook already exists")
    _set_stage(5, "skipped")
    _set_stage(6, "skipped")
    _set_stage(7, "done", "Previously approved by a human operator")
    _set_stage(8, "running", f"FAST REPLAY {playbook['id']} "
                             f"v{playbook['version']} (~2 s)")
    _console("warn", f"FAST REPLAY: bypassing Granite diagnosis, executing "
                     f"{playbook['id']} v{playbook['version']} "
                     f"[{playbook['trigger_signature']}] in ~2 s.")
    try:
        result = governance.replay(playbook["id"], actor="replay.engine",
                                   incident_id=incident_id)
    except governance.ApprovalError as exc:
        _set_stage(8, "error", f"Replay blocked: {exc}")
        _console("error", f"Replay blocked: {exc}")
        with _FLOW_LOCK:
            FLOW["pipeline"] = "rejected"
        return
    with _FLOW_LOCK:
        FLOW["pipeline"] = "replayed"
    _set_stage(8, "done", f"Replay complete in {result['elapsed_ms']} ms")
    _console("ok", f"FAST REPLAY COMPLETE in {result['elapsed_ms']} ms "
                   f"({result['steps_executed']} steps, AI bypassed).")


def _ai_pipeline(incident_id: int, sample: Dict[str, Any], fault: str) -> None:
    """Diagnose -> propose -> validate, then stop dead at the human gate."""
    with _FLOW_LOCK:
        FLOW["pipeline"] = "diagnosing"
    _set_stage(4, "running", "IBM Granite analysing telemetry (4 s budget)")

    diagnosis = _sanitize_agent_result(agents.diagnose(sample, fault))
    governance.log_audit("analysis.api", "ANALYSIS_COMPLETED", str(incident_id or ""), "LOW",
                         {"fault_type": fault, "source": diagnosis.get("source"),
                          "simulated_telemetry": True})
    database.update_incident(incident_id, diagnosis=json.dumps(diagnosis))
    _emit_agent("diagnosis", diagnosis)
    source = diagnosis.get("source", "deterministic-fallback")
    _console("ai", f"[DIAGNOSIS] {diagnosis.get('root_cause')} "
                   f"(confidence {diagnosis.get('confidence')}, source={source})")
    _set_stage(4, "done", f"Root cause identified (source: {source})")

    with _FLOW_LOCK:
        FLOW["pipeline"] = "proposing"
        FLOW["diagnosis"] = diagnosis
    _set_stage(5, "running", "Drafting structured remediation (proposal-only)")

    proposal = _bind_allowlisted_playbook(agents.recover(diagnosis, sample))
    if proposal is None:
        governance.log_audit("recovery.api", "PROPOSAL_BLOCKED", str(incident_id), "HIGH",
                             {"reason": "no approved allowlisted playbook for fault"})
        _set_stage(5, "error", "No approved allowlisted playbook matches this fault")
        with _FLOW_LOCK:
            FLOW["pipeline"] = "rejected"
        return
    proposal["validation_result"] = "PENDING"
    try:
        governance.stage_proposal(proposal, incident_id)
    except governance.ApprovalError as exc:
        _set_stage(5, "error", str(exc))
        _console("error", str(exc))
        with _FLOW_LOCK:
            FLOW["pipeline"] = "rejected"
        return
    _emit_agent("proposal", proposal)
    _console("ai", f"[PROPOSAL] {proposal.get('title')} "
                   f"(risk tier {proposal.get('risk_tier')}, "
                   f"{len(proposal.get('steps') or [])} steps)")
    _set_stage(5, "done", f"Proposal staged (risk tier {proposal.get('risk_tier')})")

    with _FLOW_LOCK:
        FLOW["pipeline"] = "validating"
        FLOW["proposal"] = proposal
    _set_stage(6, "running", "Simulating inside sandbox ZPHX-SBX-01")

    validation = agents.validate(proposal)
    _emit_agent("validation", validation)
    verdict = validation.get("result", "FAIL")
    # The stored playbook must carry the real sandbox verdict so that a later
    # re-injection of this fault qualifies for the AI-bypassing fast replay.
    proposal["validation_result"] = verdict
    governance.update_pending_validation(verdict)
    _console("ok" if verdict == "PASS" else "error",
             f"[VALIDATION] sandbox verdict: {verdict} "
             f"({validation.get('commands_checked')} commands checked)")
    _set_stage(6, "done", f"Sandbox verdict: {verdict}")
    with _FLOW_LOCK:
        FLOW["validation"] = validation
        FLOW["pipeline"] = "awaiting_gate"
    _set_stage(7, "gate", "AWAITING HUMAN APPROVAL - AI cannot self-execute")
    _console("warn", "HUMAN APPROVAL GATE: execution requires an explicit "
                     "operator decision. Direct execution returns HTTP 403.")


# ---------------------------------------------------------------------------
# 1 Hz background emitter + detection engine
# ---------------------------------------------------------------------------
def _telemetry_loop() -> None:
    breach_ticks = 0
    healthy_ticks = 0
    while True:
        socketio.sleep(config.SAMPLE_INTERVAL_SEC)
        try:
            sample = engine.sample()
            reasons = breach_reasons(sample)
            with _FLOW_LOCK:
                incident_id = FLOW["incident_id"]

            if reasons:
                healthy_ticks = 0
                breach_ticks += 1
                if incident_id is None and breach_ticks >= config.DEBOUNCE_TICKS:
                    breach_ticks = 0
                    _open_incident(sample, reasons)
            else:
                breach_ticks = 0
                if incident_id is not None:
                    healthy_ticks += 1
                    if healthy_ticks >= config.DEBOUNCE_TICKS:
                        healthy_ticks = 0
                        _resolve_incident(incident_id)
                else:
                    healthy_ticks = 0

            socketio.emit("telemetry", {
                "ts": time.time(),
                "telemetry": sample,
                "incident": _flow_copy()["incident"],
                "chaos": attack.status(),
                "stages": _flow_copy(),
            })
        except Exception as exc:  # the stream must never die or echo provider data
            app.logger.error("telemetry loop failed (%s)", type(exc).__name__)
            _console("error", "Telemetry sampling failed; the stream will retry.")


def _start_background_tasks() -> None:
    if _IS_MP_CHILD:
        return
    socketio.start_background_task(_telemetry_loop)


_start_background_tasks()


# ---------------------------------------------------------------------------
# page routers
# ---------------------------------------------------------------------------
@app.route("/")
def page_command_center():
    return render_template("command_center.html")


@app.route("/chaos")
def page_chaos_lab():
    return render_template("chaos_lab.html")


@app.route("/playbooks")
def page_playbooks():
    return render_template("playbooks.html")


@app.route("/audit")
def page_audit():
    return render_template("audit.html")


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
def _error(message: str, status: int):
    return jsonify({"ok": False, "error": message, "status": status}), status


@app.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return _error(exc.description, exc.code or 400)


@app.errorhandler(Exception)
def _unexpected_error(exc: Exception):
    app.logger.error("request failed (%s)", type(exc).__name__)
    return _error("Request could not be completed.", 500)


def _json_body() -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    if payload is None and not request.data:
        return {}
    if not isinstance(payload, dict):
        from werkzeug.exceptions import BadRequest
        raise BadRequest("Request body must be a JSON object.")
    return payload


@app.route("/api/health")
def api_health():
    try:
        with database.connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        app.logger.error("database readiness check failed (%s)", type(exc).__name__)
        return jsonify({"ok": False, "status": "not_ready",
                        "dependencies": {"database": "unavailable"}}), 503
    return jsonify({"ok": True, "status": "ready", "app": config.APP_NAME,
                    "dependencies": {"database": "ready"},
                    "chaos_enabled": _chaos_enabled()})


@app.route("/api/telemetry")
def api_telemetry():
    sample = engine.sample()
    return jsonify({
        "ok": True,
        "telemetry": sample,
        "telemetry_source": {
            "host_cpu_ram": "host-observed" if sample.get("real_cpu") is not None or sample.get("real_ram_gb") is not None else "simulated-fallback",
            "cics_tps": "simulated",
            "db2_locks": "simulated",
            "fault_metrics": "simulated" if sample.get("chaos_active") else "baseline-simulation",
        },
        "chaos": attack.status(),
        "stages": _flow_copy(),
        "incident": _flow_copy()["incident"],
        "pending": governance.get_pending(),
    })


@app.route("/api/flow")
def api_flow():
    return jsonify({
        "ok": True,
        "stages": _flow_copy(),
        "pending": governance.get_pending(),
        "chaos": attack.status(),
    })


@app.route("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "app": config.APP_NAME,
        "port": config.PORT,
        "async_mode": ASYNC_MODE,
        "model": config.GRANITE_MODEL,
        "granite_timeout_sec": config.GRANITE_TIMEOUT,
        "ai_provider_configured": config.api_key_present(),
        "chaos_enabled": _chaos_enabled(),
    })


@app.route("/api/attack", methods=["POST"])
def api_attack():
    if not _chaos_enabled():
        return _error("Chaos simulation is disabled by default; enable it explicitly in server configuration.", 403)
    auth_error = _approval_auth_error()
    if auth_error:
        return auth_error
    body = _json_body()
    scenario = body.get("scenario", body.get("fault_type"))
    if not isinstance(scenario, str) or scenario.upper().strip() not in config.SUPPORTED_FAULTS:
        return _error("scenario must identify a supported bounded simulation.", 400)
    intensity = body.get("intensity", config.CHAOS_DEFAULT_INTENSITY)
    if isinstance(intensity, bool) or not isinstance(intensity, (int, float)) or not config.CHAOS_MIN_INTENSITY <= intensity <= config.CHAOS_MAX_INTENSITY:
        return _error("intensity is outside the configured safety limits.", 400)
    try:
        result = attack.inject(scenario.upper().strip(), intensity)
    except ValueError:
        return _error("Unsupported chaos scenario.", 400)
    except Exception as exc:
        app.logger.error("chaos simulation failed (%s)", type(exc).__name__)
        return _error("Chaos simulation could not be started.", 503)
    governance.log_audit("chaos.api", "SIMULATION_STARTED", scenario.upper().strip(), "MEDIUM",
                         {"intensity": result.get("intensity"), "simulation_only": True})
    return jsonify({"ok": True, "simulation_only": True, "chaos": result}), 202


@app.route("/api/chaos/inject", methods=["POST"])
def api_chaos_inject():
    if not _chaos_enabled():
        return _error("Chaos simulation is disabled by default; enable it explicitly in server configuration.", 403)
    auth_error = _approval_auth_error()
    if auth_error:
        return auth_error
    body = _json_body()
    fault = str(body.get("fault_type") or "").upper()
    intensity = body.get("intensity", config.CHAOS_DEFAULT_INTENSITY)
    try:
        result = attack.inject(fault, intensity)
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:
        app.logger.error("legacy chaos injection failed (%s)", type(exc).__name__)
        return _error("Chaos simulation could not be started.", 503)

    governance.log_audit("chaos.lab", "CHAOS_INJECT", fault, "MEDIUM",
                         {"intensity": result.get("intensity"),
                          "workers": result.get("workers_alive")})
    with _FLOW_LOCK:
        FLOW["pipeline"] = "chaos"
    _set_stage(2, "done", f"{fault} injected at {result.get('intensity')}% intensity")
    _set_stage(3, "active", "Watching for threshold breach")
    _console("warn", result.get("message", f"{fault} injected."))
    return jsonify({"ok": True, "chaos": result})


@app.route("/api/chaos/stop", methods=["POST"])
def api_chaos_stop():
    body = _json_body()
    reason = str(body.get("reason") or "EMERGENCY_KILL_SWITCH")
    result = attack.stop(reason=reason)
    governance.log_audit("chaos.lab", "EMERGENCY_KILL_SWITCH", "all-faults", "HIGH",
                         {"reason": reason})
    _console("critical", result.get("message", "Emergency kill switch engaged."))
    return jsonify({"ok": True, "chaos": result})


@app.route("/api/diagnose", methods=["POST"])
@app.route("/api/analyze", methods=["POST"])
def api_diagnose():
    _json_body()
    sample = engine.sample()
    fault = sample.get("fault") or "UNKNOWN"
    with _FLOW_LOCK:
        incident_id = FLOW["incident_id"]
    _set_stage(4, "running", "IBM Granite analysing telemetry (4 s budget)")
    diagnosis = _sanitize_agent_result(agents.diagnose(sample, fault))
    if incident_id:
        database.update_incident(incident_id, diagnosis=json.dumps(diagnosis))
    with _FLOW_LOCK:
        FLOW["diagnosis"] = diagnosis
        if diagnosis.get("fault_type") == "NONE" or diagnosis.get("risk") == "LOW":
            FLOW["proposal"] = None
            FLOW["validation"] = None
    if diagnosis.get("fault_type") == "NONE" or diagnosis.get("risk") == "LOW":
        governance.clear_pending()
    _emit_agent("diagnosis", diagnosis)
    _console("ok" if diagnosis.get("risk") == "LOW" else "ai",
             f"[DIAGNOSIS] {diagnosis.get('root_cause')} "
             f"(source={diagnosis.get('source')})")
    _set_stage(4, "done", f"Diagnosis complete (source: "
                          f"{diagnosis.get('source')})")
    return jsonify({"ok": True, "diagnosis": diagnosis,
                    "incident_id": incident_id})


@app.route("/api/propose", methods=["POST"])
def api_propose():
    _json_body()
    sample = engine.sample()
    with _FLOW_LOCK:
        incident_id = FLOW["incident_id"]
    if incident_id is None and sample.get("health") == "HEALTHY" and not sample.get("chaos_active"):
        return _error("System is healthy with no active incident; recovery proposal not permitted.", 400)
    fault = sample.get("fault") or "UNKNOWN"
    with _FLOW_LOCK:
        diagnosis = FLOW["diagnosis"] or agents.diagnose(sample, fault)
    if diagnosis.get("fault_type") == "NONE" or str(diagnosis.get("risk", "")).upper() == "LOW":
        return _error("Diagnosis indicates healthy baseline; no recovery proposal required.", 400)
    proposal = _bind_allowlisted_playbook(agents.recover(diagnosis, sample))
    if proposal is None:
        governance.log_audit("recovery.api", "PROPOSAL_BLOCKED", str(incident_id or ""), "HIGH",
                             {"reason": "no approved allowlisted playbook for fault"})
        return _error("No approved allowlisted playbook matches this incident.", 409)
    try:
        governance.stage_proposal(proposal, incident_id)
    except governance.ApprovalError as exc:
        return _error(str(exc), exc.status)
    with _FLOW_LOCK:
        FLOW["proposal"] = proposal
    _emit_agent("proposal", proposal)
    _console("ai", f"[PROPOSAL] {proposal.get('title')} "
                   f"(risk tier {proposal.get('risk_tier')})")
    _set_stage(5, "done", f"Proposal staged (risk tier {proposal.get('risk_tier')})")
    _set_stage(6, "active")
    return jsonify({"ok": True, "proposal": proposal,
                    "pending": governance.get_pending()})


@app.route("/api/validate", methods=["POST"])
def api_validate():
    body = _json_body()
    with _FLOW_LOCK:
        proposal = FLOW["proposal"] or governance.get_pending().get("proposal")
    if not proposal:
        return _error("No proposal to validate - run the Recovery Agent first.", 409)
    requested_incident = body.get("incident_id")
    incident_id, _ = _current_plan()
    if requested_incident is not None and requested_incident != incident_id:
        return _error("Validation incident does not match the pending plan.", 409)
    validation = agents.validate(proposal)
    validation = _sanitize_agent_result(validation)
    verdict = str(validation.get("result", "FAIL")).upper()
    if verdict not in ("PASS", "FAIL"):
        verdict = "FAIL"
        validation["result"] = verdict
    with _FLOW_LOCK:
        FLOW["validation"] = validation
    _emit_agent("validation", validation)
    proposal["validation_result"] = verdict
    governance.update_pending_validation(verdict)
    governance.log_audit("validation.api", "PLAN_VALIDATED", str(_current_plan()[0] or ""),
                         "LOW" if verdict == "PASS" else "HIGH",
                         {"result": verdict, "commands_checked": validation.get("commands_checked", 0)})
    _console("ok" if verdict == "PASS" else "error",
             f"[VALIDATION] sandbox verdict: {verdict}")
    _set_stage(6, "done", f"Sandbox verdict: {verdict}")
    approval_id = None
    if verdict == "PASS":
        incident_id, bound_proposal = _current_plan()
        approval_id = workflow_service.approval_id(incident_id, bound_proposal or {})
        _set_stage(7, "gate", "AWAITING AUTHENTICATED HUMAN APPROVAL - recovery remains separate")
    else:
        _set_stage(7, "blocked", "Validation failed; approval and recovery are disabled")
    return jsonify({"ok": verdict == "PASS", "validation": validation,
                    "approval_id": approval_id, "incident_id": _current_plan()[0]}), (200 if verdict == "PASS" else 422)


@app.route("/api/approve", methods=["POST"])
def api_approve():
    try:
        result = workflow_service.approve(
            _json_body(), request.headers.get("X-Z-Phoenix-Approval-Token", ""))
    except WorkflowError as exc:
        return _error(str(exc), exc.status)
    return jsonify(result), 200


@app.route("/api/recover", methods=["POST"])
def api_recover():
    try:
        result = workflow_service.recover(
            _json_body(), request.headers.get("X-Z-Phoenix-Approval-Token", ""))
    except WorkflowError as exc:
        return _error(str(exc), exc.status)
    return jsonify(result), 200


@app.route("/api/reject", methods=["POST"])
def api_reject():
    auth_error = _approval_auth_error()
    if auth_error:
        return auth_error
    actor = _actor(_json_body())
    if actor is None:
        return _error("actor is required and must be 1 to 120 characters.", 400)
    try:
        result = governance.reject(actor)
    except governance.ApprovalError as exc:
        return _error(str(exc), exc.status)
    workflow_service.clear_approval()
    with _FLOW_LOCK:
        FLOW["pipeline"] = "rejected"
    _set_stage(7, "pending", f"REJECTED by {actor} - proposal discarded, "
                             f"chaos still active")
    _console("warn", f"Proposal REJECTED by {actor}. Nothing was executed; "
                     f"use the emergency kill switch to stand the fault down.")
    return jsonify({"ok": True, "pending": result})


@app.route("/api/execute", methods=["POST"])
def api_execute():
    """Compatibility alias for the separately authorized recovery action."""
    return api_recover()


@app.route("/api/replay/<playbook_ref>", methods=["POST"])
def api_replay(playbook_ref):
    auth_error = _approval_auth_error()
    if auth_error:
        return auth_error
    body = _json_body()
    actor = _actor(body)
    if actor is None:
        return _error("actor is required and must be 1 to 120 characters.", 400)
    stored_playbook = (database.get_playbook(playbook_ref)
                       or database.find_playbook_by_trigger(playbook_ref))
    if (stored_playbook is None
            or str(stored_playbook.get("validation_result", "")).upper() != "PASS"
            or not stored_playbook.get("approved_by")):
        return _error("Legacy replay requires a previously approved, validated playbook.", 403)
    started = time.perf_counter()
    try:
        result = governance.replay(playbook_ref, actor=actor)
    except governance.ApprovalError as exc:
        return _error(str(exc), exc.status)
    with _FLOW_LOCK:
        had_incident = FLOW["incident_id"] is not None
        if FLOW["incident_id"] == result.get("incident_id"):
            FLOW["incident_id"] = None
            FLOW["incident"] = None
        FLOW["pipeline"] = "replayed"
    if result.get("incident_id"):
        database.resolve_incident(result["incident_id"])
        socketio.emit("incident_cleared", {"id": result["incident_id"],
                                           "settled": True})
    _set_stage(7, "done", "Previously approved playbook")
    _set_stage(8, "done", f"Replay {playbook_ref} complete in "
                          f"{result['elapsed_ms']} ms")
    _console("ok", f"1-CLICK REPLAY {result['playbook_id']} "
                   f"v{result['playbook_version']} completed in "
                   f"{result['elapsed_ms']} ms (AI bypassed).")
    result["api_elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    result["incident_closed"] = had_incident
    result["simulation_only"] = True
    result["mode"] = "bounded-simulation"
    result["steps_simulated"] = result.pop("steps_executed", 0)
    result.pop("command_log", None)
    return jsonify(result)


@app.route("/api/replay", methods=["POST"])
def api_replay_safe():
    body = _json_body()
    if set(body) - {"playbook_id", "mode"}:
        return _error("Replay accepts only playbook_id and mode.", 400)
    if body.get("mode", "safe") != "safe":
        return _error("Only safe, side-effect-free replay mode is supported by this endpoint.", 400)
    playbook_id = body.get("playbook_id")
    if not isinstance(playbook_id, str) or not 1 <= len(playbook_id) <= 32:
        return _error("playbook_id is required.", 400)
    playbook = database.get_playbook(playbook_id)
    if not playbook:
        return _error("Playbook was not found.", 404)
    if (str(playbook.get("status", "")).lower() not in ("verified", "approved", "active")
            or str(playbook.get("validation_result", "")).upper() != "PASS"
            or not playbook.get("approved_by")):
        return _error("Replay requires a previously approved, validated playbook.", 403)
    try:
        steps = json.loads(playbook.get("steps_json") or "[]")
    except (TypeError, ValueError):
        return _error("Playbook contains invalid step data.", 422)
    if not isinstance(steps, list) or not steps:
        return _error("Playbook has no replayable steps.", 422)
    allowed, reason = governance.check_steps(steps)
    if not allowed:
        governance.log_audit("replay.api", "REPLAY_BLOCKED", playbook_id, "HIGH", {"reason": reason})
        return _error("Playbook failed the action allowlist check.", 422)
    result = {
        "ok": True, "mode": "safe-simulation", "simulation_only": True,
        "playbook_id": playbook["id"], "playbook_version": playbook["version"],
        "trigger_signature": playbook["trigger_signature"],
        "steps_checked": len(steps), "commands_executed": 0,
        "events": [{"order": step.get("order", index + 1),
                    "action_id": f"{playbook['id']}:{step.get('order', index + 1)}",
                    "result": "would_apply_in_simulation"}
                   for index, step in enumerate(steps)],
    }
    governance.log_audit("replay.api", "SAFE_REPLAY_COMPLETED", playbook_id, "LOW",
                         {"steps_checked": len(steps), "side_effects": False})
    return jsonify(result)


@app.route("/api/incidents")
def api_incidents():
    limit = _query_limit("limit", 50, 200)
    if isinstance(limit, tuple):
        return limit
    return jsonify({"ok": True, "incidents": database.list_incidents(limit)})


@app.route("/api/incidents/<int:incident_id>")
def api_incident(incident_id):
    incident = database.get_incident(incident_id)
    if incident is None:
        return _error(f"Incident {incident_id} not found", 404)
    return jsonify({"ok": True, "incident": incident})


@app.route("/api/playbooks")
def api_playbooks():
    playbooks = []
    for candidate in database.list_playbooks():
        if (str(candidate.get("status", "")).lower() not in ("verified", "approved", "active")
                or str(candidate.get("validation_result", "")).upper() != "PASS"
                or not candidate.get("approved_by")):
            continue
        try:
            steps = json.loads(candidate.get("steps_json") or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(steps, list) or not steps:
            continue
        allowed, _ = governance.check_steps(steps)
        if not allowed:
            continue
        candidate["steps"] = steps
        playbooks.append(candidate)
    for playbook in playbooks:
        playbook["action_ids"] = [
            f"{playbook.get('id')}:{step.get('order', index + 1)}"
            for index, step in enumerate(playbook["steps"])
        ]
        playbook["simulation_only"] = True
        playbook["match_metadata"] = {
            "trigger_signature": playbook.get("trigger_signature"),
            "validated": True,
            "approved_by": playbook.get("approved_by"),
            "safe_replay_available": True,
        }
    return jsonify({"ok": True, "playbooks": playbooks})


def _query_limit(name: str, default: int, maximum: int):
    try:
        limit = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return _error(f"{name} must be an integer.", 400)
    if limit < 1 or limit > maximum:
        return _error(f"{name} must be between 1 and {maximum}.", 400)
    return limit


def _audit_details(entry: Dict[str, Any]) -> Dict[str, Any]:
    try:
        details = json.loads(entry.get("details_json") or "{}")
    except (TypeError, ValueError):
        details = {}
    if not isinstance(details, dict):
        details = {}
    # Responses contain only the event fields used by the console/API.
    return {"id": entry.get("id"), "ts": entry.get("ts"),
            "actor": entry.get("actor"), "action": entry.get("action"),
            "target": entry.get("target"), "risk_level": entry.get("risk_level"),
            "details": {key: value for key, value in details.items()
                        if not any(part in key.lower() for part in ("secret", "token", "password", "credential", "api_key"))}}


def _event_incident_id(entry: Dict[str, Any]) -> Optional[int]:
    details = _audit_details(entry).get("details", {})
    try:
        return int(details.get("incident_id")) if details.get("incident_id") is not None else None
    except (TypeError, ValueError):
        return None


@app.route("/api/recovery_passport/<int:passport_id>")
def api_recovery_passport(passport_id: int):
    incident = database.get_incident(passport_id)
    if incident is None:
        return _error("Recovery Passport was not found.", 404)
    try:
        telemetry = json.loads(incident.get("telemetry_snapshot") or "{}")
    except (TypeError, ValueError):
        telemetry = {}
    try:
        analysis = json.loads(incident.get("diagnosis") or "null")
    except (TypeError, ValueError):
        analysis = None
    audit_rows = [_audit_details(row) for row in database.list_audit(500)
                  if _event_incident_id(row) == passport_id]
    snapshot_event = next((row for row in audit_rows
                           if row["action"] == "RECOVERY_SNAPSHOTS_CAPTURED"), None)
    approved = next((row for row in audit_rows if row["action"] == "HUMAN_GATE_APPROVED"), None)
    outcome = (
        "recovered"
        if str(incident.get("status", "")).upper() == "RESOLVED"
        and database.has_successful_recovery(passport_id)
        else "pending_or_unresolved"
    )
    playbook = database.find_playbook_by_trigger(incident.get("fault_type", ""))
    return jsonify({"ok": True, "passport": {
        "id": str(passport_id), "incident_id": passport_id,
        "evidence": {"telemetry_snapshot": telemetry,
                     "telemetry_source": "host-observed CPU/RAM where available; CICS/Db2 simulated"},
        "analysis": analysis,
        "matched_playbook": ({"id": playbook.get("id"), "version": playbook.get("version"),
                              "trigger_signature": playbook.get("trigger_signature")}
                             if playbook else None),
        "validation_and_approval": {"approval_event": approved,
                                    "approval_time": approved.get("ts") if approved else None},
        "outcome": outcome,
        "snapshots": {
            "before": ((snapshot_event or {}).get("details", {}).get("before") or telemetry),
            "after": (snapshot_event or {}).get("details", {}).get("after"),
            "after_status": "captured_in_audit" if snapshot_event else "unavailable",
        },
        "audit_links": [row["id"] for row in audit_rows],
        "audit": audit_rows,
    }})


@app.route("/api/audit")
def api_audit():
    limit = _query_limit("limit", 200, 500)
    if isinstance(limit, tuple):
        return limit
    incident_arg = request.args.get("incident_id")
    if incident_arg is not None:
        try:
            incident_id = int(incident_arg)
            if incident_id < 1:
                raise ValueError
        except ValueError:
            return _error("incident_id must be a positive integer.", 400)
    else:
        incident_id = None
    entries = database.list_audit(limit)
    if incident_id is not None:
        entries = [entry for entry in entries if _event_incident_id(entry) == incident_id]
    return jsonify({"ok": True, "audit": [_audit_details(entry) for entry in entries]})


@app.route("/api/pending")
def api_pending():
    return jsonify({"ok": True, "pending": governance.get_pending()})


# ---------------------------------------------------------------------------
# entrypoint - run strictly via socketio.run(app, host='0.0.0.0', port=8080)
# ---------------------------------------------------------------------------
def _banner() -> str:
    return (
        f"\n  {config.APP_NAME} :: {config.APP_TAGLINE}\n"
        f"  {config.APP_SUBTITLE}\n"
        f"  listening on http://0.0.0.0:{config.PORT} "
        f"(async_mode={ASYNC_MODE}, model={config.GRANITE_MODEL}, "
        f"granite_timeout={config.GRANITE_TIMEOUT}s, "
        f"api_key={'yes' if config.api_key_present() else 'fallback-mode'})\n"
    )


if __name__ == "__main__":
    print(_banner())
    socketio.run(app, host='0.0.0.0', port=8080)
