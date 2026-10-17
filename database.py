"""
Z-PHOENIX Database Module
Unified SQLite persistence layer supporting:
- Core runtime: incidents, versioned playbooks, immutable audit log
- Governance & verification: approvals, recovery history, execution metrics
- Synthetic demonstration seed data and schema migrations

Every operation opens a thread/greenlet-safe connection so the module is
safe under eventlet (green threads) and native threads.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import config
    _DEFAULT_DB_PATH = Path(getattr(config, "DB_PATH", "z_phoenix.db"))
    _DB_TIMEOUT = getattr(config, "DB_TIMEOUT_SEC", 5.0)
except ImportError:
    _DEFAULT_DB_PATH = Path(__file__).resolve().parent / "z_phoenix.db"
    _DB_TIMEOUT = 5.0

DB_PATH = _DEFAULT_DB_PATH
_WRITE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# connection helpers
# ---------------------------------------------------------------------------
def get_connection(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    """Connects to SQLite database and enables foreign keys and row factory."""
    target = db_path if db_path is not None else DB_PATH
    conn = sqlite3.connect(target, timeout=_DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def connect(db_path: Optional[Path | str] = None):
    """Yield a committed, row-dict SQLite connection (safe from any thread)."""
    target = db_path if db_path is not None else DB_PATH
    conn = sqlite3.connect(target, timeout=_DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id           TEXT    UNIQUE,
    fault_type            TEXT    NOT NULL,
    status                TEXT    NOT NULL DEFAULT 'ACTIVE',
    started_at            TEXT,
    resolved_at           TEXT,
    telemetry_snapshot    TEXT,
    diagnosis             TEXT,
    baseline_metrics_json TEXT,
    chaos_metrics_json    TEXT,
    recovered_metrics_json TEXT,
    diagnosis_json        TEXT,
    created_at            TEXT    DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS playbooks (
    playbook_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    id                 TEXT    UNIQUE,
    name               TEXT    NOT NULL,
    description        TEXT    DEFAULT '',
    version            INTEGER NOT NULL,
    trigger_signature  TEXT    DEFAULT '',
    action_id          TEXT    DEFAULT '',
    steps_json         TEXT    DEFAULT '[]',
    context_json       TEXT    DEFAULT '{}',
    parameters_json    TEXT    DEFAULT '{}',
    validation_result  TEXT    DEFAULT '',
    validation_status  TEXT    DEFAULT 'PENDING',
    status             TEXT    NOT NULL DEFAULT 'draft',
    approved_by        TEXT,
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_playbooks_trigger ON playbooks (trigger_signature);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents (status);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    playbook_id INTEGER,
    action_id   TEXT NOT NULL,
    decision    TEXT NOT NULL,
    approved_by TEXT,
    reason      TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (incident_id) REFERENCES incidents(incident_id) ON DELETE CASCADE,
    FOREIGN KEY (playbook_id) REFERENCES playbooks(playbook_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_approvals_incident ON approvals (incident_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER,
    ts           TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    target       TEXT,
    risk_level   TEXT,
    incident_id  TEXT,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS recovery_history (
    recovery_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id         TEXT NOT NULL,
    playbook_id         INTEGER,
    action_id           TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    before_metrics_json TEXT,
    after_metrics_json  TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (incident_id) REFERENCES incidents(incident_id) ON DELETE CASCADE,
    FOREIGN KEY (playbook_id) REFERENCES playbooks(playbook_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_recovery_incident ON recovery_history (incident_id);
"""


# ---------------------------------------------------------------------------
# seed data (standard runtime playbooks)
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
        "approved_by": "data.platform",
        "steps": [
            {
                "order": 1,
                "title": "Display contending threads on DSNDB01",
                "command": "display db(DSNDB01) spacenam(*) lock",
                "rationale": "Read-only diagnosis of the lock-graph head.",
            },
            {
                "order": 2,
                "title": "Cancel the blocking thread",
                "command": "cancel thread(<BLOCKING_TOKEN>)",
                "rationale": "Breaks the lock contention without restarting the subsystem.",
            },
            {
                "order": 3,
                "title": "Publish recovery marker",
                "command": "echo ZPHX-DB2-RECOVERY-OK",
                "rationale": "Deterministic success marker for the audit trail.",
            },
        ],
    },
]


def init_db(db_path: Optional[Path | str] = None, seed: Optional[bool] = None) -> None:
    """Initializes SQLite schema, runs non-destructive migrations, and seeds playbooks."""
    should_seed = seed if seed is not None else (db_path is None)
    with _WRITE_LOCK, connect(db_path) as conn:
        conn.executescript(SCHEMA)

        # Non-destructive schema migration for pre-existing databases
        incident_cols = {row["name"] for row in conn.execute("PRAGMA table_info(incidents)").fetchall()}
        if "incident_id" not in incident_cols:
            conn.execute("ALTER TABLE incidents ADD COLUMN incident_id TEXT")
        if "baseline_metrics_json" not in incident_cols:
            conn.execute("ALTER TABLE incidents ADD COLUMN baseline_metrics_json TEXT")
        if "chaos_metrics_json" not in incident_cols:
            conn.execute("ALTER TABLE incidents ADD COLUMN chaos_metrics_json TEXT")
        if "recovered_metrics_json" not in incident_cols:
            conn.execute("ALTER TABLE incidents ADD COLUMN recovered_metrics_json TEXT")
        if "diagnosis_json" not in incident_cols:
            conn.execute("ALTER TABLE incidents ADD COLUMN diagnosis_json TEXT")

        playbook_cols = {row["name"] for row in conn.execute("PRAGMA table_info(playbooks)").fetchall()}
        if "playbook_id" not in playbook_cols:
            conn.execute("ALTER TABLE playbooks ADD COLUMN playbook_id INTEGER")
            conn.execute("UPDATE playbooks SET playbook_id = rowid WHERE playbook_id IS NULL")
        if "description" not in playbook_cols:
            conn.execute("ALTER TABLE playbooks ADD COLUMN description TEXT DEFAULT ''")
        if "parameters_json" not in playbook_cols:
            conn.execute("ALTER TABLE playbooks ADD COLUMN parameters_json TEXT DEFAULT '{}'")
        if "action_id" not in playbook_cols:
            conn.execute("ALTER TABLE playbooks ADD COLUMN action_id TEXT DEFAULT ''")
        if "context_json" not in playbook_cols:
            conn.execute("ALTER TABLE playbooks ADD COLUMN context_json TEXT DEFAULT '{}'")
        if "validation_status" not in playbook_cols:
            conn.execute("ALTER TABLE playbooks ADD COLUMN validation_status TEXT DEFAULT 'PENDING'")

        approval_cols = {row["name"] for row in conn.execute("PRAGMA table_info(approvals)").fetchall()}
        if "reason" not in approval_cols:
            conn.execute("ALTER TABLE approvals ADD COLUMN reason TEXT")

        audit_cols = {row["name"] for row in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
        if "incident_id" not in audit_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN incident_id TEXT")
        if "event_id" not in audit_cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN event_id INTEGER")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_playbooks_action ON playbooks (action_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_incident ON audit_log (incident_id)")

        if should_seed:
            for p in SEED_PLAYBOOKS:
                existing = conn.execute(
                    "SELECT 1 FROM playbooks WHERE id = ?", (p["id"],)
                ).fetchone()
                if existing is None:
                    steps_json = json.dumps(p["steps"])
                    conn.execute(
                        """
                        INSERT INTO playbooks
                            (id, name, version, trigger_signature, steps_json,
                             validation_result, status, approved_by, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            p["id"],
                            p["name"],
                            p["version"],
                            p["trigger_signature"],
                            steps_json,
                            p.get("validation_result"),
                            p.get("status", "verified"),
                            p.get("approved_by"),
                            _now(),
                        ),
                    )


# ---------------------------------------------------------------------------
# incident queries
# ---------------------------------------------------------------------------
def create_incident(
    fault_type: str = "",
    telemetry: Optional[Dict[str, Any]] = None,
    diagnosis: Optional[Any] = None,
    *,
    incident_id: Optional[str] = None,
    status: Optional[str] = None,
    baseline_metrics: Optional[Dict[str, Any]] = None,
    chaos_metrics: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Any:
    """Inserts a new incident record, compatible with both core app and demo seed data."""
    # Detect if called with (incident_id, fault_type, ...)
    if incident_id is None and fault_type and ("-" in fault_type or fault_type.startswith("INC")):
        # First positional was incident_id
        incident_id = fault_type
        fault_type = str(telemetry or "")
        telemetry = None

    eff_status = status or "ACTIVE"
    now_ts = _now()
    telemetry_json = json.dumps(telemetry) if telemetry else None
    diagnosis_str = json.dumps(diagnosis) if isinstance(diagnosis, (dict, list)) else (str(diagnosis) if diagnosis else None)
    base_m_json = json.dumps(baseline_metrics) if baseline_metrics else None
    chaos_m_json = json.dumps(chaos_metrics) if chaos_metrics else None

    with _WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO incidents
                (incident_id, fault_type, status, started_at, telemetry_snapshot,
                 diagnosis, baseline_metrics_json, chaos_metrics_json, diagnosis_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                fault_type,
                eff_status,
                now_ts,
                telemetry_json,
                diagnosis_str,
                base_m_json,
                chaos_m_json,
                diagnosis_str,
            ),
        )
        row_id = cur.lastrowid
        if not incident_id:
            auto_inc_id = f"INC-{row_id:04d}"
            conn.execute("UPDATE incidents SET incident_id = ? WHERE id = ?", (auto_inc_id, row_id))
            return row_id
        return incident_id


def update_incident(
    incident_id: Any,
    status: Optional[str] = None,
    diagnosis: Optional[Any] = None,
    telemetry: Optional[Dict[str, Any]] = None,
    resolved: bool = False,
    db_path: Optional[Path | str] = None,
) -> None:
    """Updates status/diagnosis for an incident."""
    now_ts = _now() if resolved else None
    telemetry_json = json.dumps(telemetry) if telemetry else None
    diagnosis_str = json.dumps(diagnosis) if isinstance(diagnosis, (dict, list)) else (str(diagnosis) if diagnosis else None)

    with _WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            """
            UPDATE incidents
               SET status = COALESCE(?, status),
                   diagnosis = COALESCE(?, diagnosis),
                   diagnosis_json = COALESCE(?, diagnosis_json),
                   resolved_at = CASE WHEN ? THEN ? ELSE resolved_at END,
                   telemetry_snapshot = COALESCE(?, telemetry_snapshot)
             WHERE id = ? OR incident_id = ?
            """,
            (status, diagnosis_str, diagnosis_str, resolved, now_ts, telemetry_json, incident_id, str(incident_id)),
        )


def update_incident_status(
    incident_id: str,
    status: str,
    recovered_metrics: Optional[Dict[str, Any]] = None,
    diagnosis: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> bool:
    """Updates status and optional metrics of an incident (Member 3 helper)."""
    with _WRITE_LOCK, connect(db_path) as conn:
        updates = ["status = ?"]
        params: List[Any] = [status]
        if recovered_metrics is not None:
            updates.append("recovered_metrics_json = ?")
            params.append(json.dumps(recovered_metrics))
        if diagnosis is not None:
            diag_str = json.dumps(diagnosis)
            updates.append("diagnosis = ?")
            updates.append("diagnosis_json = ?")
            params.extend([diag_str, diag_str])
        params.extend([incident_id, incident_id])
        sql = f"UPDATE incidents SET {', '.join(updates)} WHERE incident_id = ? OR id = ?"
        cur = conn.execute(sql, params)
        return cur.rowcount > 0


def get_incident(incident_id: Any, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    """Fetches an incident by ID or incident_id."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE id = ? OR incident_id = ? LIMIT 1",
            (incident_id, str(incident_id)),
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        for key in ["baseline_metrics_json", "chaos_metrics_json", "recovered_metrics_json", "diagnosis_json"]:
            if res.get(key):
                try:
                    res[key.replace("_json", "")] = json.loads(res[key])
                except Exception:
                    res[key.replace("_json", "")] = None
        return res


def list_incidents(limit: int = 50, db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """Returns most recent incidents."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            for key in ["baseline_metrics_json", "chaos_metrics_json", "recovered_metrics_json", "diagnosis_json"]:
                if d.get(key):
                    try:
                        d[key.replace("_json", "")] = json.loads(d[key])
                    except Exception:
                        d[key.replace("_json", "")] = None
            results.append(d)
        return results


# ---------------------------------------------------------------------------
# playbook queries
# ---------------------------------------------------------------------------
def _next_playbook_id(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT id FROM playbooks WHERE id LIKE 'PB-%'").fetchall()
    max_num = 0
    for r in rows:
        val = r["id"] or ""
        try:
            num = int(val.split("-")[1])
            if num > max_num:
                max_num = num
        except (IndexError, ValueError):
            continue
    return f"PB-{max_num + 1:03d}"


def create_playbook(
    name: Optional[str] = None,
    version: Optional[int] = None,
    action_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    validation_status: str = "PENDING",
    approved_by: Optional[str] = None,
    description: str = "",
    db_path: Optional[Path | str] = None,
    *,
    playbook_id: Optional[str] = None,
    trigger_signature: Optional[str] = None,
    steps: Optional[List[Dict[str, Any]]] = None,
    validation_result: Optional[str] = None,
    status: Optional[str] = None,
) -> int:
    """Inserts a new versioned playbook record."""
    with _WRITE_LOCK, connect(db_path) as conn:
        # Resolve ID
        id_str = playbook_id
        if not id_str:
            id_str = _next_playbook_id(conn)

        p_name = name or "Unnamed Playbook"
        p_version = version or 1
        p_trigger = trigger_signature or action_id or ""
        p_action = action_id or trigger_signature or ""
        p_steps = json.dumps(steps or [])
        p_context = json.dumps(context or {})
        p_params = json.dumps(parameters or {})
        p_val_res = validation_result or validation_status or "PASS"
        p_val_stat = validation_status or validation_result or "VALIDATED"
        p_stat = status or "verified"

        cur = conn.execute(
            """
            INSERT INTO playbooks
                (id, name, description, version, trigger_signature, action_id,
                 steps_json, context_json, parameters_json, validation_result,
                 validation_status, status, approved_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id_str,
                p_name,
                description or "",
                p_version,
                p_trigger,
                p_action,
                p_steps,
                p_context,
                p_params,
                p_val_res,
                p_val_stat,
                p_stat,
                approved_by,
                _now(),
            ),
        )
        return cur.lastrowid


def save_playbook(
    name: str,
    trigger_signature: str,
    steps: List[Dict[str, Any]],
    validation_result: str,
    approved_by: str,
    status: str = "verified",
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Store a playbook v(N+1)."""
    with _WRITE_LOCK, connect(db_path) as conn:
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
                 validation_result, validation_status, status, approved_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playbook_id,
                name,
                version,
                trigger_signature,
                steps_json,
                validation_result,
                "VALIDATED" if validation_result == "PASS" else "PENDING",
                status,
                approved_by,
                _now(),
            ),
        )
        return {
            "id": playbook_id,
            "name": name,
            "version": version,
            "trigger_signature": trigger_signature,
            "steps": steps,
            "validation_result": validation_result,
            "status": status,
            "approved_by": approved_by,
        }


def get_playbook(playbook_id: Any, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a playbook by its primary key ID (text 'PB-xxx' or integer playbook_id)."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM playbooks WHERE id = ? OR playbook_id = ? LIMIT 1",
            (str(playbook_id), playbook_id),
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["steps"] = json.loads(res.get("steps_json") or "[]")
        except Exception:
            res["steps"] = []
        try:
            res["context"] = json.loads(res.get("context_json") or "{}")
        except Exception:
            res["context"] = {}
        try:
            res["parameters"] = json.loads(res.get("parameters_json") or "{}")
        except Exception:
            res["parameters"] = {}
        return res


def list_playbooks(
    validation_status: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Lists playbooks, optionally filtered by validation status."""
    with connect(db_path) as conn:
        if validation_status:
            rows = conn.execute(
                "SELECT * FROM playbooks WHERE validation_status = ? OR validation_result = ? ORDER BY name, version DESC",
                (validation_status, validation_status),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM playbooks ORDER BY name, version DESC").fetchall()

        results = []
        for r in rows:
            d = dict(r)
            try:
                d["steps"] = json.loads(d.get("steps_json") or "[]")
            except Exception:
                d["steps"] = []
            try:
                d["context"] = json.loads(d.get("context_json") or "{}")
            except Exception:
                d["context"] = {}
            try:
                d["parameters"] = json.loads(d.get("parameters_json") or "{}")
            except Exception:
                d["parameters"] = {}
            results.append(d)
        return results


def get_playbook_by_name_version(
    name: str,
    version: int,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieves a specific playbook by its name and version."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM playbooks WHERE name = ? AND version = ? LIMIT 1",
            (name, version),
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["context"] = json.loads(res.get("context_json") or "{}")
        except Exception:
            res["context"] = {}
        try:
            res["parameters"] = json.loads(res.get("parameters_json") or "{}")
        except Exception:
            res["parameters"] = {}
        try:
            res["steps"] = json.loads(res.get("steps_json") or "[]")
        except Exception:
            res["steps"] = []
        return res


def get_latest_playbook_version(
    name: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieves the latest version of a playbook by name."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM playbooks WHERE name = ? ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["context"] = json.loads(res.get("context_json") or "{}")
        except Exception:
            res["context"] = {}
        try:
            res["parameters"] = json.loads(res.get("parameters_json") or "{}")
        except Exception:
            res["parameters"] = {}
        try:
            res["steps"] = json.loads(res.get("steps_json") or "[]")
        except Exception:
            res["steps"] = []
        return res


def update_playbook_validation_status(
    playbook_id: Any,
    validation_status: str,
    approved_by: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> bool:
    """Updates validation status and optional approver."""
    with _WRITE_LOCK, connect(db_path) as conn:
        if approved_by:
            cur = conn.execute(
                "UPDATE playbooks SET validation_status = ?, validation_result = ?, approved_by = ? WHERE playbook_id = ? OR id = ?",
                (validation_status, validation_status, approved_by, playbook_id, str(playbook_id)),
            )
        else:
            cur = conn.execute(
                "UPDATE playbooks SET validation_status = ?, validation_result = ? WHERE playbook_id = ? OR id = ?",
                (validation_status, validation_status, playbook_id, str(playbook_id)),
            )
        return cur.rowcount > 0


def match_playbook(trigger_signature: str, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    """Fast-replay lookup: returns highest verified version matching the trigger."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM playbooks
             WHERE (trigger_signature = ? OR action_id = ?)
               AND (status = 'verified' OR validation_status = 'VALIDATED')
               AND (validation_result = 'PASS' OR validation_status = 'VALIDATED')
             ORDER BY version DESC
             LIMIT 1
            """,
            (trigger_signature, trigger_signature),
        ).fetchone()
        if row is None:
            return None
        res = dict(row)
        try:
            res["steps"] = json.loads(res.get("steps_json") or "[]")
        except Exception:
            res["steps"] = []
        return res


def update_pending_validation(
    trigger_signature: str,
    validation_result: str,
    db_path: Optional[Path | str] = None,
) -> None:
    """Propagate sandbox validation verdict to the latest playbook for a trigger."""
    with _WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            """
            UPDATE playbooks
               SET validation_result = ?,
                   validation_status = ?
             WHERE id = (
                 SELECT id FROM playbooks
                  WHERE trigger_signature = ? OR action_id = ?
                  ORDER BY version DESC LIMIT 1
             )
            """,
            (
                validation_result,
                "VALIDATED" if validation_result == "PASS" else "PENDING",
                trigger_signature,
                trigger_signature,
            ),
        )


# ---------------------------------------------------------------------------
# approvals queries (Human Approval Gate)
# ---------------------------------------------------------------------------
def create_approval_request(
    incident_id: str,
    action_id: str,
    playbook_id: Optional[int] = None,
    reason: Optional[str] = None,
    requested_by: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> int:
    """Creates a new PENDING approval request for human-in-the-loop sign-off."""
    with _WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO approvals (incident_id, playbook_id, action_id, decision, reason)
            VALUES (?, ?, ?, 'PENDING', ?)
            """,
            (incident_id, playbook_id, action_id, reason),
        )
        return cur.lastrowid


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

    with _WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE approvals
            SET decision = ?, approved_by = ?, reason = COALESCE(?, reason)
            WHERE approval_id = ?
            """,
            (decision, approved_by, reason, approval_id),
        )
        return cur.rowcount > 0


def get_approval(approval_id: int, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves an approval record by ID."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        return dict(row) if row else None


def get_approvals_for_incident(incident_id: str, db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """Retrieves all approvals associated with an incident ID."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE incident_id = ? ORDER BY created_at DESC",
            (incident_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_approvals(db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """Retrieves all pending approval requests."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.*, i.fault_type, i.status AS incident_status
            FROM approvals a
            JOIN incidents i ON (a.incident_id = i.incident_id OR a.incident_id = CAST(i.id AS TEXT))
            WHERE a.decision = 'PENDING'
            ORDER BY a.created_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# audit log queries
# ---------------------------------------------------------------------------
_AUDIT_SINK = None


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
        pass


def log_audit(
    actor: str,
    action: str,
    target: str = "",
    risk_level: str = "LOW",
    details: Optional[Dict[str, Any]] = None,
    emit: bool = True,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Appends an event to the immutable audit log; optionally pushes to live socket."""
    ts = _now()
    details_json = json.dumps(details or {})
    with _WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO audit_log
                (ts, actor, action, target, risk_level, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ts, actor, action, target, risk_level, details_json),
        )
        row_id = int(cur.lastrowid)

    entry = {
        "id": row_id,
        "ts": ts,
        "actor": actor,
        "action": action,
        "target": target,
        "risk_level": risk_level,
        "details_json": details_json,
    }
    if emit:
        _emit_audit(entry)
    return entry


def log_audit_event(
    actor: str,
    action: str,
    incident_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> int:
    """Appends an event to the audit log (Member 3 signature)."""
    ts = _now()
    details_json = json.dumps(details or {})
    with _WRITE_LOCK, connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO audit_log
                (ts, incident_id, actor, action, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ts, incident_id, actor, action, details_json),
        )
        return cur.lastrowid


def list_audit(limit: int = 200, db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """Returns most recent audit events."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("details_json"):
                try:
                    d["details"] = json.loads(d["details_json"])
                except Exception:
                    d["details"] = None
            results.append(d)
        return results


def get_audit_logs(
    incident_id: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Retrieves audit logs, optionally filtered by incident."""
    with connect(db_path) as conn:
        if incident_id:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE incident_id = ? ORDER BY id DESC LIMIT ?",
                (incident_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            if d.get("details_json"):
                try:
                    d["details"] = json.loads(d["details_json"])
                except Exception:
                    d["details"] = None
            results.append(d)
        return results


# ---------------------------------------------------------------------------
# recovery history queries
# ---------------------------------------------------------------------------
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
    with _WRITE_LOCK, connect(db_path) as conn:
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


def get_recovery_history(
    incident_id: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Retrieves recovery attempts history."""
    with connect(db_path) as conn:
        if incident_id:
            rows = conn.execute(
                "SELECT * FROM recovery_history WHERE incident_id = ? ORDER BY recovery_id DESC LIMIT ?",
                (incident_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM recovery_history ORDER BY recovery_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print("Z-Phoenix database initialized and verified.")
