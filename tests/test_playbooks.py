"""
Automated Tests for Playbooks Service (services/playbooks.py)
Tests:
1. Versioning: auto-increment, specific versions, duplicate version errors, deprecation.
2. Context matching & scoring (0-100) based on subsystem, fault_type, and telemetry.
3. Missing fields and validation error handling.
4. NO_MATCH fallback when similarity threshold is not met.
5. Safety disclaimers and governance flags.
"""

import pytest
from database import init_db
from services.playbooks import (
    DuplicatePlaybookVersionError,
    InvalidPlaybookError,
    calculate_match_score,
    deprecate_playbook_version,
    find_matching_playbooks,
    get_latest_playbook,
    get_playbook_by_version,
    list_all_playbooks,
    save_recovery_playbook,
)


@pytest.fixture
def test_db(tmp_path):
    """Provides a fresh isolated database for each test."""
    db_file = tmp_path / "test_playbooks.db"
    init_db(db_file)
    return db_file


# ============================================================================
# 1. VERSIONING TESTS
# ============================================================================

def test_playbook_auto_version_increment(test_db):
    """Verifies that saving playbooks with the same name automatically increments version numbers."""
    # First save -> version 1
    pb1 = save_recovery_playbook(
        name="CICS CPU Throttling",
        action_id="reduce_simulated_load",
        context={"subsystem": "CICS", "fault_type": "cpu_burn"},
        db_path=test_db,
    )
    assert pb1["version"] == 1

    # Second save -> version 2
    pb2 = save_recovery_playbook(
        name="CICS CPU Throttling",
        action_id="reduce_simulated_load",
        context={"subsystem": "CICS", "fault_type": "cpu_burn", "min_cpu_pct": 85},
        db_path=test_db,
    )
    assert pb2["version"] == 2

    # Query latest -> should return version 2
    latest = get_latest_playbook("CICS CPU Throttling", db_path=test_db)
    assert latest is not None
    assert latest["version"] == 2

    # Query specific version 1 -> should return version 1
    v1 = get_playbook_by_version("CICS CPU Throttling", 1, db_path=test_db)
    assert v1 is not None
    assert v1["version"] == 1


def test_playbook_duplicate_version_rejected(test_db):
    """Verifies that attempting to insert an already existing version raises DuplicatePlaybookVersionError."""
    save_recovery_playbook(
        name="Db2 Thread Pool Reset",
        version=1,
        action_id="stop_owned_chaos_worker",
        context={"subsystem": "Db2", "fault_type": "thread_leak"},
        db_path=test_db,
    )

    # Attempting to overwrite version 1 explicitly must fail
    with pytest.raises(DuplicatePlaybookVersionError):
        save_recovery_playbook(
            name="Db2 Thread Pool Reset",
            version=1,  # Duplicate!
            action_id="stop_owned_chaos_worker",
            context={"subsystem": "Db2", "fault_type": "thread_leak"},
            db_path=test_db,
        )


def test_playbook_deprecation(test_db):
    """Verifies that older versions can be marked DEPRECATED and excluded from active lists."""
    save_recovery_playbook(
        name="Worker Teardown",
        version=1,
        action_id="stop_owned_chaos_worker",
        context={"subsystem": "*"},
        db_path=test_db,
    )
    save_recovery_playbook(
        name="Worker Teardown",
        version=2,
        action_id="stop_owned_chaos_worker",
        context={"subsystem": "*"},
        db_path=test_db,
    )

    # Deprecate version 1
    success = deprecate_playbook_version("Worker Teardown", 1, deprecated_by="OPERATOR_ALICE", db_path=test_db)
    assert success is True

    # Active playbooks list should only contain version 2
    active = list_all_playbooks(include_deprecated=False, db_path=test_db)
    versions = [p["version"] for p in active if p["name"] == "Worker Teardown"]
    assert versions == [2]

    # Full list including deprecated shows both
    all_pbs = list_all_playbooks(include_deprecated=True, db_path=test_db)
    versions_all = [p["version"] for p in all_pbs if p["name"] == "Worker Teardown"]
    assert sorted(versions_all) == [1, 2]


# ============================================================================
# 2. VALIDATION & MISSING FIELDS TESTS
# ============================================================================

def test_invalid_playbook_parameters(test_db):
    """Ensures playbooks with empty names, empty actions, or invalid contexts are rejected."""
    with pytest.raises(InvalidPlaybookError):
        save_recovery_playbook(name="", action_id="stop_owned_chaos_worker", context={}, db_path=test_db)

    with pytest.raises(InvalidPlaybookError):
        save_recovery_playbook(name="Valid Name", action_id="", context={}, db_path=test_db)

    with pytest.raises(InvalidPlaybookError):
        save_recovery_playbook(
            name="Valid Name",
            action_id="reduce_simulated_load",
            context="not_a_dict",  # type: ignore
            db_path=test_db,
        )


# ============================================================================
# 3. DETERMINISTIC MATCH SCORING (0 to 100)
# ============================================================================

def test_calculate_match_score_high_match():
    """Tests exact match on subsystem, fault type, and telemetry conditions."""
    playbook = {
        "context": {
            "target_subsystem": "CICS",
            "supported_fault_types": ["cpu_burn", "high_cpu"],
            "telemetry_conditions": {
                "cpu_pct": {">=": 80.0},
                "cics_resp_ms": {">=": 500.0},
            },
        }
    }

    incident = {
        "subsystem": "CICS",
        "fault_type": "cpu_burn",
        "metrics": {"cpu_pct": 98.4, "cics_resp_ms": 1200.0},
    }

    score, reasons = calculate_match_score(playbook, incident)
    # 35 (subsystem) + 40 (fault_type) + 25 (both telemetry conditions passed) = 100
    assert score == 100
    assert any("Exact subsystem match on 'CICS'" in r for r in reasons)
    assert any("Exact fault_type match on 'cpu_burn'" in r for r in reasons)
    assert any("Telemetry conditions passed: 2/2" in r for r in reasons)


def test_calculate_match_score_subsystem_conflict():
    """Tests that a subsystem conflict reduces score appropriately."""
    playbook = {
        "context": {
            "target_subsystem": "Db2",
            "supported_fault_types": ["cpu_burn"],
        }
    }
    incident = {
        "subsystem": "CICS",
        "fault_type": "cpu_burn",
        "metrics": {},
    }

    score, reasons = calculate_match_score(playbook, incident)
    # 0 (subsystem conflict) + 40 (fault_type match) + 15 (no telemetry conditions) = 55
    assert score == 55
    assert any("Subsystem conflict" in r for r in reasons)


# ============================================================================
# 4. CONTEXT MATCHING & RANKING
# ============================================================================

def test_find_matching_playbooks_ranking(test_db):
    """Ensures matching ranks playbooks by score descending."""
    # Playbook A: Generic CICS load reduction (Moderate match)
    save_recovery_playbook(
        name="CICS Generic Relief",
        action_id="reduce_simulated_load",
        context={"subsystem": "CICS", "fault_type": "*"},
        db_path=test_db,
    )

    # Playbook B: Highly tailored for CICS CPU Burn with threshold (High match)
    save_recovery_playbook(
        name="CICS Severe CPU Burn Mitigation",
        action_id="reduce_simulated_load",
        context={
            "subsystem": "CICS",
            "fault_type": "cpu_burn",
            "min_cpu_pct": 80.0,
        },
        db_path=test_db,
    )

    incident = {
        "subsystem": "CICS",
        "fault_type": "cpu_burn",
        "metrics": {"cpu_pct": 95.0},
    }

    result = find_matching_playbooks(incident, min_score_threshold=50, db_path=test_db)
    assert result["status"] == "MATCH_FOUND"
    assert result["match_found"] is True
    assert len(result["ranked_matches"]) == 2

    top = result["top_match"]
    assert top["name"] == "CICS Severe CPU Burn Mitigation"
    assert top["match_score"] == 100

    # Ensure safety flags are set
    assert top["requires_governance_approval"] is True
    assert top["governance_cleared"] is False
    assert "safety_disclaimer" in top


# ============================================================================
# 5. NO_MATCH FALLBACK & MISSING FIELD HANDLING
# ============================================================================

def test_find_matching_playbooks_no_match(test_db):
    """Ensures that unhandled or low-scoring faults return a clear NO_MATCH response."""
    # Save a CICS-specific playbook
    save_recovery_playbook(
        name="CICS CPU Throttling",
        action_id="reduce_simulated_load",
        context={"subsystem": "CICS", "fault_type": "cpu_burn"},
        db_path=test_db,
    )

    # Incident with a completely unrelated fault in Db2
    incident = {
        "subsystem": "Db2",
        "fault_type": "tablespace_corruption",
        "metrics": {},
    }

    result = find_matching_playbooks(incident, min_score_threshold=50, db_path=test_db)
    assert result["status"] == "NO_MATCH"
    assert result["match_found"] is False
    assert result["top_match"] is None
    assert len(result["ranked_matches"]) == 0
    assert "No suitable recovery playbook meets the minimum threshold" in result["message"]


def test_find_matching_playbooks_missing_fault_type(test_db):
    """Ensures missing fault_type is caught gracefully without unhandled exceptions."""
    result = find_matching_playbooks({}, db_path=test_db)
    assert result["status"] == "NO_MATCH"
    assert result["match_found"] is False
    assert "Missing required field 'fault_type'" in result["message"]
