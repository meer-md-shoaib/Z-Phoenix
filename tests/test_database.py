"""
Tests for Z-PHOENIX Database Initialization & Query Helpers
Uses an isolated SQLite database file per test via pytest's tmp_path.
"""

import sqlite3
import pytest
from database import (
    init_db,
    get_connection,
    create_incident,
    get_incident,
    update_incident_status,
    list_incidents,
    create_playbook,
    get_playbook,
    list_playbooks,
    create_approval_request,
    update_approval_decision,
    get_pending_approvals,
    log_audit_event,
    get_audit_logs,
    record_recovery_attempt,
    get_recovery_history,
)


@pytest.fixture
def test_db(tmp_path):
    """Provides a fresh isolated test database path for each test."""
    db_file = tmp_path / "test_zphoenix.db"
    init_db(db_file)
    return db_file


def test_init_db_creates_all_tables(test_db):
    """Verifies that all 5 required tables and indexes exist."""
    conn = get_connection(test_db)
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        expected = {"incidents", "playbooks", "approvals", "audit_log", "recovery_history"}
        assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"
    finally:
        conn.close()


def test_foreign_keys_enforced(test_db):
    """Verifies that foreign key constraints prevent orphan records."""
    # Attempting to create an approval for a non-existent incident must fail
    with pytest.raises(sqlite3.IntegrityError):
        create_approval_request(
            incident_id="NON_EXISTENT_INCIDENT",
            action_id="stop_owned_chaos_worker",
            db_path=test_db,
        )


def test_playbook_unique_name_version(test_db):
    """Ensures versioning constraint: duplicate (name, version) is rejected."""
    create_playbook(
        name="CICS CPU Throttling",
        version=1,
        action_id="reduce_simulated_load",
        context={"subsystem": "CICS"},
        db_path=test_db,
    )

    with pytest.raises(sqlite3.IntegrityError):
        create_playbook(
            name="CICS CPU Throttling",
            version=1,  # Same version!
            action_id="reduce_simulated_load",
            context={"subsystem": "CICS"},
            db_path=test_db,
        )

    # Different version must succeed
    p2_id = create_playbook(
        name="CICS CPU Throttling",
        version=2,  # Incremented version
        action_id="reduce_simulated_load",
        context={"subsystem": "CICS", "threshold": 85},
        db_path=test_db,
    )
    assert p2_id > 0


def test_incident_lifecycle(test_db):
    """Tests creating, fetching, and updating incidents."""
    inc_id = "INC-20261017-001"
    create_incident(
        incident_id=inc_id,
        fault_type="cpu_burn",
        status="DETECTED",
        baseline_metrics={"cpu_pct": 32.5, "cics_resp_ms": 12},
        chaos_metrics={"cpu_pct": 98.4, "cics_resp_ms": 1450},
        db_path=test_db,
    )

    incident = get_incident(inc_id, db_path=test_db)
    assert incident is not None
    assert incident["incident_id"] == inc_id
    assert incident["fault_type"] == "cpu_burn"
    assert incident["status"] == "DETECTED"
    assert incident["baseline_metrics"]["cpu_pct"] == 32.5
    assert incident["chaos_metrics"]["cpu_pct"] == 98.4

    # Update status to RECOVERED with post-recovery telemetry
    updated = update_incident_status(
        incident_id=inc_id,
        status="RESOLVED",
        recovered_metrics={"cpu_pct": 34.0, "cics_resp_ms": 15},
        db_path=test_db,
    )
    assert updated is True

    refetched = get_incident(inc_id, db_path=test_db)
    assert refetched["status"] == "RESOLVED"
    assert refetched["recovered_metrics"]["cpu_pct"] == 34.0

    all_incs = list_incidents(db_path=test_db)
    assert len(all_incs) == 1


def test_governance_approval_workflow(test_db):
    """Tests human approval request creation, decision, and queue filtering."""
    inc_id = "INC-20261017-002"
    create_incident(incident_id=inc_id, fault_type="db2_thread_leak", db_path=test_db)

    # 1. Create a pending approval
    app_id = create_approval_request(
        incident_id=inc_id,
        action_id="stop_owned_chaos_worker",
        reason="Requires operator authorization before resetting pool",
        db_path=test_db,
    )
    assert app_id > 0

    pending = get_pending_approvals(db_path=test_db)
    assert len(pending) == 1
    assert pending[0]["approval_id"] == app_id
    assert pending[0]["decision"] == "PENDING"
    assert pending[0]["fault_type"] == "db2_thread_leak"

    # 2. Operator signs off
    success = update_approval_decision(
        approval_id=app_id,
        decision="APPROVED",
        approved_by="MAINFRAME_OPERATOR_1",
        reason="Verified telemetry indicates simulated worker leak",
        db_path=test_db,
    )
    assert success is True

    # 3. Pending queue is now empty
    remaining_pending = get_pending_approvals(db_path=test_db)
    assert len(remaining_pending) == 0


def test_audit_log_records_events(test_db):
    """Ensures audit trail persists events accurately."""
    evt_id = log_audit_event(
        actor="AI_DIAGNOSIS_AGENT",
        action="PROPOSED_PLAYBOOK",
        incident_id=None,
        details={"playbook": "reduce_simulated_load", "confidence": 95},
        db_path=test_db,
    )
    assert evt_id > 0

    logs = get_audit_logs(db_path=test_db)
    assert len(logs) == 1
    assert logs[0]["actor"] == "AI_DIAGNOSIS_AGENT"
    assert logs[0]["action"] == "PROPOSED_PLAYBOOK"
    assert logs[0]["details"]["confidence"] == 95


def test_recovery_history_tracking(test_db):
    """Tests recording recovery execution telemetry."""
    inc_id = "INC-20261017-003"
    create_incident(incident_id=inc_id, fault_type="cics_socket_exhaustion", db_path=test_db)

    rec_id = record_recovery_attempt(
        incident_id=inc_id,
        action_id="stop_owned_chaos_worker",
        outcome="SUCCESS",
        before_metrics={"socket_wait_count": 450},
        after_metrics={"socket_wait_count": 2},
        db_path=test_db,
    )
    assert rec_id > 0

    history = get_recovery_history(incident_id=inc_id, db_path=test_db)
    assert len(history) == 1
    assert history[0]["outcome"] == "SUCCESS"
    assert history[0]["action_id"] == "stop_owned_chaos_worker"
