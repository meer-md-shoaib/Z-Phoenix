"""
Z-PHOENIX Database Module
Initializes SQLite schema and provides query helper functions for:
- Incidents
- Versioned Playbooks
- Human Approvals
- Immutable Audit Logs
- Recovery History
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default database path stored inside the project folder
DB_PATH = Path(__file__).resolve().parent / "zphoenix.db"


def get_connection(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    """
    Connects to SQLite database and enables foreign keys and row factory.
    Accepts an optional db_path (useful for isolated unit tests using ':memory:').
    """
    target = db_path if db_path is not None else DB_PATH
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Optional[Path | str] = None) -> None:
    """
    Initializes all core tables and performance indexes.
    """
    conn = get_connection(db_path)
    try:
        conn.executescript("""
        -- 1. Incidents Table: Stores system faults and telemetry checkpoints
        CREATE TABLE IF NOT EXISTS incidents (
            incident_id TEXT PRIMARY KEY,
            fault_type TEXT NOT NULL,
            status TEXT NOT NULL,                -- 'DETECTED', 'ANALYZING', 'PENDING_APPROVAL', 'RECOVERING', 'RESOLVED', 'FAILED'
            baseline_metrics_json TEXT,
            chaos_metrics_json TEXT,
            recovered_metrics_json TEXT,
            diagnosis_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- 2. Playbooks Table: Stores versioned recovery playbooks
        CREATE TABLE IF NOT EXISTS playbooks (
            playbook_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            version INTEGER NOT NULL,
            action_id TEXT NOT NULL,             -- e.g. 'stop_owned_chaos_worker', 'reduce_simulated_load'
            context_json TEXT NOT NULL,          -- Matching criteria & trigger thresholds
            parameters_json TEXT DEFAULT '{}',   -- Specific action parameters
            validation_status TEXT NOT NULL,     -- 'VALIDATED', 'PENDING', 'DEPRECATED'
            approved_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, version)
        );

        -- 3. Approvals Table: Tracks human-in-the-loop governance sign-offs
        CREATE TABLE IF NOT EXISTS approvals (
            approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            playbook_id INTEGER,
            action_id TEXT NOT NULL,
            decision TEXT NOT NULL,              -- 'PENDING', 'APPROVED', 'REJECTED'
            approved_by TEXT,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (incident_id)
                REFERENCES incidents(incident_id) ON DELETE CASCADE,
            FOREIGN KEY (playbook_id)
                REFERENCES playbooks(playbook_id) ON DELETE SET NULL
        );

        -- 4. Audit Log Table: Immutable history of all actions and governance checks
        CREATE TABLE IF NOT EXISTS audit_log (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT,
            actor TEXT NOT NULL,                 -- e.g. 'SYSTEM', 'AI_DIAGNOSIS', 'OPERATOR_BOB'
            action TEXT NOT NULL,                -- e.g. 'CHAOS_TRIGGERED', 'APPROVAL_GRANTED', 'ACTION_EXECUTED'
            details_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (incident_id)
                REFERENCES incidents(incident_id) ON DELETE SET NULL
        );

        -- 5. Recovery History Table: Outcome and verification metrics for recovery actions
        CREATE TABLE IF NOT EXISTS recovery_history (
            recovery_id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            playbook_id INTEGER,
            action_id TEXT NOT NULL,
            outcome TEXT NOT NULL,               -- 'SUCCESS', 'FAILED', 'ROLLED_BACK'
            before_metrics_json TEXT,
            after_metrics_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (incident_id)
                REFERENCES incidents(incident_id) ON DELETE CASCADE,
            FOREIGN KEY (playbook_id)
                REFERENCES playbooks(playbook_id) ON DELETE SET NULL
        );

        -- Performance Indexes
        CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
        CREATE INDEX IF NOT EXISTS idx_playbooks_action ON playbooks(action_id);
        CREATE INDEX IF NOT EXISTS idx_approvals_incident ON approvals(incident_id);
        CREATE INDEX IF NOT EXISTS idx_audit_incident ON audit_log(incident_id);
        CREATE INDEX IF NOT EXISTS idx_recovery_incident ON recovery_history(incident_id);
        """)

        conn.commit()

        tables = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table'
            AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """).fetchall()

        print("Database initialized successfully!")
        print("Tables verified:")
        for table in tables:
            print("-", table["name"])

    finally:
        conn.close()


# ============================================================================
# QUERY HELPERS: INCIDENTS
# ============================================================================

def create_incident(
    incident_id: str,
    fault_type: str,
    status: str = "DETECTED",
    baseline_metrics: Optional[Dict[str, Any]] = None,
    chaos_metrics: Optional[Dict[str, Any]] = None,
    diagnosis: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> str:
    """Inserts a new incident record."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO incidents (
                    incident_id, fault_type, status,
                    baseline_metrics_json, chaos_metrics_json, diagnosis_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    fault_type,
                    status,
                    json.dumps(baseline_metrics) if baseline_metrics else None,
                    json.dumps(chaos_metrics) if chaos_metrics else None,
                    json.dumps(diagnosis) if diagnosis else None,
                ),
            )
        return incident_id
    finally:
        conn.close()


def get_incident(incident_id: str, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    """Fetches a single incident by ID as a dictionary."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if not row:
            return None

        result = dict(row)
        for key in ["baseline_metrics_json", "chaos_metrics_json", "recovered_metrics_json", "diagnosis_json"]:
            if result.get(key):
                try:
                    result[key.replace("_json", "")] = json.loads(result[key])
                except json.JSONDecodeError:
                    result[key.replace("_json", "")] = None
        return result
    finally:
        conn.close()


def update_incident_status(
    incident_id: str,
    status: str,
    recovered_metrics: Optional[Dict[str, Any]] = None,
    diagnosis: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> bool:
    """Updates the status and optional metrics of an incident."""
    conn = get_connection(db_path)
    try:
        updates = ["status = ?"]
        params: List[Any] = [status]

        if recovered_metrics is not None:
            updates.append("recovered_metrics_json = ?")
            params.append(json.dumps(recovered_metrics))

        if diagnosis is not None:
            updates.append("diagnosis_json = ?")
            params.append(json.dumps(diagnosis))

        params.append(incident_id)
        sql = f"UPDATE incidents SET {', '.join(updates)} WHERE incident_id = ?"

        with conn:
            cur = conn.execute(sql, params)
            return cur.rowcount > 0
    finally:
        conn.close()


def list_incidents(limit: int = 50, db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """Returns a list of the most recent incidents."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================================
# QUERY HELPERS: PLAYBOOKS
# ============================================================================

def create_playbook(
    name: str,
    version: int,
    action_id: str,
    context: Dict[str, Any],
    parameters: Optional[Dict[str, Any]] = None,
    validation_status: str = "VALIDATED",
    description: str = "",
    approved_by: Optional[str] = "ADMIN",
    db_path: Optional[Path | str] = None,
) -> int:
    """Inserts a new versioned playbook record."""
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO playbooks (
                    name, description, version, action_id,
                    context_json, parameters_json, validation_status, approved_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    description,
                    version,
                    action_id,
                    json.dumps(context),
                    json.dumps(parameters or {}),
                    validation_status,
                    approved_by,
                ),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_playbook(playbook_id: int, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a playbook by its primary key ID."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM playbooks WHERE playbook_id = ?", (playbook_id,)).fetchone()
        if not row:
            return None
        res = dict(row)
        res["context"] = json.loads(res["context_json"])
        res["parameters"] = json.loads(res.get("parameters_json") or "{}")
        return res
    finally:
        conn.close()


def list_playbooks(
    validation_status: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Lists all playbooks, optionally filtered by validation status."""
    conn = get_connection(db_path)
    try:
        if validation_status:
            rows = conn.execute(
                "SELECT * FROM playbooks WHERE validation_status = ? ORDER BY name, version DESC",
                (validation_status,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM playbooks ORDER BY name, version DESC").fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["context"] = json.loads(d["context_json"])
            d["parameters"] = json.loads(d.get("parameters_json") or "{}")
            results.append(d)
        return results
    finally:
        conn.close()


def get_playbook_by_name_version(
    name: str,
    version: int,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieves a specific playbook by its name and version."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM playbooks WHERE name = ? AND version = ?", (name, version)
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        res["context"] = json.loads(res["context_json"])
        res["parameters"] = json.loads(res.get("parameters_json") or "{}")
        return res
    finally:
        conn.close()


def get_latest_playbook_version(
    name: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieves the latest version of a playbook by name."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM playbooks WHERE name = ? ORDER BY version DESC LIMIT 1", (name,)
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        res["context"] = json.loads(res["context_json"])
        res["parameters"] = json.loads(res.get("parameters_json") or "{}")
        return res
    finally:
        conn.close()


def update_playbook_validation_status(
    playbook_id: int,
    validation_status: str,
    approved_by: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> bool:
    """Updates the validation status and optional approver of a playbook."""
    conn = get_connection(db_path)
    try:
        with conn:
            if approved_by:
                cur = conn.execute(
                    "UPDATE playbooks SET validation_status = ?, approved_by = ? WHERE playbook_id = ?",
                    (validation_status, approved_by, playbook_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE playbooks SET validation_status = ? WHERE playbook_id = ?",
                    (validation_status, playbook_id),
                )
            return cur.rowcount > 0
    finally:
        conn.close()


# ============================================================================
# QUERY HELPERS: APPROVALS (GOVERNANCE GATE)
# ============================================================================

def create_approval_request(
    incident_id: str,
    action_id: str,
    playbook_id: Optional[int] = None,
    reason: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> int:
    """Creates a new PENDING approval request for human-in-the-loop sign-off."""
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO approvals (incident_id, playbook_id, action_id, decision, reason)
                VALUES (?, ?, ?, 'PENDING', ?)
                """,
                (incident_id, playbook_id, action_id, reason),
            )
            return cur.lastrowid
    finally:
        conn.close()


def update_approval_decision(
    approval_id: int,
    decision: str,
    approved_by: str,
    reason: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> bool:
    """Updates approval decision to 'APPROVED', 'REJECTED', or 'CONSUMED'."""
    allowed_decisions = {"APPROVED", "REJECTED", "CONSUMED"}
    if decision not in allowed_decisions:
        raise ValueError(f"Invalid decision '{decision}'. Must be one of {sorted(allowed_decisions)}.")

    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                """
                UPDATE approvals
                SET decision = ?, approved_by = ?, reason = COALESCE(?, reason)
                WHERE approval_id = ?
                """,
                (decision, approved_by, reason, approval_id),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def get_approval(approval_id: int, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a single approval record by its primary key ID."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_approvals_for_incident(incident_id: str, db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """Retrieves all approvals associated with a specific incident ID."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE incident_id = ? ORDER BY created_at DESC",
            (incident_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending_approvals(db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """Retrieves all pending approval requests."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT a.*, i.fault_type, i.status AS incident_status
            FROM approvals a
            JOIN incidents i ON a.incident_id = i.incident_id
            WHERE a.decision = 'PENDING'
            ORDER BY a.created_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================================
# QUERY HELPERS: AUDIT LOG (IMMUTABILITY)
# ============================================================================

def log_audit_event(
    actor: str,
    action: str,
    incident_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> int:
    """Appends an event to the immutable audit log."""
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO audit_log (incident_id, actor, action, details_json)
                VALUES (?, ?, ?, ?)
                """,
                (incident_id, actor, action, json.dumps(details) if details else None),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_audit_logs(
    incident_id: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Retrieves audit logs, optionally filtered by incident."""
    conn = get_connection(db_path)
    try:
        if incident_id:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE incident_id = ? ORDER BY created_at DESC LIMIT ?",
                (incident_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            if d.get("details_json"):
                try:
                    d["details"] = json.loads(d["details_json"])
                except json.JSONDecodeError:
                    d["details"] = None
            results.append(d)
        return results
    finally:
        conn.close()


# ============================================================================
# QUERY HELPERS: RECOVERY HISTORY
# ============================================================================

def record_recovery_attempt(
    incident_id: str,
    action_id: str,
    outcome: str,
    playbook_id: Optional[int] = None,
    before_metrics: Optional[Dict[str, Any]] = None,
    after_metrics: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> int:
    """Records an executed recovery attempt and telemetry comparison."""
    conn = get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO recovery_history (
                    incident_id, playbook_id, action_id, outcome,
                    before_metrics_json, after_metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    playbook_id,
                    action_id,
                    outcome,
                    json.dumps(before_metrics) if before_metrics else None,
                    json.dumps(after_metrics) if after_metrics else None,
                ),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_recovery_history(
    incident_id: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Retrieves recovery attempts history."""
    conn = get_connection(db_path)
    try:
        if incident_id:
            rows = conn.execute(
                "SELECT * FROM recovery_history WHERE incident_id = ? ORDER BY created_at DESC LIMIT ?",
                (incident_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM recovery_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
