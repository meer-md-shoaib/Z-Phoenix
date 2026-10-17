"""
Automated Tests for Demo Seed Data (seed_data.py)
Tests:
1. Seed populates all 3 demo scenarios (CPU overload, Db2 lock contention, CICS slowdown).
2. All action IDs are allowlisted.
3. All records are clearly tagged as synthetic demo fixtures.
4. Idempotency: Running seed_demo_data() twice does NOT create duplicates or crash.
5. Governance compliance: Pending approval queue reflects demo state accurately.
"""

import pytest
from database import (
    get_approvals_for_incident,
    get_incident,
    get_pending_approvals,
    get_playbook,
    get_playbook_by_name_version,
    get_recovery_history,
    list_incidents,
    list_playbooks,
)
from seed_data import seed_demo_data
from services.governance import ALLOWED_RECOVERY_ACTIONS


@pytest.fixture
def test_db(tmp_path):
    """Provides a fresh isolated database path for testing."""
    return tmp_path / "test_seed_zphoenix.db"


def test_seed_demo_data_creates_all_scenarios(test_db):
    """Verifies that seed_demo_data populates all 3 expected incident scenarios."""
    counts = seed_demo_data(test_db)

    assert counts["incidents"] == 3
    assert counts["playbooks"] >= 4
    assert counts["approvals"] == 3
    assert counts["recovery_history"] == 1
    assert counts["audit_events"] >= 5

    # 1. CPU Overload Scenario
    inc_cpu = get_incident("DEMO-INC-CPU-001", db_path=test_db)
    assert inc_cpu is not None
    assert inc_cpu["fault_type"] == "cpu_burn"
    assert inc_cpu["status"] == "PENDING_APPROVAL"
    assert inc_cpu["baseline_metrics"]["cpu_pct"] == 24.5
    assert inc_cpu["chaos_metrics"]["cpu_pct"] == 98.2
    assert inc_cpu["diagnosis"]["is_synthetic_demo"] is True

    # 2. Db2 Lock Contention Scenario
    inc_db2 = get_incident("DEMO-INC-DB2-002", db_path=test_db)
    assert inc_db2 is not None
    assert inc_db2["fault_type"] == "db2_lock_contention"
    assert inc_db2["status"] == "RESOLVED"
    assert inc_db2["chaos_metrics"]["deadlock_count"] == 6

    # 3. CICS Slowdown Scenario
    inc_cics = get_incident("DEMO-INC-CICS-003", db_path=test_db)
    assert inc_cics is not None
    assert inc_cics["fault_type"] == "cics_slowdown"
    assert inc_cics["status"] == "RECOVERING"
    assert inc_cics["chaos_metrics"]["active_tasks"] == 480


def test_seed_demo_playbooks_and_allowlist(test_db):
    """Verifies that all seeded playbooks use allowlisted actions and show versioning."""
    seed_demo_data(test_db)

    # Versioning: CICS CPU Throttling has both v1 and v2
    pb_v1 = get_playbook_by_name_version("[DEMO-SYNTHETIC] CICS CPU Throttling", 1, db_path=test_db)
    pb_v2 = get_playbook_by_name_version("[DEMO-SYNTHETIC] CICS CPU Throttling", 2, db_path=test_db)
    assert pb_v1 is not None
    assert pb_v2 is not None
    assert pb_v1["version"] == 1
    assert pb_v2["version"] == 2

    # Check allowlisted actions
    all_pbs = list_playbooks(db_path=test_db)
    for pb in all_pbs:
        assert pb["action_id"] in ALLOWED_RECOVERY_ACTIONS
        assert pb["validation_status"] == "VALIDATED"
        assert "[DEMO-SYNTHETIC]" in pb["name"]


def test_seed_demo_data_is_idempotent(test_db):
    """Verifies that running seed_demo_data twice produces 0 new records and causes no errors."""
    counts_run_1 = seed_demo_data(test_db)
    assert counts_run_1["incidents"] == 3
    assert counts_run_1["playbooks"] >= 4

    # Second run on the same database
    counts_run_2 = seed_demo_data(test_db)
    assert counts_run_2["incidents"] == 0
    assert counts_run_2["playbooks"] == 0
    assert counts_run_2["approvals"] == 0
    assert counts_run_2["recovery_history"] == 0
    assert counts_run_2["audit_events"] == 0

    # Total counts remain unchanged
    all_incs = list_incidents(db_path=test_db)
    assert len(all_incs) == 3


def test_seed_governance_queue_state(test_db):
    """Verifies that demo approvals accurately reflect pending vs resolved states."""
    seed_demo_data(test_db)

    # Exactly 1 incident is pending approval (CPU Overload)
    pending = get_pending_approvals(db_path=test_db)
    assert len(pending) == 1
    assert pending[0]["incident_id"] == "DEMO-INC-CPU-001"
    assert pending[0]["action_id"] == "reduce_simulated_load"

    # Db2 incident approval was approved by operator
    db2_approvals = get_approvals_for_incident("DEMO-INC-DB2-002", db_path=test_db)
    assert len(db2_approvals) == 1
    assert db2_approvals[0]["decision"] == "APPROVED"
    assert db2_approvals[0]["approved_by"] == "DEMO_OPERATOR_ALICE"

    # Recovery history verified for Db2 incident
    history = get_recovery_history(incident_id="DEMO-INC-DB2-002", db_path=test_db)
    assert len(history) == 1
    assert history[0]["outcome"] == "SUCCESS"
    assert history[0]["action_id"] == "reset_simulated_db2_pool"
