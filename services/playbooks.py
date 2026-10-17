"""
Z-PHOENIX Playbooks Service
Handles:
1. Versioned recovery playbook storage and retrieval (v1, v2, ...).
2. Context-aware playbook matching based on:
   - Subsystem (e.g. CICS, Db2, z/OS, Network)
   - Fault Type (e.g. cpu_burn, db2_thread_leak, cics_socket_exhaustion)
   - Telemetry Anomaly Conditions (e.g. cpu_pct >= 85, active_threads >= 100)
3. Deterministic scoring (0 to 100) with explainable match reasons.
4. Safe fallback (NO_MATCH) when similarity is insufficient.

CRITICAL SAFETY RULES:
- Match score is a similarity metric ONLY, NOT proof of safety.
- A high score DOES NOT bypass validation, governance or human approval.
- This service does NOT execute recovery commands.
"""

import operator
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from database import (
        create_playbook,
        get_latest_playbook_version,
        get_playbook,
        get_playbook_by_name_version,
        list_playbooks,
        log_audit_event,
        update_playbook_validation_status,
    )
except ImportError:
    from ..database import (
        create_playbook,
        get_latest_playbook_version,
        get_playbook,
        get_playbook_by_name_version,
        list_playbooks,
        log_audit_event,
        update_playbook_validation_status,
    )


# ============================================================================
# CUSTOM EXCEPTIONS
# ============================================================================

class PlaybookError(Exception):
    """Base exception for playbook service."""
    pass


class DuplicatePlaybookVersionError(PlaybookError):
    """Raised when an attempt is made to overwrite an existing version."""
    pass


class InvalidPlaybookError(PlaybookError):
    """Raised when playbook data fails schema or contract validation."""
    pass


# Supported comparison operators for telemetry conditions
COMPARISON_OPERATORS = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}


# ============================================================================
# PLAYBOOK REPOSITORY & VERSIONING
# ============================================================================

def save_recovery_playbook(
    name: str,
    action_id: str,
    context: Dict[str, Any],
    parameters: Optional[Dict[str, Any]] = None,
    description: str = "",
    version: Optional[int] = None,
    validation_status: str = "VALIDATED",
    approved_by: Optional[str] = "ADMIN",
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Saves a versioned recovery playbook into the database.
    
    If version is None, it automatically increments from the latest version:
      - If no versions exist yet for `name`: version 1 is assigned.
      - If version 1 exists: version 2 is assigned, etc.
    If a specific version is provided and already exists, DuplicatePlaybookVersionError is raised.
    """
    if not isinstance(name, str) or not name.strip():
        raise InvalidPlaybookError("Playbook 'name' must be a non-empty string.")

    if not isinstance(action_id, str) or not action_id.strip():
        raise InvalidPlaybookError("Playbook 'action_id' must be a non-empty string.")

    if not isinstance(context, dict):
        raise InvalidPlaybookError("Playbook 'context' must be a valid dictionary.")

    valid_statuses = {"VALIDATED", "PENDING", "DEPRECATED"}
    if validation_status not in valid_statuses:
        raise InvalidPlaybookError(
            f"Invalid validation_status '{validation_status}'. Allowed: {sorted(valid_statuses)}"
        )

    clean_name = name.strip()
    latest_existing = get_latest_playbook_version(clean_name, db_path=db_path)

    if version is None:
        assigned_version = (latest_existing["version"] + 1) if latest_existing else 1
    else:
        if not isinstance(version, int) or version < 1:
            raise InvalidPlaybookError("Playbook 'version' must be an integer >= 1.")
        
        # Check if version already exists
        existing_version = get_playbook_by_name_version(clean_name, version, db_path=db_path)
        if existing_version is not None:
            raise DuplicatePlaybookVersionError(
                f"Playbook '{clean_name}' version {version} already exists. "
                f"Playbook versions are immutable; use a higher version number."
            )
        assigned_version = version

    playbook_id = create_playbook(
        name=clean_name,
        description=description.strip(),
        version=assigned_version,
        action_id=action_id.strip(),
        context=context,
        parameters=parameters or {},
        validation_status=validation_status,
        approved_by=approved_by,
        db_path=db_path,
    )

    # Record in immutable audit log
    log_audit_event(
        actor=approved_by or "SYSTEM",
        action="PLAYBOOK_REGISTERED",
        details={
            "playbook_id": playbook_id,
            "name": clean_name,
            "version": assigned_version,
            "action_id": action_id.strip(),
            "validation_status": validation_status,
        },
        db_path=db_path,
    )

    return {
        "playbook_id": playbook_id,
        "name": clean_name,
        "version": assigned_version,
        "action_id": action_id.strip(),
        "validation_status": validation_status,
        "description": description.strip(),
    }


def get_playbook_by_version(
    name: str,
    version: int,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieves an exact version of a playbook by name and version number."""
    return get_playbook_by_name_version(name.strip(), version, db_path=db_path)


def get_latest_playbook(
    name: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieves the newest version of a playbook by name."""
    return get_latest_playbook_version(name.strip(), db_path=db_path)


def list_all_playbooks(
    include_deprecated: bool = False,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Lists all playbooks, optionally omitting deprecated versions."""
    all_pbs = list_playbooks(db_path=db_path)
    if not include_deprecated:
        return [p for p in all_pbs if p.get("validation_status") != "DEPRECATED"]
    return all_pbs


def deprecate_playbook_version(
    name: str,
    version: int,
    deprecated_by: str = "ADMIN",
    db_path: Optional[Path | str] = None,
) -> bool:
    """Marks an older version of a playbook as DEPRECATED so it won't match in production."""
    pb = get_playbook_by_version(name, version, db_path=db_path)
    if not pb:
        return False

    success = update_playbook_validation_status(
        playbook_id=pb["playbook_id"],
        validation_status="DEPRECATED",
        approved_by=deprecated_by,
        db_path=db_path,
    )
    if success:
        log_audit_event(
            actor=deprecated_by,
            action="PLAYBOOK_DEPRECATED",
            details={"playbook_id": pb["playbook_id"], "name": name, "version": version},
            db_path=db_path,
        )
    return success


# ============================================================================
# DETERMINISTIC CONTEXT MATCHING & SCORING ENGINE
# ============================================================================

def _evaluate_condition(actual_val: Any, op_str: str, target_val: Any) -> bool:
    """Safely evaluates numeric/string comparison operators."""
    try:
        op_func = COMPARISON_OPERATORS.get(op_str)
        if op_func is None:
            return False
        return op_func(float(actual_val), float(target_val))
    except (ValueError, TypeError):
        return False


def calculate_match_score(
    playbook: Dict[str, Any],
    incident_context: Dict[str, Any],
) -> Tuple[int, List[str]]:
    """
    Calculates a deterministic context match score (0 to 100) between a playbook and an incident.

    Scoring Breakdown (Total Max = 100 points):
    1. Subsystem Alignment (Max 35 points):
       - Exact match on subsystem (e.g. 'CICS' == 'CICS'): +35 pts
       - Generic / Wildcard subsystem ('*' or 'ALL' or undefined): +20 pts
       - Explicit conflict (e.g. playbook targets 'Db2' but incident is 'CICS'): 0 pts

    2. Fault Type Alignment (Max 40 points):
       - Exact fault_type match: +40 pts
       - Substring / Partial fault match (e.g. 'cpu' in 'cpu_burn'): +20 pts
       - Generic / Wildcard ('*' or 'ALL' or undefined): +15 pts
       - Unrelated fault: 0 pts

    3. Telemetry Anomaly Verification (Max 25 points):
       - Telemetry condition checks (e.g. cpu_pct >= 80, active_threads >= 100):
         Pro-rated based on the ratio of passed conditions: (passed / total) * 25 pts
       - If no specific telemetry conditions are defined (general recovery): +15 pts
       - If telemetry conditions exist but 0 pass: 0 pts

    Returns:
        (score: int, match_reasons: List[str])
    """
    score = 0
    reasons: List[str] = []

    pb_context = playbook.get("context", {})
    if not isinstance(pb_context, dict):
        return 0, ["Playbook context format is invalid."]

    incident_subsystem = (
        incident_context.get("subsystem")
        or incident_context.get("affected_subsystem")
        or ""
    ).strip().upper()

    incident_fault = (
        incident_context.get("fault_type")
        or ""
    ).strip().lower()

    incident_metrics = (
        incident_context.get("metrics")
        or incident_context.get("telemetry")
        or incident_context.get("chaos_metrics")
        or {}
    )

    # ------------------------------------------------------------------------
    # 1. SUBSYSTEM MATCH (Max 35 pts)
    # ------------------------------------------------------------------------
    pb_subsystem = (
        pb_context.get("target_subsystem")
        or pb_context.get("subsystem")
        or "*"
    ).strip().upper()

    if pb_subsystem in {"*", "ALL"}:
        score += 20
        reasons.append("Generic subsystem support ('*') (+20)")
    elif incident_subsystem and pb_subsystem == incident_subsystem:
        score += 35
        reasons.append(f"Exact subsystem match on '{incident_subsystem}' (+35)")
    elif not incident_subsystem:
        # Subsystem unspecified in incident
        score += 10
        reasons.append(f"Playbook specifies '{pb_subsystem}', incident subsystem unspecified (+10)")
    else:
        # Conflict: Playbook is for CICS, incident is Db2
        reasons.append(
            f"Subsystem conflict: Playbook targets '{pb_subsystem}' but incident is '{incident_subsystem}' (+0)"
        )

    # ------------------------------------------------------------------------
    # 2. FAULT TYPE MATCH (Max 40 pts)
    # ------------------------------------------------------------------------
    supported_faults = pb_context.get("supported_fault_types") or pb_context.get("fault_type") or "*"
    if isinstance(supported_faults, str):
        supported_fault_list = [f.strip().lower() for f in supported_faults.split(",") if f.strip()]
    elif isinstance(supported_faults, list):
        supported_fault_list = [str(f).strip().lower() for f in supported_faults]
    else:
        supported_fault_list = ["*"]

    if "*" in supported_fault_list or "all" in supported_fault_list:
        score += 15
        reasons.append("Generic fault_type handler ('*') (+15)")
    elif incident_fault and incident_fault in supported_fault_list:
        score += 40
        reasons.append(f"Exact fault_type match on '{incident_fault}' (+40)")
    elif incident_fault and any(f in incident_fault or incident_fault in f for f in supported_fault_list):
        score += 20
        reasons.append(f"Partial fault_type similarity to {supported_fault_list} (+20)")
    else:
        reasons.append(
            f"Fault type mismatch: '{incident_fault}' not in supported faults {supported_fault_list} (+0)"
        )

    # ------------------------------------------------------------------------
    # 3. TELEMETRY CONDITIONS MATCH (Max 25 pts)
    # ------------------------------------------------------------------------
    telemetry_conditions = pb_context.get("telemetry_conditions") or {}
    
    # Also support shorthand thresholds like {"min_cpu_pct": 80}
    normalized_conditions: List[Tuple[str, str, float]] = []
    if isinstance(telemetry_conditions, dict):
        for metric_name, cond in telemetry_conditions.items():
            if isinstance(cond, dict):
                for op_str, target_val in cond.items():
                    normalized_conditions.append((metric_name, op_str, target_val))
            elif isinstance(cond, (int, float)):
                normalized_conditions.append((metric_name, ">=", cond))

    # Check for shorthand keys in pb_context like 'min_cpu_pct'
    for k, v in pb_context.items():
        if k.startswith("min_") and isinstance(v, (int, float)):
            metric = k.replace("min_", "")
            normalized_conditions.append((metric, ">=", v))
        elif k.startswith("max_") and isinstance(v, (int, float)):
            metric = k.replace("max_", "")
            normalized_conditions.append((metric, "<=", v))

    if not normalized_conditions:
        # General baseline playbook with no strict numeric prerequisites
        score += 15
        reasons.append("No strict telemetry conditions required; standard baseline policy (+15)")
    else:
        total_conds = len(normalized_conditions)
        passed_conds = 0

        for metric_name, op_str, target_val in normalized_conditions:
            actual_val = incident_metrics.get(metric_name)
            if actual_val is not None and _evaluate_condition(actual_val, op_str, target_val):
                passed_conds += 1
                reasons.append(
                    f"Telemetry verified: {metric_name} ({actual_val}) {op_str} {target_val}"
                )
            else:
                reasons.append(
                    f"Telemetry unmet: {metric_name} ({actual_val}) {op_str} {target_val}"
                )

        if passed_conds > 0:
            telem_score = int(round((passed_conds / total_conds) * 25))
            score += telem_score
            reasons.append(f"Telemetry conditions passed: {passed_conds}/{total_conds} (+{telem_score})")
        else:
            reasons.append(f"No telemetry conditions passed (0/{total_conds}) (+0)")

    # Bound score between 0 and 100
    final_score = max(0, min(100, score))
    return final_score, reasons


def find_matching_playbooks(
    incident_context: Dict[str, Any],
    min_score_threshold: int = 50,
    only_validated: bool = True,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Discovers, evaluates, and ranks recovery playbooks matching the incident context.

    Safety Guarantee:
    - Match score is a similarity metric ONLY, NOT proof of safety.
    - All matches return requires_governance_approval=True and governance_cleared=False.
    - If no playbook achieves >= min_score_threshold, returns a clear NO_MATCH response.
    """
    if not isinstance(incident_context, dict):
        return {
            "status": "NO_MATCH",
            "match_found": False,
            "incident_fault_type": None,
            "threshold": min_score_threshold,
            "top_match": None,
            "ranked_matches": [],
            "message": "Incident context must be a dictionary with at least 'fault_type'.",
        }

    fault_type = incident_context.get("fault_type")
    if not fault_type or not str(fault_type).strip():
        return {
            "status": "NO_MATCH",
            "match_found": False,
            "incident_fault_type": None,
            "threshold": min_score_threshold,
            "top_match": None,
            "ranked_matches": [],
            "message": "Missing required field 'fault_type' in incident context.",
        }

    # Fetch candidate playbooks from database
    status_filter = "VALIDATED" if only_validated else None
    candidates = list_all_playbooks(include_deprecated=False, db_path=db_path)
    if status_filter:
        candidates = [p for p in candidates if p.get("validation_status") == status_filter]

    scored_matches: List[Dict[str, Any]] = []

    for pb in candidates:
        score, reasons = calculate_match_score(pb, incident_context)
        if score >= min_score_threshold:
            scored_matches.append({
                "playbook_id": pb["playbook_id"],
                "name": pb["name"],
                "version": pb["version"],
                "action_id": pb["action_id"],
                "description": pb.get("description", ""),
                "parameters": pb.get("parameters", {}),
                "validation_status": pb.get("validation_status", "VALIDATED"),
                "match_score": score,
                "match_reasons": reasons,
                # Safety flags
                "safety_disclaimer": "Match score indicates similarity only. Execution requires validation and human approval.",
                "requires_governance_approval": True,
                "governance_cleared": False,
            })

    # Sort descending by match_score, then newer version first
    scored_matches.sort(key=lambda m: (m["match_score"], m["version"]), reverse=True)

    if not scored_matches:
        return {
            "status": "NO_MATCH",
            "match_found": False,
            "incident_fault_type": fault_type,
            "threshold": min_score_threshold,
            "top_match": None,
            "ranked_matches": [],
            "message": (
                f"No suitable recovery playbook meets the minimum threshold of {min_score_threshold}/100 "
                f"for fault '{fault_type}'. Manual review or AI agent diagnosis required."
            ),
        }

    top = scored_matches[0]
    return {
        "status": "MATCH_FOUND",
        "match_found": True,
        "incident_fault_type": fault_type,
        "threshold": min_score_threshold,
        "top_match": top,
        "ranked_matches": scored_matches,
        "message": (
            f"Found {len(scored_matches)} matching playbook(s). "
            f"Top match: '{top['name']}' v{top['version']} with score {top['match_score']}/100."
        ),
    }
