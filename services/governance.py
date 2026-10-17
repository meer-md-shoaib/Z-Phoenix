"""
Z-PHOENIX Governance & Safety Service
Enforces:
1. Strict Action Allowlist (no arbitrary or shell commands).
2. Validation Gates (playbooks must be VALIDATED; parameters must be safe).
3. Human-in-the-Loop Approval (PENDING, APPROVED, REJECTED).
4. Incident-Specific Approval Coupling (approvals cannot be cross-applied).
5. Immutable Audit Logging of all governance decisions and blocked attempts.
6. Safe Replay Prevention (approvals are single-use / consumed upon execution).

CRITICAL SAFETY RULES:
- Governance only returns authorization decisions. It DOES NOT execute shell commands.
- AI-generated text or diagnosis NEVER bypasses the human approval gate.
- High match scores from playbooks DO NOT grant execution permission.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from database import (
        create_approval_request,
        get_approval,
        get_approvals_for_incident,
        get_incident,
        get_pending_approvals,
        get_playbook,
        log_audit_event,
        update_approval_decision,
    )
except ImportError:
    from ..database import (
        create_approval_request,
        get_approval,
        get_approvals_for_incident,
        get_incident,
        get_pending_approvals,
        get_playbook,
        log_audit_event,
        update_approval_decision,
    )


# ============================================================================
# CUSTOM EXCEPTIONS
# ============================================================================

class GovernanceError(Exception):
    """Base exception for governance violations."""
    pass


class ActionNotAllowedError(GovernanceError):
    """Raised when an action is not on the explicit allowlist."""
    pass


class GovernanceValidationError(GovernanceError):
    """Raised when a recovery proposal fails safety or schema validation."""
    pass


class UnauthorizedActorError(GovernanceError):
    """Raised when an unauthorized actor (e.g. AI or System) attempts to approve."""
    pass


class DuplicateApprovalDecisionError(GovernanceError):
    """Raised when attempting to re-decide an already decided approval."""
    pass


# ============================================================================
# SAFETY ALLOWLISTS & PATTERNS
# ============================================================================

# Explicit allowlist of permissible recovery actions (simulated mainframe recovery)
ALLOWED_RECOVERY_ACTIONS: Set[str] = {
    "stop_owned_chaos_worker",
    "reduce_simulated_load",
    "no_action",
    "restart_simulated_cics_region",
    "reset_simulated_db2_pool",
}

# Forbidden automated actors who CANNOT provide human approval
FORBIDDEN_APPROVER_PATTERNS = {
    "SYSTEM",
    "AI",
    "AI_AGENT",
    "AI_DIAGNOSIS",
    "AGENT",
    "AUTOPILOT",
    "CHAOS_ENGINE",
}

# Dangerous shell injection patterns
SHELL_INJECTION_PATTERN = re.compile(
    r"[;&|`$<>\\]|\b(rm|sh|bash|powershell|cmd|sudo|chmod|curl|wget|eval|exec)\b",
    re.IGNORECASE,
)


# ============================================================================
# VALIDATION GATES
# ============================================================================

def is_action_allowlisted(action_id: str) -> bool:
    """Returns True if the action is in the explicit allowlist."""
    if not isinstance(action_id, str):
        return False
    return action_id.strip() in ALLOWED_RECOVERY_ACTIONS


def validate_recovery_proposal(
    action_id: str,
    parameters: Optional[Dict[str, Any]] = None,
    playbook_id: Optional[int] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Validates a recovery action proposal against the allowlist and safety rules.

    Checks:
    1. Action ID is on the explicit allowlist.
    2. Parameters are safe from shell injection and contain valid types.
    3. If a playbook is referenced, it must exist and have validation_status == 'VALIDATED'.
    """
    # 1. Action allowlist check
    if not is_action_allowlisted(action_id):
        return {
            "valid": False,
            "status": "VALIDATION_FAILED",
            "reason": (
                f"Action '{action_id}' is not in the explicit allowlist: "
                f"{sorted(ALLOWED_RECOVERY_ACTIONS)}. Shell commands and unknown actions are strictly rejected."
            ),
        }

    # 2. Check for shell injection in action_id
    if SHELL_INJECTION_PATTERN.search(action_id):
        return {
            "valid": False,
            "status": "VALIDATION_FAILED",
            "reason": f"Dangerous characters or shell commands detected in action_id: '{action_id}'.",
        }

    # 3. Parameter safety checks
    params = parameters or {}
    if not isinstance(params, dict):
        return {
            "valid": False,
            "status": "VALIDATION_FAILED",
            "reason": "Recovery parameters must be a dictionary.",
        }

    for key, value in params.items():
        if isinstance(value, str) and SHELL_INJECTION_PATTERN.search(value):
            return {
                "valid": False,
                "status": "VALIDATION_FAILED",
                "reason": f"Dangerous character or command detected in parameter '{key}': '{value}'.",
            }

    # 4. Playbook validation check (if playbook_id is provided)
    if playbook_id is not None:
        pb = get_playbook(playbook_id, db_path=db_path)
        if not pb:
            return {
                "valid": False,
                "status": "VALIDATION_FAILED",
                "reason": f"Referenced playbook_id={playbook_id} does not exist.",
            }

        if pb.get("action_id") != action_id:
            return {
                "valid": False,
                "status": "VALIDATION_FAILED",
                "reason": (
                    f"Action mismatch: Proposal action is '{action_id}' "
                    f"but playbook_id={playbook_id} specifies action '{pb.get('action_id')}'."
                ),
            }

        pb_status = pb.get("validation_status")
        if pb_status != "VALIDATED":
            return {
                "valid": False,
                "status": "VALIDATION_FAILED",
                "reason": (
                    f"Playbook '{pb.get('name')}' (v{pb.get('version')}) has validation_status='{pb_status}'. "
                    f"Only 'VALIDATED' playbooks are eligible for recovery authorization."
                ),
            }

    return {
        "valid": True,
        "status": "VALIDATED",
        "reason": "Proposal passed action allowlist, parameter safety, and playbook validation checks.",
    }


# ============================================================================
# HUMAN APPROVAL WORKFLOW
# ============================================================================

def request_recovery_approval(
    incident_id: str,
    action_id: str,
    playbook_id: Optional[int] = None,
    parameters: Optional[Dict[str, Any]] = None,
    requested_by: str = "AI_AGENT",
    reason: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Submits a recovery proposal into the human governance gate.
    
    First validates the action against the allowlist and safety criteria.
    If valid, creates a PENDING approval record and logs an audit event.
    """
    if not incident_id or not str(incident_id).strip():
        raise GovernanceValidationError("incident_id must be a non-empty string.")

    # 1. Run validation gate
    val_result = validate_recovery_proposal(
        action_id=action_id,
        parameters=parameters,
        playbook_id=playbook_id,
        db_path=db_path,
    )
    if not val_result["valid"]:
        log_audit_event(
            actor=requested_by,
            action="APPROVAL_REQUEST_REJECTED_BY_VALIDATION",
            incident_id=incident_id,
            details={"action_id": action_id, "reason": val_result["reason"]},
            db_path=db_path,
        )
        raise GovernanceValidationError(val_result["reason"])

    # 2. Verify incident exists
    incident = get_incident(incident_id, db_path=db_path)
    if not incident:
        raise GovernanceValidationError(f"Incident '{incident_id}' does not exist in database.")

    # 3. Create PENDING approval record in database
    approval_id = create_approval_request(
        incident_id=incident_id,
        action_id=action_id,
        playbook_id=playbook_id,
        reason=reason,
        db_path=db_path,
    )

    # 4. Log in immutable audit log
    log_audit_event(
        actor=requested_by,
        action="APPROVAL_REQUESTED",
        incident_id=incident_id,
        details={
            "approval_id": approval_id,
            "action_id": action_id,
            "playbook_id": playbook_id,
            "parameters": parameters or {},
            "reason": reason,
        },
        db_path=db_path,
    )

    return {
        "approval_id": approval_id,
        "incident_id": incident_id,
        "action_id": action_id,
        "playbook_id": playbook_id,
        "decision": "PENDING",
        "status": "AWAITING_HUMAN_APPROVAL",
        "validation_status": "VALIDATED",
        "requested_by": requested_by,
        "reason": reason,
    }


def record_human_approval(
    approval_id: int,
    decision: str,
    human_actor: str,
    reason: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Records a human operator's decision on a pending approval.

    CRITICAL RULES:
    1. decision must be 'APPROVED' or 'REJECTED'.
    2. human_actor must be an authorized human (AI / System / empty is forbidden).
    3. The approval record must exist and must currently be in 'PENDING' state.
    """
    clean_decision = str(decision).strip().upper()
    if clean_decision not in {"APPROVED", "REJECTED"}:
        raise GovernanceValidationError(
            f"Invalid decision '{decision}'. Must be 'APPROVED' or 'REJECTED'."
        )

    # Human Actor Gate: Automated agents CANNOT self-approve
    if not human_actor or not str(human_actor).strip():
        raise UnauthorizedActorError("An authorized human actor must be specified for approval.")

    clean_actor = str(human_actor).strip().upper()
    if clean_actor in FORBIDDEN_APPROVER_PATTERNS or any(p in clean_actor for p in FORBIDDEN_APPROVER_PATTERNS):
        raise UnauthorizedActorError(
            f"Actor '{human_actor}' is an automated or AI agent. "
            f"Only authorized human operators can sign off on recovery actions."
        )

    # Retrieve existing approval
    approval = get_approval(approval_id, db_path=db_path)
    if not approval:
        raise GovernanceValidationError(f"Approval record approval_id={approval_id} does not exist.")

    current_decision = approval.get("decision")
    if current_decision != "PENDING":
        raise DuplicateApprovalDecisionError(
            f"Approval {approval_id} has already been decided with status '{current_decision}'. "
            f"Cannot re-decide an approval."
        )

    # Update in database
    update_approval_decision(
        approval_id=approval_id,
        decision=clean_decision,
        approved_by=str(human_actor).strip(),
        reason=reason,
        db_path=db_path,
    )

    # Audit log
    audit_action = "APPROVAL_GRANTED" if clean_decision == "APPROVED" else "APPROVAL_REJECTED"
    log_audit_event(
        actor=str(human_actor).strip(),
        action=audit_action,
        incident_id=approval["incident_id"],
        details={
            "approval_id": approval_id,
            "decision": clean_decision,
            "action_id": approval["action_id"],
            "reason": reason,
        },
        db_path=db_path,
    )

    return {
        "approval_id": approval_id,
        "incident_id": approval["incident_id"],
        "action_id": approval["action_id"],
        "decision": clean_decision,
        "approved_by": str(human_actor).strip(),
        "reason": reason,
        "status": "DECISION_RECORDED",
    }


# ============================================================================
# EXECUTION PERMISSION VERIFICATION GATE
# ============================================================================

def check_recovery_permission(
    incident_id: str,
    action_id: str,
    approval_id: int,
    playbook_id: Optional[int] = None,
    parameters: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    The definitive safety gate that decides whether a recovery action is AUTHORIZED or BLOCKED.

    Checks:
    1. Action allowlist check.
    2. Parameter safety check (no shell injections).
    3. Incident existence and state check.
    4. Exact Incident Coupling (approval belongs to this incident_id).
    5. Exact Action Coupling (approval belongs to this action_id).
    6. Exact Playbook Coupling (if playbook_id given, approval matches).
    7. Decision is 'APPROVED' (blocks if 'PENDING', 'REJECTED', or 'CONSUMED').
    8. Approver was an authorized human (not AI/SYSTEM).
    9. Playbook validation check (must be 'VALIDATED').

    Returns:
        Dict with keys: 'permitted', 'status', 'reason', 'incident_id', 'action_id', 'approval_id'
    """
    def _block(reason: str) -> Dict[str, Any]:
        # Only set incident_id foreign key if incident exists in DB; otherwise store in details
        audit_inc_id = incident_id if get_incident(incident_id, db_path=db_path) else None
        log_audit_event(
            actor="GOVERNANCE_GATE",
            action="RECOVERY_BLOCKED",
            incident_id=audit_inc_id,
            details={
                "attempted_incident_id": incident_id,
                "approval_id": approval_id,
                "action_id": action_id,
                "playbook_id": playbook_id,
                "blocked_reason": reason,
            },
            db_path=db_path,
        )
        return {
            "permitted": False,
            "status": "BLOCKED",
            "reason": reason,
            "incident_id": incident_id,
            "action_id": action_id,
            "approval_id": approval_id,
        }

    # 1. Action allowlist
    if not is_action_allowlisted(action_id):
        return _block(f"Action '{action_id}' is not allowlisted.")

    # 2. Parameter safety
    val_result = validate_recovery_proposal(action_id, parameters, playbook_id, db_path=db_path)
    if not val_result["valid"]:
        return _block(f"Safety validation failed: {val_result['reason']}")

    # 3. Incident existence
    incident = get_incident(incident_id, db_path=db_path)
    if not incident:
        return _block(f"Incident '{incident_id}' not found.")
    
    if incident.get("status") in {"RESOLVED"}:
        return _block(f"Incident '{incident_id}' is already RESOLVED. No recovery needed.")

    # 4. Approval record existence
    approval = get_approval(approval_id, db_path=db_path)
    if not approval:
        return _block(f"Approval record ID={approval_id} does not exist.")

    # 5. Incident coupling check
    if approval["incident_id"] != incident_id:
        return _block(
            f"Approval mismatch: approval_id={approval_id} was issued for incident "
            f"'{approval['incident_id']}', not '{incident_id}'. Cross-incident authorization blocked."
        )

    # 6. Action coupling check
    if approval["action_id"] != action_id:
        return _block(
            f"Action mismatch: approval_id={approval_id} authorizes action "
            f"'{approval['action_id']}', but execution requested for '{action_id}'. Authorization blocked."
        )

    # 7. Playbook coupling check
    if playbook_id is not None and approval.get("playbook_id") is not None:
        if approval["playbook_id"] != playbook_id:
            return _block(
                f"Playbook mismatch: approval authorizes playbook_id={approval['playbook_id']}, "
                f"not requested playbook_id={playbook_id}."
            )

    # 8. Decision check
    decision = approval.get("decision")
    if decision == "PENDING":
        return _block("Recovery cannot execute: Approval is still PENDING human sign-off.")
    elif decision == "REJECTED":
        return _block(f"Recovery cannot execute: Proposal was REJECTED by human operator ({approval.get('reason')}).")
    elif decision == "CONSUMED":
        return _block("Recovery cannot execute: Approval has already been CONSUMED by a prior execution. Replay blocked.")
    elif decision != "APPROVED":
        return _block(f"Recovery cannot execute: Invalid approval decision state '{decision}'.")

    # 9. Authorized Human Approver check
    approved_by = (approval.get("approved_by") or "").strip().upper()
    if not approved_by or approved_by in FORBIDDEN_APPROVER_PATTERNS or any(p in approved_by for p in FORBIDDEN_APPROVER_PATTERNS):
        return _block(f"Invalid approval: Sign-off was provided by automated actor '{approval.get('approved_by')}'.")

    # 10. Playbook validation check (if playbook is linked)
    if approval.get("playbook_id"):
        pb = get_playbook(approval["playbook_id"], db_path=db_path)
        if pb and pb.get("validation_status") != "VALIDATED":
            return _block(f"Linked playbook '{pb.get('name')}' is {pb.get('validation_status')}, not VALIDATED.")

    # All checks passed!
    log_audit_event(
        actor="GOVERNANCE_GATE",
        action="RECOVERY_AUTHORIZED",
        incident_id=incident_id,
        details={
            "approval_id": approval_id,
            "action_id": action_id,
            "playbook_id": playbook_id,
            "approved_by": approval.get("approved_by"),
        },
        db_path=db_path,
    )

    return {
        "permitted": True,
        "status": "AUTHORIZED",
        "reason": "All governance checks passed: allowlisted action, validated proposal, and human approval verified.",
        "incident_id": incident_id,
        "action_id": action_id,
        "approval_id": approval_id,
        "approved_by": approval.get("approved_by"),
    }


def consume_recovery_approval(
    approval_id: int,
    executed_by: str = "EXECUTION_HANDLER",
    db_path: Optional[Path | str] = None,
) -> bool:
    """
    Marks an approval as CONSUMED once its action has been executed.
    Prevents replay attacks or reusing the same approval twice.
    """
    approval = get_approval(approval_id, db_path=db_path)
    if not approval:
        return False

    success = update_approval_decision(
        approval_id=approval_id,
        decision="CONSUMED",
        approved_by=approval.get("approved_by") or "OPERATOR",
        reason=f"Consumed by {executed_by} upon execution.",
        db_path=db_path,
    )

    if success:
        log_audit_event(
            actor=executed_by,
            action="APPROVAL_CONSUMED",
            incident_id=approval["incident_id"],
            details={"approval_id": approval_id, "action_id": approval["action_id"]},
            db_path=db_path,
        )

    return success
