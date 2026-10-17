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

import agents
import attack
import config
import database
import governance
import workload

database.init_db()

app = Flask(__name__)
app.config["SECRET_KEY"] = "z-phoenix-console"
app.config["JSON_SORT_KEYS"] = False

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
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
        return json.loads(json.dumps(FLOW))


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
    database.resolve_incident(incident_id)
    with _FLOW_LOCK:
        if FLOW["incident_id"] == incident_id:
            FLOW["incident_id"] = None
            FLOW["incident"] = None
        pipeline = FLOW["pipeline"]
        settled = pipeline in ("complete", "replayed")
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
    if not settled:
        # The incident died without a validated recovery (kill switch or
        # 60 s auto-kill). Any staged proposal must not remain approvable,
        # otherwise the gate would authorise a fix for a dead incident.
        pending = governance.get_pending()
        if pending.get("state") == governance.GATE_AWAITING:
            governance.clear_pending()
            governance.log_audit("governance", "GATE_EXPIRED",
                                 f"incident#{incident_id}", "LOW",
                                 {"reason": "incident resolved before approval"})
    socketio.emit("incident_cleared", {"id": incident_id, "settled": settled})
    socketio.emit("stages", _flow_copy())
    _console("ok", f"INCIDENT #{incident_id} RESOLVED - telemetry back inside "
                   f"the recovery envelope.")
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
            str(playbook.get("validation_result", "")).upper() == "PASS":
        _fast_replay(incident_id, playbook)
    else:
        _ai_pipeline(incident_id, sample, fault)


def _fast_replay(incident_id: int, playbook: Dict[str, Any]) -> None:
    with _FLOW_LOCK:
        FLOW["pipeline"] = "replayed"
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
    _set_stage(8, "done", f"Replay complete in {result['elapsed_ms']} ms")
    _console("ok", f"FAST REPLAY COMPLETE in {result['elapsed_ms']} ms "
                   f"({result['steps_executed']} steps, AI bypassed).")


def _ai_pipeline(incident_id: int, sample: Dict[str, Any], fault: str) -> None:
    """Diagnose -> propose -> validate, then stop dead at the human gate."""
    with _FLOW_LOCK:
        FLOW["pipeline"] = "diagnosing"
    _set_stage(4, "running", "IBM Granite analysing telemetry (4 s budget)")

    diagnosis = agents.diagnose(sample, fault)
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

    proposal = agents.recover(diagnosis, sample)
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
        except Exception as exc:  # the stream must never die
            _console("error", f"telemetry loop error: {type(exc).__name__}: {exc}")


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


def _json_body() -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


@app.route("/api/telemetry")
def api_telemetry():
    sample = engine.sample()
    return jsonify({
        "ok": True,
        "telemetry": sample,
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
        "api_key_present": config.api_key_present(),
        "db_path": config.DB_PATH,
    })


@app.route("/api/chaos/inject", methods=["POST"])
def api_chaos_inject():
    body = _json_body()
    fault = str(body.get("fault_type") or "").upper()
    intensity = body.get("intensity", config.CHAOS_DEFAULT_INTENSITY)
    try:
        result = attack.inject(fault, intensity)
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:
        return _error(f"chaos injection failed: {type(exc).__name__}: {exc}", 500)

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
def api_diagnose():
    sample = engine.sample()
    fault = sample.get("fault") or "UNKNOWN"
    with _FLOW_LOCK:
        incident_id = FLOW["incident_id"]
    _set_stage(4, "running", "IBM Granite analysing telemetry (4 s budget)")
    diagnosis = agents.diagnose(sample, fault)
    if incident_id:
        database.update_incident(incident_id, diagnosis=json.dumps(diagnosis))
    with _FLOW_LOCK:
        FLOW["diagnosis"] = diagnosis
    _emit_agent("diagnosis", diagnosis)
    _console("ai", f"[DIAGNOSIS] {diagnosis.get('root_cause')} "
                   f"(source={diagnosis.get('source')})")
    _set_stage(4, "done", f"Root cause identified (source: "
                          f"{diagnosis.get('source')})")
    return jsonify({"ok": True, "diagnosis": diagnosis,
                    "incident_id": incident_id})


@app.route("/api/propose", methods=["POST"])
def api_propose():
    sample = engine.sample()
    fault = sample.get("fault") or "UNKNOWN"
    with _FLOW_LOCK:
        incident_id = FLOW["incident_id"]
        diagnosis = FLOW["diagnosis"] or agents.diagnose(sample, fault)
    proposal = agents.recover(diagnosis, sample)
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
    with _FLOW_LOCK:
        proposal = FLOW["proposal"] or governance.get_pending().get("proposal")
    if not proposal:
        return _error("No proposal to validate - run the Recovery Agent first.", 409)
    validation = agents.validate(proposal)
    with _FLOW_LOCK:
        FLOW["validation"] = validation
    _emit_agent("validation", validation)
    verdict = validation.get("result", "FAIL")
    proposal["validation_result"] = verdict
    governance.update_pending_validation(verdict)
    _console("ok" if verdict == "PASS" else "error",
             f"[VALIDATION] sandbox verdict: {verdict}")
    _set_stage(6, "done", f"Sandbox verdict: {verdict}")
    _set_stage(7, "gate", "AWAITING HUMAN APPROVAL - AI cannot self-execute")
    return jsonify({"ok": True, "validation": validation})


@app.route("/api/approve", methods=["POST"])
def api_approve():
    actor = str(_json_body().get("actor") or "sre.oncall")
    try:
        governance.approve(actor)
        result = governance.execute(actor)
    except governance.ApprovalError as exc:
        _console("error", f"APPROVAL BLOCKED ({exc.status}): {exc}")
        return _error(str(exc), exc.status)
    _set_stage(7, "done", f"Approved by {result.get('approved_by')}")
    _set_stage(8, "done", f"Stored {result['playbook_id']} "
                          f"v{result['playbook_version']} in "
                          f"{result['elapsed_ms']} ms")
    if result.get("incident_id"):
        database.resolve_incident(result["incident_id"])
    with _FLOW_LOCK:
        FLOW["pipeline"] = "complete"
        FLOW["incident_id"] = None
        FLOW["incident"] = None
    socketio.emit("incident_cleared", {"id": result.get("incident_id"),
                                       "settled": True})
    socketio.emit("stages", _flow_copy())
    _console("ok", f"APPROVED + EXECUTED in {result['elapsed_ms']} ms. "
                   f"Chaos cleared, metrics restored, fix stored as "
                   f"{result['playbook_id']} v{result['playbook_version']}.")
    return jsonify(result)


@app.route("/api/reject", methods=["POST"])
def api_reject():
    actor = str(_json_body().get("actor") or "sre.oncall")
    try:
        result = governance.reject(actor)
    except governance.ApprovalError as exc:
        return _error(str(exc), exc.status)
    with _FLOW_LOCK:
        FLOW["pipeline"] = "rejected"
    _set_stage(7, "pending", f"REJECTED by {actor} - proposal discarded, "
                             f"chaos still active")
    _console("warn", f"Proposal REJECTED by {actor}. Nothing was executed; "
                     f"use the emergency kill switch to stand the fault down.")
    return jsonify({"ok": True, "pending": result})


@app.route("/api/execute", methods=["POST"])
def api_execute():
    """Direct execution is always blocked without a recorded human approval."""
    actor = str(_json_body().get("actor") or "sre.oncall")
    try:
        result = governance.execute(actor)
    except governance.ApprovalError as exc:
        _console("error", f"EXECUTION BLOCKED ({exc.status}): {exc}")
        return _error(str(exc), exc.status)
    _set_stage(7, "done", f"Approved by {result.get('approved_by')}")
    _set_stage(8, "done", f"Stored {result['playbook_id']} "
                          f"v{result['playbook_version']}")
    with _FLOW_LOCK:
        FLOW["pipeline"] = "complete"
        FLOW["incident_id"] = None
        FLOW["incident"] = None
    socketio.emit("stages", _flow_copy())
    _console("ok", f"EXECUTED: {result['playbook_id']} v{result['playbook_version']}")
    return jsonify(result)


@app.route("/api/replay/<playbook_ref>", methods=["POST"])
def api_replay(playbook_ref):
    started = time.perf_counter()
    try:
        result = governance.replay(playbook_ref, actor="sre.replay")
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
    return jsonify(result)


@app.route("/api/incidents")
def api_incidents():
    limit = int(request.args.get("limit", 50))
    return jsonify({"ok": True, "incidents": database.list_incidents(limit)})


@app.route("/api/incidents/<int:incident_id>")
def api_incident(incident_id):
    incident = database.get_incident(incident_id)
    if incident is None:
        return _error(f"Incident {incident_id} not found", 404)
    return jsonify({"ok": True, "incident": incident})


@app.route("/api/playbooks")
def api_playbooks():
    playbooks = database.list_playbooks()
    for playbook in playbooks:
        try:
            playbook["steps"] = json.loads(playbook.get("steps_json") or "[]")
        except (ValueError, TypeError):
            playbook["steps"] = []
    return jsonify({"ok": True, "playbooks": playbooks})


@app.route("/api/audit")
def api_audit():
    limit = int(request.args.get("limit", 200))
    return jsonify({"ok": True, "audit": database.list_audit(limit)})


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
