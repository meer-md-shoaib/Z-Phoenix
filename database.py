"""
Z-PHOENIX :: database.py
=======================
SQLite persistence layer for Z-PHOENIX.

  * incidents   - one row per detected (and eventually resolved) fault
  * playbooks   - versioned remediation playbooks keyed by trigger signature
  * audit_log   - immutable trail of every actor action and risk tier

Schema creation is idempotent (`CREATE TABLE IF NOT EXISTS`) and the library
is seeded on first boot with PB-001 (CPU runaway, v1, verified) and
PB-002 (Db2 lock contention, v1, verified).

Every operation opens a short-lived, thread/greenlet-safe connection so the
module is safe under both eventlet (green threads) and native threads.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import config

_WRITE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# connection helpers
# ---------------------------------------------------------------------------
@contextmanager
def connect():
    """Yield a committed, row-dict SQLite connection (safe from any thread)."""
    conn = sqlite3.connect(config.DB_PATH, timeout=config.DB_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fault_type        TEXT    NOT NULL,
    started_at        TEXT    NOT NULL,
    resolved_at       TEXT,
    telemetry_snapshot TEXT,
    diagnosis         TEXT,
    status            TEXT    NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS playbooks (
    id                 TEXT    PRIMARY KEY,
    name               TEXT    NOT NULL,
    version            INTEGER NOT NULL,
    trigger_signature  TEXT    NOT NULL,
    steps_json         TEXT    NOT NULL,
    validation_result  TEXT,
    status             TEXT    NOT NULL DEFAULT 'draft',
    approved_by        TEXT,
    created_at         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_playbooks_trigger ON playbooks (trigger_signature);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    actor        TEXT    NOT NULL,
    action       TEXT    NOT NULL,
    target       TEXT,
    risk_level   TEXT,
    details_json TEXT
);
"""


# ---------------------------------------------------------------------------
# seed data
# ---------------------------------------------------------------------------
SEED_PLAYBOOKS = [
    {
        "id": "PB-001",
        "name": "CPU Runaway Job Containment",
        "version": 1,
        "trigger_signature": "CPU_RUNAWAY",
        "validation_result": "PASS",
        "status": "verified",
        "approved_by": "platform.eng",
        "steps": [
            {
                "order": 1,
                "title": "Identify the top CPU consumer",
                "command": "ps -eo pid,pcpu,comm --sort=-pcpu | head -5",
                "rationale": "Read-only confirmation of the runaway PID.",
            },
            {
                "order": 2,
                "title": "Throttle CBM domain PROD",
                "command": "mod cbm -domain PROD -throttle 40",
                "rationale": "Soft-caps the runaway workload immediately.",
            },
            {
                "order": 3,
                "title": "Terminate the runaway unit of work",
                "command": "kill -9 <RUNAWAY_PID>",
                "rationale": "Removes the root cause, scoped to one PID.",
            },
            {
                "order": 4,
                "title": "Publish recovery marker",
                "command": "echo ZPHX-CPU-RECOVERY-OK",
                "rationale": "Deterministic success marker for the audit trail.",
            },
        ],
    },
    {
        "id": "PB-002",
        "name": "Db2 Lock Contention Clearing",
        "version": 1,
        "trigger_signature": "DB2_LOCK_CONTENTION",
        "validation_result": "PASS",
        "status": "verified",
        "approved_by": "platform.eng",
        "steps": [
            {
                "order": 1,
                "title": "Identify the blocking application",
                "command": "ps -eo pid,pcpu,comm --sort=-pcpu | head -5",
                "rationale": "Confirm the blocker before forcing anything.",
            },
            {
                "order": 2,
                "title": "Force blocked applications",
                "command": "db2 force application (ALL)",
                "rationale": "Releases the exclusive page lock immediately.",
            },
            {
                "order": 3,
                "title": "Terminate Db2 connections",
                "command": "db2 terminate",
                "rationale": "Clears the connection pool cleanly.",
            },
            {
                "order": 4,
                "title": "Raise LOCKTIMEOUT",
                "command": "db2 update db cfg for ZPHXDB using LOCKTIMEOUT 30",
                "rationale": "Prevents indefinite lock waiting on recurrence.",
            },
            {
                "order": 5,
                "title": "Restart Db2 member",
                "command": "systemctl restart db2units",
                "rationale": "Re-initialises the lock manager.",
            },
        ],
    },
]

SEED_AUDIT = [
    {
        "actor": "system",
        "action": "BOOT",
        "target": "z-phoenix",
        "risk_level": "LOW",
        "details": {"note": "Z-PHOENIX console booted", "port": config.PORT},
    },
    {
        "actor": "platform.eng",
        "action": "PLAYBOOK_SEEDED",
        "target": "PB-001 v1",
        "risk_level": "LOW",
        "details": {"trigger": "CPU_RUNAWAY", "status": "verified"},
    },
    {
        "actor": "platform.eng",
        "action": "PLAYBOOK_SEEDED",
        "target": "PB-002 v1",
        "risk_level": "LOW",
        "details": {"trigger": "DB2_LOCK_CONTENTION", "status": "verified"},
    },
]


def init_db() -> None:
    """Create the schema (idempotent) and seed the playbook library + audit."""
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
    for seed in SEED_PLAYBOOKS:
        if get_playbook(seed["id"]) is None:
            with _WRITE_LOCK, connect() as conn:
                conn.execute(
                    """
                    INSERT INTO playbooks
                        (id, name, version, trigger_signature, steps_json,
                         validation_result, status, approved_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        seed["id"],
                        seed["name"],
                        seed["version"],
                        seed["trigger_signature"],
                        json.dumps(seed["steps"]),
                        seed["validation_result"],
                        seed["status"],
                        seed["approved_by"],
                        _now(),
                    ),
                )
    if list_audit(limit=1) == []:
        for entry in SEED_AUDIT:
            log_audit(
                entry["actor"],
                entry["action"],
                entry["target"],
                entry["risk_level"],
                entry["details"],
                emit=False,
            )


# ---------------------------------------------------------------------------
# incidents
# ---------------------------------------------------------------------------
def insert_incident(fault_type: str, snapshot: Dict[str, Any] | str,
                    status: str = "ACTIVE") -> int:
    payload = snapshot if isinstance(snapshot, str) else json.dumps(snapshot)
    with _WRITE_LOCK, connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO incidents (fault_type, started_at, telemetry_snapshot, status)
            VALUES (?, ?, ?, ?)
            """,
            (fault_type, _now(), payload, status),
        )
        return int(cur.lastrowid)


def update_incident(incident_id: int, diagnosis: Optional[str] = None,
                    status: Optional[str] = None) -> None:
    sets, params = [], []
    if diagnosis is not None:
        sets.append("diagnosis = ?")
        params.append(diagnosis)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if not sets:
        return
    params.append(incident_id)
    with _WRITE_LOCK, connect() as conn:
        conn.execute(f"UPDATE incidents SET {', '.join(sets)} WHERE id = ?", params)


def resolve_incident(incident_id: int, diagnosis: Optional[str] = None) -> None:
    update_incident(incident_id, diagnosis=diagnosis, status="RESOLVED")
    with _WRITE_LOCK, connect() as conn:
        conn.execute(
            "UPDATE incidents SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
            (_now(), incident_id),
        )


def get_incident(incident_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(
            conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        )


def get_active_incident() -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(
            conn.execute(
                "SELECT * FROM incidents WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        )


def has_resolved_incident(trigger_signature: str) -> bool:
    """True once this fault has been fully recovered before (enables fast replay)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM incidents WHERE fault_type = ? AND status = 'RESOLVED' LIMIT 1",
            (trigger_signature,),
        ).fetchone()
    return row is not None


def list_incidents(limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# playbooks
# ---------------------------------------------------------------------------
def _next_playbook_id(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT id FROM playbooks").fetchall()
    highest = 0
    for row in rows:
        raw = (row["id"] or "").replace("PB-", "").strip()
        if raw.isdigit():
            highest = max(highest, int(raw))
    return f"PB-{highest + 1:03d}"


def save_playbook(name: str, trigger_signature: str, steps: List[Dict[str, Any]],
                  validation_result: str, approved_by: str,
                  status: str = "verified") -> Dict[str, Any]:
    """
    Store a playbook. If a playbook already exists for this trigger signature the
    row is replaced with version v(N+1); otherwise a new PB-xxx is created.
    The previous version remains traceable through the audit_log.
    """
    with _WRITE_LOCK, connect() as conn:
        existing = conn.execute(
            "SELECT * FROM playbooks WHERE trigger_signature = ? ORDER BY version DESC LIMIT 1",
            (trigger_signature,),
        ).fetchone()
        if existing is not None:
            playbook_id = existing["id"]
            version = int(existing["version"]) + 1
            name = name or existing["name"]
        else:
            playbook_id = _next_playbook_id(conn)
            version = 1
            name = name or f"{trigger_signature} auto-remediation"
        steps_json = json.dumps(
            [dict(step, order=i + 1) for i, step in enumerate(steps or [])]
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO playbooks
                (id, name, version, trigger_signature, steps_json,
                 validation_result, status, approved_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playbook_id,
                name,
                version,
                trigger_signature,
                steps_json,
                validation_result,
                status,
                approved_by,
                _now(),
            ),
        )
    return {"id": playbook_id, "version": version, "name": name}


def get_playbook(playbook_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(
            conn.execute("SELECT * FROM playbooks WHERE id = ?", (playbook_id,)).fetchone()
        )


def find_playbook_by_trigger(trigger_signature: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        return _row_to_dict(
            conn.execute(
                "SELECT * FROM playbooks WHERE trigger_signature = ? "
                "ORDER BY version DESC LIMIT 1",
                (trigger_signature,),
            ).fetchone()
        )


def list_playbooks() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM playbooks ORDER BY trigger_signature, version DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# audit log
# ---------------------------------------------------------------------------
def log_audit(actor: str, action: str, target: str = "", risk_level: str = "LOW",
              details: Optional[Dict[str, Any]] = None,
              emit: bool = True) -> Dict[str, Any]:
    """Append an immutable audit row; optionally push it to live dashboards."""
    details_json = json.dumps(details or {})
    with _WRITE_LOCK, connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO audit_log (ts, actor, action, target, risk_level, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_now(), actor, action, target, risk_level, details_json),
        )
        row_id = int(cur.lastrowid)
    entry = {
        "id": row_id,
        "ts": _now(),
        "actor": actor,
        "action": action,
        "target": target,
        "risk_level": risk_level,
        "details_json": details_json,
    }
    if emit:
        _emit_audit(entry)
    return entry


_AUDIT_SINK: Optional[Any] = None


def set_audit_sink(fn) -> None:
    """Register a callback invoked with every new audit row (socket push)."""
    global _AUDIT_SINK
    _AUDIT_SINK = fn


def _emit_audit(entry: Dict[str, Any]) -> None:
    if _AUDIT_SINK is None:
        return
    try:
        _AUDIT_SINK(entry)
    except Exception:
        # The audit trail is never allowed to break the control plane.
        pass


def list_audit(limit: int = 200) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
