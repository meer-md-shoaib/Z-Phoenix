"""
Z-PHOENIX :: governance.py
=========================
Bounded-autonomy control plane.

  * The AI can propose, but it can NEVER execute. Direct execution without a
    recorded human approval raises ApprovalError (HTTP 403 at the API layer).
  * Every staged command is checked against the shared allowlist/denylist.
  * Every actor action is appended to the immutable audit_log.
  * replay() re-executes a previously validated playbook against the ~2 s
    budget, bypassing all AI inference.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import attack
import config
import database
import workload

# ---------------------------------------------------------------------------
# bounded approval gate state
# ---------------------------------------------------------------------------
GATE_EMPTY = "EMPTY"
GATE_AWAITING = "AWAITING_APPROVAL"
GATE_APPROVED = "APPROVED"
GATE_REJECTED = "REJECTED"
GATE_EXECUTED = "EXECUTED"

_LOCK = threading.Lock()
_PENDING: Dict[str, Any] = {
    "state": GATE_EMPTY,
    "proposal": None,
    "incident_id": None,
    "approved_by": None,
    "decided_by": None,
    "created_at": None,
    "decided_at": None,
}

_AUDIT_EMITTER: Optional[Callable[[Dict[str, Any]], None]] = None


class ApprovalError(Exception):
    """Raised when the human approval gate or the allowlist blocks an action."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


def set_audit_emitter(fn: Callable[[Dict[str, Any]], None]) -> None:
    """Register a live sink (socket push) for new audit rows."""
    global _AUDIT_EMITTER
    _AUDIT_EMITTER = fn


def log_audit(actor: str, action: str, target: str = "", risk_level: str = "LOW",
              details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write an audit row and push it to the live console."""
    entry = database.log_audit(actor, action, target, risk_level, details, emit=False)
    if _AUDIT_EMITTER is not None:
        try:
            _AUDIT_EMITTER(entry)
        except Exception:
            pass
    return entry


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# action allowlist
# ---------------------------------------------------------------------------
def check_steps(steps: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Verify every proposed command against the allowlist/denylist."""
    for step in steps or []:
        command = str(step.get("command") or "")
        allowed, reason = config.command_allowed(command)
        if not allowed:
            return False, f"step {step.get('order', '?')}: {reason}"
    return True, "all commands allowlisted"


# ---------------------------------------------------------------------------
# approval gate
# ---------------------------------------------------------------------------
def get_pending() -> Dict[str, Any]:
    with _LOCK:
        snapshot = dict(_PENDING)
    proposal = snapshot.get("proposal")
    snapshot["steps"] = (proposal or {}).get("steps", [])
    snapshot["risk_tier"] = (proposal or {}).get("risk_tier", "-")
    snapshot["title"] = (proposal or {}).get("title", "-")
    return snapshot


def clear_pending() -> None:
    with _LOCK:
        _PENDING.update({
            "state": GATE_EMPTY,
            "proposal": None,
            "incident_id": None,
            "approved_by": None,
            "decided_by": None,
            "created_at": None,
            "decided_at": None,
        })


def stage_proposal(proposal: Dict[str, Any],
                   incident_id: Optional[int] = None) -> Dict[str, Any]:
    """Present a validated proposal to the human approval gate."""
    ok, reason = check_steps(proposal.get("steps") or [])
    risk = str(proposal.get("risk_tier") or "MEDIUM").upper()
    if not ok:
        log_audit("validation.agent", "PROPOSAL_BLOCKED",
                  f"incident#{incident_id}", "HIGH", {"reason": reason})
        raise ApprovalError(f"Sandbox policy gate rejected the proposal: {reason}",
                            status=400)
    with _LOCK:
        _PENDING.update({
            "state": GATE_AWAITING,
            "proposal": proposal,
            "incident_id": incident_id,
            "approved_by": None,
            "decided_by": None,
            "created_at": _now(),
            "decided_at": None,
        })
    log_audit("recovery.agent", "PROPOSAL_STAGED",
              proposal.get("title", "recovery-proposal"), risk,
              {"incident_id": incident_id,
               "steps": len(proposal.get("steps") or []),
               "risk_tier": risk})
    return get_pending()


def update_pending_validation(verdict: str) -> None:
    """Attach the sandbox verdict to the staged proposal (gate is still open)."""
    with _LOCK:
        if _PENDING["proposal"] is not None:
            _PENDING["proposal"]["validation_result"] = str(verdict or "").upper()


def approve(actor: str = "sre.oncall") -> Dict[str, Any]:
    """Record the human decision. This alone does not execute anything."""
    with _LOCK:
        if _PENDING["state"] != GATE_AWAITING:
            raise ApprovalError(
                f"Nothing awaiting approval (gate state: {_PENDING['state']}).",
                status=409,
            )
        _PENDING.update({
            "state": GATE_APPROVED,
            "approved_by": actor,
            "decided_by": actor,
            "decided_at": _now(),
        })
        proposal = _PENDING["proposal"]
        incident_id = _PENDING["incident_id"]
    log_audit(actor, "HUMAN_GATE_APPROVED",
              (proposal or {}).get("title", "recovery-proposal"),
              str((proposal or {}).get("risk_tier") or "MEDIUM").upper(),
              {"incident_id": incident_id,
               "risk_tier": (proposal or {}).get("risk_tier"),
               "steps": len((proposal or {}).get("steps") or [])})
    return get_pending()


def reject(actor: str = "sre.oncall") -> Dict[str, Any]:
    with _LOCK:
        if _PENDING["state"] != GATE_AWAITING:
            raise ApprovalError(
                f"Nothing awaiting approval (gate state: {_PENDING['state']}).",
                status=409,
            )
        _PENDING.update({
            "state": GATE_REJECTED,
            "decided_by": actor,
            "decided_at": _now(),
        })
        proposal = _PENDING["proposal"]
        incident_id = _PENDING["incident_id"]
    log_audit(actor, "HUMAN_GATE_REJECTED",
              (proposal or {}).get("title", "recovery-proposal"),
              "LOW", {"incident_id": incident_id})
    return get_pending()


# ---------------------------------------------------------------------------
# execution (only reachable through an approved gate)
# ---------------------------------------------------------------------------
def execute(actor: str = "sre.oncall") -> Dict[str, Any]:
    """
    Execute the approved proposal.

    Raises ApprovalError(403) when the gate has not been explicitly approved.
    Clears the chaos fault, restores the metrics and stores the fix as
    playbook v(N+1).
    """
    with _LOCK:
        if _PENDING["state"] != GATE_APPROVED:
            state = _PENDING["state"]
            raise ApprovalError(
                "403 Forbidden: AI-initiated execution is blocked. A recorded "
                "human approval is required at the Human Approval Gate "
                f"(gate state: {state}).",
                status=403,
            )
        proposal = dict(_PENDING["proposal"] or {})
        incident_id = _PENDING["incident_id"]
        approved_by = _PENDING["approved_by"] or actor

    steps = proposal.get("steps") or []
    ok, reason = check_steps(steps)
    if not ok:
        log_audit(actor, "EXECUTION_BLOCKED", proposal.get("title", "-"), "HIGH",
                  {"reason": reason})
        clear_pending()
        raise ApprovalError(f"Allowlist violation blocked execution: {reason}",
                            status=400)

    started = time.perf_counter()
    log_audit(approved_by, "EXECUTION_STARTED",
              proposal.get("title", "recovery-proposal"),
              str(proposal.get("risk_tier") or "MEDIUM").upper(),
              {"incident_id": incident_id, "steps": len(steps)})

    # Simulated command execution against the rehearsal LPAR.
    command_log: List[str] = []
    for step in steps:
        time.sleep(0.08)
        command_log.append(
            f"[exec] {step.get('command')} -> rc=0 ({step.get('title')})"
        )

    # Clearing the chaos fault restores the healthy baseline immediately.
    stop_result = attack.stop(reason="APPROVED_EXECUTION")
    workload.engine.clear_fault()

    fault_type = str(proposal.get("fault_type") or "UNKNOWN").upper()
    validation_result = str(proposal.get("validation_result") or "PASS").upper()
    if validation_result not in ("PASS", "FAIL"):
        validation_result = "PASS"
    stored = database.save_playbook(
        name=proposal.get("title") or f"{fault_type} remediation",
        trigger_signature=fault_type,
        steps=steps,
        validation_result=validation_result,
        approved_by=approved_by,
        status="verified",
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    log_audit("z-phoenix", "PLAYBOOK_STORED",
              f"{stored['id']} v{stored['version']}", "LOW",
              {"trigger": fault_type,
               "validation": validation_result,
               "approved_by": approved_by})
    log_audit(approved_by, "EXECUTION_COMPLETED",
              f"{stored['id']} v{stored['version']}",
              str(proposal.get("risk_tier") or "MEDIUM").upper(),
              {"incident_id": incident_id,
               "elapsed_ms": elapsed_ms,
               "result": "RECOVERED"})

    clear_pending()
    return {
        "ok": True,
        "result": "RECOVERED",
        "incident_id": incident_id,
        "playbook_id": stored["id"],
        "playbook_version": stored["version"],
        "trigger_signature": fault_type,
        "steps_executed": len(steps),
        "command_log": command_log,
        "elapsed_ms": elapsed_ms,
        "approved_by": approved_by,
        "chaos": stop_result,
    }


# ---------------------------------------------------------------------------
# fast replay (~2 s) - bypasses every AI agent
# ---------------------------------------------------------------------------
def replay(playbook_ref: str, actor: str = "replay.engine",
           incident_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Re-execute a verified playbook for its trigger signature within the ~2 s
    budget. No AI inference, no approval delay: the fix was already validated
    and approved in a previous rehearsal.
    """
    playbook = (database.get_playbook(playbook_ref)
                or database.find_playbook_by_trigger(playbook_ref))
    if playbook is None:
        raise ApprovalError(f"Playbook '{playbook_ref}' not found.", status=404)
    if str(playbook.get("status", "")).lower() not in ("verified", "active", "approved"):
        raise ApprovalError(
            f"Playbook {playbook['id']} is not verified (status: "
            f"{playbook.get('status')}); replay denied.",
            status=403,
        )

    try:
        steps = json.loads(playbook.get("steps_json") or "[]")
    except (ValueError, TypeError) as exc:
        raise ApprovalError(
            f"Playbook {playbook['id']} has corrupt steps: {exc}", status=400)

    ok, reason = check_steps(steps)
    if not ok:
        log_audit(actor, "REPLAY_BLOCKED", playbook["id"], "HIGH", {"reason": reason})
        raise ApprovalError(f"Allowlist violation blocked replay: {reason}",
                            status=400)

    budget = config.REPLAY_BUDGET_SEC
    per_step = budget / max(1, len(steps))
    started = time.perf_counter()
    log_audit(actor, "REPLAY_STARTED", f"{playbook['id']} v{playbook['version']}",
              "LOW", {"trigger": playbook["trigger_signature"],
                      "steps": len(steps),
                      "budget_sec": budget,
                      "bypasses_ai": True})

    command_log: List[str] = []
    for step in steps:
        time.sleep(per_step)
        command_log.append(f"[replay] {step.get('command')} -> rc=0")

    stop_result = attack.stop(reason="FAST_REPLAY")
    workload.engine.clear_fault()
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    log_audit(actor, "REPLAY_COMPLETED",
              f"{playbook['id']} v{playbook['version']}", "LOW",
              {"elapsed_ms": elapsed_ms,
               "result": "RECOVERED",
               "trigger": playbook["trigger_signature"]})

    return {
        "ok": True,
        "result": "RECOVERED",
        "mode": "fast-replay",
        "playbook_id": playbook["id"],
        "playbook_version": int(playbook["version"]),
        "trigger_signature": playbook["trigger_signature"],
        "steps_executed": len(steps),
        "command_log": command_log,
        "elapsed_ms": elapsed_ms,
        "incident_id": incident_id,
        "actor": actor,
        "chaos": stop_result,
    }
