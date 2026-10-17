"""
Automated Tests for Governance Service (services/governance.py)
Tests:
1. Approved recovery workflow end-to-end.
2. Missing / Pending approval blocking.
3. Rejected approval blocking.
4. Action allowlist enforcement (rejecting unknown actions and shell commands).
5. Parameter safety validation (rejecting shell injections).
6. Cross-incident approval mismatch rejection.
7. Cross-action approval mismatch rejection.
8. Preventing unauthorized AI/System actors from self-approving.
9. Duplicate decision protection (preventing re-deciding an approval).
10. Safe replay prevention (consumed approval cannot be reused).
11. Unvalidated / Deprecated playbook blocking.
12. Immutable audit logging of all governance gates.
"""

import pytest
from database import create_incident, create_playbook, get_audit_logs, init_db
from services.governance import (
    ALLOWED_RECOVERY_ACTIONS,
    DuplicateApprovalDecisionError,
    GovernanceValidationError,
    UnauthorizedActorError,
    check_recovery_permission,
    consume_recovery_approval,
    is_action_allowlisted,
    record_human_approval,
    request_recovery_approval,
    validate_recovery_proposal,
)


@pytest.fixture
def test_db(tmp_path):
    """Provides a fresh isolated database for each test."""
    db_file = tmp_path / "test_governance.db"
    init_db(db_file)
    return db_file


@pytest.fixture
def active_incident(test_db):
    """Helper fixture to insert an active incident for testing."""
    inc_id = "INC-TEST-001"
    create_incident(
        incident_id=inc_id,
        fault_type="cpu_burn",
        status="DETECTED",
        db_path=test_db,
    )
    return inc_id


# ============================================================================
# 1. ALLOWLIST & VALIDATION TESTS
# ============================================================================

def test_action_allowlist():
    """Verifies that only known simulated actions are allowlisted."""
    assert is_action_allowlisted("stop_owned_chaos_worker") is True
    assert is_action_allowlisted("reduce_simulated_load") is True
    assert is_action_allowlisted("no_action") is True

    # Dangerous commands must be rejected
    assert is_action_allowlisted("rm -rf /") is False
    assert is_action_allowlisted("bash") is False
    assert is_action_allowlisted("drop_table") is False
    assert is_action_allowlisted("") is False


def test_validate_recovery_proposal_dangerous_shell(test_db):
    """Rejects proposals attempting to inject shell commands via action or parameters."""
    # Unknown/dangerous action
    res1 = validate_recovery_proposal("cat /etc/passwd", db_path=test_db)
    assert res1["valid"] is False
    assert "not in the explicit allowlist" in res1["reason"]

    # Parameter with shell injection
    res2 = validate_recovery_proposal(
        action_id="reduce_simulated_load",
        parameters={"script": "stop_worker; rm -rf /tmp"},
        db_path=test_db,
    )
    assert res2["valid"] is False
    assert "Dangerous character or command detected" in res2["reason"]


def test_validate_recovery_proposal_unvalidated_playbook(test_db):
    """Rejects proposals referencing unvalidated or deprecated playbooks."""
    pb_id = create_playbook(
        name="Experimental Action",
        version=1,
        action_id="reduce_simulated_load",
        context={"subsystem": "CICS"},
        validation_status="PENDING",  # Not validated!
        db_path=test_db,
    )

    res = validate_recovery_proposal(
        action_id="reduce_simulated_load",
        playbook_id=pb_id,
        db_path=test_db,
    )
    assert res["valid"] is False
    assert "has validation_status='PENDING'" in res["reason"]


# ============================================================================
# 2. END-TO-END APPROVED RECOVERY WORKFLOW
# ============================================================================

def test_approved_workflow_end_to_end(test_db, active_incident):
    """Tests the full path from proposal request to human sign-off to execution permission."""
    pb_id = create_playbook(
        name="CICS CPU Throttling",
        version=1,
        action_id="reduce_simulated_load",
        context={"subsystem": "CICS"},
        validation_status="VALIDATED",
        db_path=test_db,
    )

    # 1. Request approval (by AI Agent)
    req = request_recovery_approval(
        incident_id=active_incident,
        action_id="reduce_simulated_load",
        playbook_id=pb_id,
        parameters={"throttle_pct": 25},
        requested_by="AI_DIAGNOSIS_AGENT",
        reason="Diagnosis identified CICS CPU burn; proposing safe load reduction",
        db_path=test_db,
    )
    app_id = req["approval_id"]
    assert req["decision"] == "PENDING"
    assert req["status"] == "AWAITING_HUMAN_APPROVAL"

    # Before human approval: execution MUST be blocked
    perm_pending = check_recovery_permission(
        incident_id=active_incident,
        action_id="reduce_simulated_load",
        approval_id=app_id,
        playbook_id=pb_id,
        db_path=test_db,
    )
    assert perm_pending["permitted"] is False
    assert perm_pending["status"] == "BLOCKED"
    assert "still PENDING human sign-off" in perm_pending["reason"]

    # 2. Authorized human operator reviews and signs off
    decision_result = record_human_approval(
        approval_id=app_id,
        decision="APPROVED",
        human_actor="OPERATOR_SARAH",
        reason="Verified telemetry and CICS workload queue",
        db_path=test_db,
    )
    assert decision_result["decision"] == "APPROVED"
    assert decision_result["approved_by"] == "OPERATOR_SARAH"

    # 3. Check permission: MUST now be AUTHORIZED
    perm_authorized = check_recovery_permission(
        incident_id=active_incident,
        action_id="reduce_simulated_load",
        approval_id=app_id,
        playbook_id=pb_id,
        db_path=test_db,
    )
    assert perm_authorized["permitted"] is True
    assert perm_authorized["status"] == "AUTHORIZED"
    assert perm_authorized["approved_by"] == "OPERATOR_SARAH"


# ============================================================================
# 3. REJECTION WORKFLOW
# ============================================================================

def test_rejected_workflow_blocks_execution(test_db, active_incident):
    """Tests that a rejected approval firmly blocks execution authorization."""
    req = request_recovery_approval(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        requested_by="AI_AGENT",
        db_path=test_db,
    )
    app_id = req["approval_id"]

    # Human operator rejects the recovery action
    record_human_approval(
        approval_id=app_id,
        decision="REJECTED",
        human_actor="OPERATOR_BOB",
        reason="Workload still active; manual investigation needed",
        db_path=test_db,
    )

    perm = check_recovery_permission(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        approval_id=app_id,
        db_path=test_db,
    )
    assert perm["permitted"] is False
    assert perm["status"] == "BLOCKED"
    assert "was REJECTED by human operator" in perm["reason"]


# ============================================================================
# 4. SAFETY GATE: PREVENTING AUTOMATED/AI SELF-APPROVAL
# ============================================================================

def test_ai_agent_cannot_self_approve(test_db, active_incident):
    """Ensures AI agents or automated systems cannot authorize their own proposals."""
    req = request_recovery_approval(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        requested_by="AI_AGENT",
        db_path=test_db,
    )
    app_id = req["approval_id"]

    # AI attempting to sign off must be blocked
    with pytest.raises(UnauthorizedActorError):
        record_human_approval(
            approval_id=app_id,
            decision="APPROVED",
            human_actor="AI_AGENT",  # Forbidden!
            db_path=test_db,
        )

    with pytest.raises(UnauthorizedActorError):
        record_human_approval(
            approval_id=app_id,
            decision="APPROVED",
            human_actor="SYSTEM",  # Forbidden!
            db_path=test_db,
        )


# ============================================================================
# 5. INCIDENT & ACTION MISMATCH GATES
# ============================================================================

def test_cross_incident_approval_mismatch(test_db, active_incident):
    """An approval issued for incident A must NEVER authorize incident B."""
    req = request_recovery_approval(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        db_path=test_db,
    )
    app_id = req["approval_id"]
    record_human_approval(app_id, "APPROVED", human_actor="OPERATOR_ALICE", db_path=test_db)

    # 1. Non-existent incident is rejected
    perm_nonexistent = check_recovery_permission(
        incident_id="INC-DOES-NOT-EXIST",
        action_id="stop_owned_chaos_worker",
        approval_id=app_id,
        db_path=test_db,
    )
    assert perm_nonexistent["permitted"] is False
    assert "not found" in perm_nonexistent["reason"]

    # 2. Valid second incident attempting to steal approval from incident A is rejected
    other_inc = "INC-TEST-002"
    create_incident(incident_id=other_inc, fault_type="db2_thread_leak", db_path=test_db)

    perm_mismatch = check_recovery_permission(
        incident_id=other_inc,
        action_id="stop_owned_chaos_worker",
        approval_id=app_id,
        db_path=test_db,
    )
    assert perm_mismatch["permitted"] is False
    assert perm_mismatch["status"] == "BLOCKED"
    assert "Cross-incident authorization blocked" in perm_mismatch["reason"]


def test_cross_action_approval_mismatch(test_db, active_incident):
    """An approval issued for action A must NEVER authorize action B."""
    req = request_recovery_approval(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        db_path=test_db,
    )
    app_id = req["approval_id"]
    record_human_approval(app_id, "APPROVED", human_actor="OPERATOR_ALICE", db_path=test_db)

    # Attempting to execute 'reduce_simulated_load' using an approval for 'stop_owned_chaos_worker'
    perm = check_recovery_permission(
        incident_id=active_incident,
        action_id="reduce_simulated_load",
        approval_id=app_id,
        db_path=test_db,
    )
    assert perm["permitted"] is False
    assert perm["status"] == "BLOCKED"
    assert "authorizes action 'stop_owned_chaos_worker', but execution requested for 'reduce_simulated_load'" in perm["reason"]


# ============================================================================
# 6. DUPLICATE DECISION & SAFE REPLAY TESTS
# ============================================================================

def test_duplicate_decision_rejected(test_db, active_incident):
    """Prevents re-deciding an approval that is already decided."""
    req = request_recovery_approval(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        db_path=test_db,
    )
    app_id = req["approval_id"]
    record_human_approval(app_id, "APPROVED", human_actor="OPERATOR_ALICE", db_path=test_db)

    # Attempting to decide it again must fail
    with pytest.raises(DuplicateApprovalDecisionError):
        record_human_approval(app_id, "REJECTED", human_actor="OPERATOR_BOB", db_path=test_db)


def test_consumed_approval_cannot_be_replayed(test_db, active_incident):
    """Once an approval is consumed upon execution, it cannot be reused."""
    req = request_recovery_approval(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        db_path=test_db,
    )
    app_id = req["approval_id"]
    record_human_approval(app_id, "APPROVED", human_actor="OPERATOR_ALICE", db_path=test_db)

    # Initial check: AUTHORIZED
    perm1 = check_recovery_permission(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        approval_id=app_id,
        db_path=test_db,
    )
    assert perm1["permitted"] is True

    # Execution consumes the approval
    consumed = consume_recovery_approval(app_id, executed_by="CHAOS_RECOVERY_ENGINE", db_path=test_db)
    assert consumed is True

    # Replay attempt: MUST BE BLOCKED
    perm2 = check_recovery_permission(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        approval_id=app_id,
        db_path=test_db,
    )
    assert perm2["permitted"] is False
    assert perm2["status"] == "BLOCKED"
    assert "Approval has already been CONSUMED" in perm2["reason"]


# ============================================================================
# 7. AUDIT LOGGING OF GOVERNANCE GATES
# ============================================================================

def test_governance_audit_logging(test_db, active_incident):
    """Ensures every governance gate decision produces an immutable audit record."""
    req = request_recovery_approval(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        requested_by="AI_AGENT",
        db_path=test_db,
    )
    app_id = req["approval_id"]
    record_human_approval(app_id, "APPROVED", human_actor="OPERATOR_ALICE", db_path=test_db)
    check_recovery_permission(active_incident, "stop_owned_chaos_worker", app_id, db_path=test_db)
    consume_recovery_approval(app_id, executed_by="RECOVERY_RUNNER", db_path=test_db)

    # Also trigger a blocked attempt
    check_recovery_permission(
        incident_id=active_incident,
        action_id="stop_owned_chaos_worker",
        approval_id=app_id,  # Now consumed
        db_path=test_db,
    )

    logs = get_audit_logs(incident_id=active_incident, db_path=test_db)
    actions = [l["action"] for l in logs]

    assert "APPROVAL_REQUESTED" in actions
    assert "APPROVAL_GRANTED" in actions
    assert "RECOVERY_AUTHORIZED" in actions
    assert "APPROVAL_CONSUMED" in actions
    assert "RECOVERY_BLOCKED" in actions
