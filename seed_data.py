"""
Z-PHOENIX Synthetic Demo Data Seeder
Populates the SQLite database with clearly labelled, synthetic demonstration data for:
1. CPU Overload Scenario (Pending Human Operator Approval)
2. Simulated Db2 Lock Contention Scenario (Fully Resolved with Historical Metrics)
3. Simulated CICS Slowdown Scenario (Approved & Actively Recovering)

SAFETY & GOVERNANCE RULES:
- All data is explicitly labelled with '[DEMO-SYNTHETIC]' and 'is_synthetic_demo: True'.
- All action IDs are strictly allowlisted (e.g. reduce_simulated_load, stop_owned_chaos_worker, reset_simulated_db2_pool).
- Idempotent: Running this script multiple times will NOT duplicate records.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from database import (
    DB_PATH,
    create_incident,
    get_approvals_for_incident,
    get_audit_logs,
    get_connection,
    get_incident,
    get_playbook_by_name_version,
    get_recovery_history,
    init_db,
    log_audit_event,
    record_recovery_attempt,
)
from services.governance import (
    ALLOWED_RECOVERY_ACTIONS,
    record_human_approval,
    request_recovery_approval,
)
from services.playbooks import save_recovery_playbook


def seed_demo_data(db_path: Optional[Path | str] = None) -> Dict[str, int]:
    """
    Populates SQLite database with synthetic demo data in an idempotent manner.
    Returns a summary count of records created:
      {"incidents": int, "playbooks": int, "approvals": int, "recovery_history": int, "audit_events": int}
    """
    target_db = db_path if db_path is not None else DB_PATH
    init_db(target_db)

    counts = {
        "incidents": 0,
        "playbooks": 0,
        "approvals": 0,
        "recovery_history": 0,
        "audit_events": 0,
    }

    # ========================================================================
    # 1. SEED VERSIONED PLAYBOOKS
    # ========================================================================
    demo_playbooks = [
        {
            "name": "[DEMO-SYNTHETIC] CICS CPU Throttling",
            "version": 1,
            "action_id": "reduce_simulated_load",
            "description": "Baseline transaction rate throttling to relieve CICS processor saturation",
            "context": {
                "target_subsystem": "CICS",
                "supported_fault_types": ["cpu_burn", "cpu_overload"],
                "telemetry_conditions": {"cpu_pct": {">=": 80.0}},
                "is_synthetic_demo": True,
            },
            "parameters": {"throttle_pct": 20, "is_synthetic_demo": True},
            "validation_status": "VALIDATED",
            "approved_by": "DEMO_ADMIN",
        },
        {
            "name": "[DEMO-SYNTHETIC] CICS CPU Throttling",
            "version": 2,
            "action_id": "reduce_simulated_load",
            "description": "Adaptive dynamic throttling with 35% load reduction and feedback loop",
            "context": {
                "target_subsystem": "CICS",
                "supported_fault_types": ["cpu_burn", "cpu_overload"],
                "telemetry_conditions": {
                    "cpu_pct": {">=": 85.0},
                    "cics_resp_ms": {">=": 500.0},
                },
                "is_synthetic_demo": True,
            },
            "parameters": {"throttle_pct": 35, "adaptive_step": 5, "is_synthetic_demo": True},
            "validation_status": "VALIDATED",
            "approved_by": "DEMO_ADMIN",
        },
        {
            "name": "[DEMO-SYNTHETIC] Db2 Thread Pool & Lock Reset",
            "version": 1,
            "action_id": "reset_simulated_db2_pool",
            "description": "Drains blocked thread queues and safely resets simulated Db2 thread pool",
            "context": {
                "target_subsystem": "Db2",
                "supported_fault_types": ["db2_lock_contention", "thread_leak"],
                "telemetry_conditions": {"lock_wait_seconds": {">=": 5.0}},
                "is_synthetic_demo": True,
            },
            "parameters": {"drain_timeout_sec": 10, "is_synthetic_demo": True},
            "validation_status": "VALIDATED",
            "approved_by": "DEMO_ADMIN",
        },
        {
            "name": "[DEMO-SYNTHETIC] CICS Rogue Worker Teardown",
            "version": 1,
            "action_id": "stop_owned_chaos_worker",
            "description": "Terminates simulated chaos injector process consuming socket listener threads",
            "context": {
                "target_subsystem": "CICS",
                "supported_fault_types": ["cics_slowdown", "cics_socket_exhaustion"],
                "telemetry_conditions": {"active_tasks": {">=": 300}},
                "is_synthetic_demo": True,
            },
            "parameters": {"force_kill": False, "is_synthetic_demo": True},
            "validation_status": "VALIDATED",
            "approved_by": "DEMO_ADMIN",
        },
        {
            "name": "[DEMO-SYNTHETIC] Non-Disruptive Telemetry Health Check",
            "version": 1,
            "action_id": "no_action",
            "description": "Pass-through observer policy for transient metric jitter without workload disruption",
            "context": {
                "target_subsystem": "*",
                "supported_fault_types": ["transient_jitter", "telemetry_spike"],
                "is_synthetic_demo": True,
            },
            "parameters": {"is_synthetic_demo": True},
            "validation_status": "VALIDATED",
            "approved_by": "DEMO_ADMIN",
        },
    ]

    playbook_map = {}
    for pb_spec in demo_playbooks:
        existing = get_playbook_by_name_version(pb_spec["name"], pb_spec["version"], db_path=target_db)
        if existing:
            playbook_map[(pb_spec["name"], pb_spec["version"])] = existing["playbook_id"]
        else:
            created = save_recovery_playbook(
                name=pb_spec["name"],
                action_id=pb_spec["action_id"],
                context=pb_spec["context"],
                parameters=pb_spec["parameters"],
                description=pb_spec["description"],
                version=pb_spec["version"],
                validation_status=pb_spec["validation_status"],
                approved_by=pb_spec["approved_by"],
                db_path=target_db,
            )
            playbook_map[(pb_spec["name"], pb_spec["version"])] = created["playbook_id"]
            counts["playbooks"] += 1

    # ========================================================================
    # 2. SCENARIO 1: CPU OVERLOAD SCENARIO (PENDING HUMAN APPROVAL)
    # ========================================================================
    inc1_id = "DEMO-INC-CPU-001"
    if not get_incident(inc1_id, db_path=target_db):
        create_incident(
            incident_id=inc1_id,
            fault_type="cpu_burn",
            status="PENDING_APPROVAL",
            baseline_metrics={
                "cpu_pct": 24.5,
                "ziip_util_pct": 18.0,
                "ready_queue_depth": 2,
                "mips_consumed": 1250,
                "cics_resp_ms": 15,
                "is_synthetic_demo": True,
            },
            chaos_metrics={
                "cpu_pct": 98.2,
                "ziip_util_pct": 96.5,
                "ready_queue_depth": 28,
                "mips_consumed": 7900,
                "cics_resp_ms": 1420,
                "is_synthetic_demo": True,
            },
            diagnosis={
                "root_cause": "[DEMO-SYNTHETIC] Runaway batch analytics worker saturating CP engines",
                "evidence": [
                    "CPU utilization increased from 24.5% to 98.2%",
                    "Ready queue depth spiked to 28 tasks",
                    "CICS transaction response times degraded by 9400%",
                ],
                "confidence_score": 95,
                "risk_level": "medium",
                "blast_radius": {
                    "affected_components": ["CICS_REGION_A", "BATCH_JOB_ANALYTICS"],
                    "impact": "Customer-facing online transaction latency elevated",
                },
                "recovery_steps": [
                    {
                        "action_id": "reduce_simulated_load",
                        "parameters": {"throttle_pct": 35},
                    }
                ],
                "rollback_conditions": ["Restore standard batch allocation if CPU < 60%"],
                "is_synthetic_demo": True,
            },
            db_path=target_db,
        )
        counts["incidents"] += 1

        # Audit events for Incident 1
        log_audit_event(
            actor="SYSTEM",
            action="[DEMO-SYNTHETIC] BASELINE_ESTABLISHED",
            incident_id=inc1_id,
            details={"cpu_pct": 24.5, "status": "HEALTHY", "is_synthetic_demo": True},
            db_path=target_db,
        )
        log_audit_event(
            actor="CHAOS_ENGINE",
            action="[DEMO-SYNTHETIC] CHAOS_INJECTED",
            incident_id=inc1_id,
            details={"fault_type": "cpu_burn", "target": "CP_CORES", "is_synthetic_demo": True},
            db_path=target_db,
        )
        counts["audit_events"] += 2

        # Create Pending Approval request
        pb_cpu_v2_id = playbook_map.get(("[DEMO-SYNTHETIC] CICS CPU Throttling", 2))
        req = request_recovery_approval(
            incident_id=inc1_id,
            action_id="reduce_simulated_load",
            playbook_id=pb_cpu_v2_id,
            parameters={"throttle_pct": 35, "is_synthetic_demo": True},
            requested_by="AI_DIAGNOSIS_AGENT",
            reason="[DEMO-SYNTHETIC] AI diagnosis recommends reducing simulated load by 35% to relieve CPU starvation",
            db_path=target_db,
        )
        counts["approvals"] += 1

    # ========================================================================
    # 3. SCENARIO 2: SIMULATED Db2 LOCK CONTENTION (RESOLVED & VERIFIED)
    # ========================================================================
    inc2_id = "DEMO-INC-DB2-002"
    if not get_incident(inc2_id, db_path=target_db):
        create_incident(
            incident_id=inc2_id,
            fault_type="db2_lock_contention",
            status="RESOLVED",
            baseline_metrics={
                "db2_active_threads": 42,
                "lock_wait_seconds": 0.05,
                "deadlock_count": 0,
                "bufferpool_hit_pct": 98.7,
                "tx_per_sec": 850,
                "is_synthetic_demo": True,
            },
            chaos_metrics={
                "db2_active_threads": 185,
                "lock_wait_seconds": 12.8,
                "deadlock_count": 6,
                "bufferpool_hit_pct": 94.2,
                "tx_per_sec": 48,
                "is_synthetic_demo": True,
            },
            diagnosis={
                "root_cause": "[DEMO-SYNTHETIC] Long-running uncommitted transaction holding exclusive table space lock",
                "evidence": [
                    "Average lock wait escalated from 0.05s to 12.8s",
                    "Active thread pool saturated (185 threads)",
                    "Deadlock frequency reached 6 events/min",
                ],
                "confidence_score": 92,
                "risk_level": "medium",
                "blast_radius": {
                    "affected_components": ["DB2_SUBSYS_DSN1", "TABLESPACE_ACCT01"],
                    "impact": "Account balance inquiry queries queueing",
                },
                "recovery_steps": [
                    {"action_id": "reset_simulated_db2_pool", "parameters": {"drain_timeout_sec": 10}}
                ],
                "rollback_conditions": ["Abort reset if active write transactions exceed threshold"],
                "is_synthetic_demo": True,
            },
            db_path=target_db,
        )
        counts["incidents"] += 1

        # Complete approval & recovery history lifecycle
        pb_db2_id = playbook_map.get(("[DEMO-SYNTHETIC] Db2 Thread Pool & Lock Reset", 1))
        app_req = request_recovery_approval(
            incident_id=inc2_id,
            action_id="reset_simulated_db2_pool",
            playbook_id=pb_db2_id,
            parameters={"drain_timeout_sec": 10, "is_synthetic_demo": True},
            requested_by="AI_DIAGNOSIS_AGENT",
            reason="[DEMO-SYNTHETIC] Reset simulated thread pool to clear deadlocked threads",
            db_path=target_db,
        )
        app_id = app_req["approval_id"]

        # Human operator approved
        record_human_approval(
            approval_id=app_id,
            decision="APPROVED",
            human_actor="DEMO_OPERATOR_ALICE",
            reason="[DEMO-SYNTHETIC] Verified telemetry confirms runaway thread deadlock in ACCT01",
            db_path=target_db,
        )
        counts["approvals"] += 1

        # Recovery executed successfully
        record_recovery_attempt(
            incident_id=inc2_id,
            action_id="reset_simulated_db2_pool",
            outcome="SUCCESS",
            playbook_id=pb_db2_id,
            before_metrics={
                "lock_wait_seconds": 12.8,
                "db2_active_threads": 185,
                "deadlock_count": 6,
                "is_synthetic_demo": True,
            },
            after_metrics={
                "lock_wait_seconds": 0.08,
                "db2_active_threads": 44,
                "deadlock_count": 0,
                "is_synthetic_demo": True,
            },
            db_path=target_db,
        )
        counts["recovery_history"] += 1

        # Audit logs for Incident 2
        log_audit_event(
            actor="DEMO_OPERATOR_ALICE",
            action="[DEMO-SYNTHETIC] RECOVERY_EXECUTED",
            incident_id=inc2_id,
            details={"action_id": "reset_simulated_db2_pool", "outcome": "SUCCESS", "is_synthetic_demo": True},
            db_path=target_db,
        )
        log_audit_event(
            actor="SYSTEM",
            action="[DEMO-SYNTHETIC] TELEMETRY_VERIFIED_HEALTHY",
            incident_id=inc2_id,
            details={"lock_wait_seconds": 0.08, "status": "RESOLVED", "is_synthetic_demo": True},
            db_path=target_db,
        )
        counts["audit_events"] += 2

    # ========================================================================
    # 4. SCENARIO 3: SIMULATED CICS SLOWDOWN (APPROVED & RECOVERING)
    # ========================================================================
    inc3_id = "DEMO-INC-CICS-003"
    if not get_incident(inc3_id, db_path=target_db):
        create_incident(
            incident_id=inc3_id,
            fault_type="cics_slowdown",
            status="RECOVERING",
            baseline_metrics={
                "cics_resp_ms": 18,
                "active_tasks": 35,
                "max_task_pct": 28.0,
                "storage_cushion_free_kb": 45000,
                "is_synthetic_demo": True,
            },
            chaos_metrics={
                "cics_resp_ms": 1850,
                "active_tasks": 480,
                "max_task_pct": 96.0,
                "storage_cushion_free_kb": 8200,
                "is_synthetic_demo": True,
            },
            diagnosis={
                "root_cause": "[DEMO-SYNTHETIC] Socket listener thread pool exhaustion causing MaxTask limit threshold breach",
                "evidence": [
                    "Active CICS tasks at 96% of maximum limit (480/500)",
                    "Response times degraded from 18ms to 1850ms",
                    "Storage cushion dropped to critical boundary (8.2 MB)",
                ],
                "confidence_score": 90,
                "risk_level": "high",
                "blast_radius": {
                    "affected_components": ["CICS_REGION_PROD1", "TCP_IP_LISTENER"],
                    "impact": "New transaction handshakes timing out",
                },
                "recovery_steps": [
                    {"action_id": "stop_owned_chaos_worker", "parameters": {"force_kill": False}}
                ],
                "rollback_conditions": ["If tasks do not decline within 30s, escalate to region restart"],
                "is_synthetic_demo": True,
            },
            db_path=target_db,
        )
        counts["incidents"] += 1

        # Create approval and record human approval
        pb_cics_teardown_id = playbook_map.get(("[DEMO-SYNTHETIC] CICS Rogue Worker Teardown", 1))
        req_cics = request_recovery_approval(
            incident_id=inc3_id,
            action_id="stop_owned_chaos_worker",
            playbook_id=pb_cics_teardown_id,
            parameters={"force_kill": False, "is_synthetic_demo": True},
            requested_by="AI_DIAGNOSIS_AGENT",
            reason="[DEMO-SYNTHETIC] Stop rogue stress injector process consuming socket listener threads",
            db_path=target_db,
        )
        app_cics_id = req_cics["approval_id"]

        # Human operator approved
        record_human_approval(
            approval_id=app_cics_id,
            decision="APPROVED",
            human_actor="DEMO_OPERATOR_BOB",
            reason="[DEMO-SYNTHETIC] Operator authorized termination of simulated chaos injector",
            db_path=target_db,
        )
        counts["approvals"] += 1

        log_audit_event(
            actor="DEMO_OPERATOR_BOB",
            action="[DEMO-SYNTHETIC] RECOVERY_INITIATED",
            incident_id=inc3_id,
            details={"action_id": "stop_owned_chaos_worker", "status": "RECOVERING", "is_synthetic_demo": True},
            db_path=target_db,
        )
        counts["audit_events"] += 1

    return counts


if __name__ == "__main__":
    print("Seeding Z-PHOENIX synthetic demonstration data...")
    result = seed_demo_data()
    print("Seed process completed!")
    print(f"- Incidents created: {result['incidents']}")
    print(f"- Playbooks created: {result['playbooks']}")
    print(f"- Approvals created: {result['approvals']}")
    print(f"- Recovery history entries: {result['recovery_history']}")
    print(f"- Audit events created: {result['audit_events']}")
