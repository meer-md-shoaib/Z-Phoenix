"""Approval and recovery workflow orchestration.

The service owns the volatile, single-use approval grant and enforces the
two-request contract: approve a validated plan, then recover with that same
grant. HTTP and UI concerns remain in the Flask application.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

import governance


class WorkflowError(Exception):
    """Expected workflow failure with an HTTP status suitable for the API."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class WorkflowService:
    def __init__(self, current_plan: Callable[[], Tuple[Optional[int], Optional[Dict[str, Any]]]],
                 sample: Callable[[], Dict[str, Any]], complete: Callable[[Dict[str, Any]], None],
                 set_stage: Callable[[int, str, Optional[str]], None],
                 logger: Any = None, approval_ttl_sec: int = 300):
        self._current_plan = current_plan
        self._sample = sample
        self._complete = complete
        self._set_stage = set_stage
        self._logger = logger
        self._approval_ttl_sec = approval_ttl_sec
        self._lock = threading.Lock()
        self._approval: Optional[Dict[str, Any]] = None

    @staticmethod
    def approval_id(incident_id: Optional[int], proposal: Dict[str, Any]) -> str:
        canonical = json.dumps({"incident_id": incident_id, "proposal": proposal},
                               sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def actor(body: Dict[str, Any]) -> str:
        actor = body.get("actor")
        if not isinstance(actor, str) or not 1 <= len(actor.strip()) <= 120:
            raise WorkflowError("actor is required and must be 1 to 120 characters.", 400)
        return actor.strip()

    @staticmethod
    def _authenticate(token: str) -> None:
        expected = os.environ.get("Z_PHOENIX_APPROVAL_TOKEN", "")
        if not expected:
            raise WorkflowError("Recovery approval is disabled: operator authentication is not configured.", 503)
        if not token or not hmac.compare_digest(token, expected):
            raise WorkflowError("Valid operator authentication is required.", 401)

    def approve(self, body: Dict[str, Any], token: str) -> Dict[str, Any]:
        self._authenticate(token)
        actor = self.actor(body)
        incident_id, proposal = self._current_plan()
        if not proposal:
            raise WorkflowError("No validated recovery plan is pending.", 409)
        if incident_id is None:
            raise WorkflowError("A persisted incident is required before approval.", 409)
        if str(proposal.get("validation_result", "")).upper() != "PASS":
            raise WorkflowError("A successful validation is required before approval.", 409)
        expected_id = self.approval_id(incident_id, proposal)
        supplied_id = body.get("approval_id")
        if not isinstance(supplied_id, str) or not hmac.compare_digest(supplied_id, expected_id):
            raise WorkflowError("Approval does not match the current incident and validated plan.", 409)
        if body.get("incident_id") != incident_id:
            raise WorkflowError("Approval incident does not match the current plan.", 409)
        try:
            governance.approve(actor)
        except governance.ApprovalError as exc:
            raise WorkflowError(str(exc), exc.status) from exc
        approved_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._approval = {
                "approval_id": expected_id,
                "incident_id": incident_id,
                "plan_fingerprint": self.approval_id(incident_id, proposal),
                "actor": actor,
                "approved_at": approved_at,
                "expires_at_epoch": time.time() + self._approval_ttl_sec,
            }
        governance.log_audit(actor, "PLAN_APPROVAL_BOUND", expected_id, "MEDIUM",
                             {"incident_id": incident_id, "expires_in_sec": self._approval_ttl_sec})
        self._set_stage(7, "approved", f"Approved by {actor}; recovery awaits a separate request")
        return {"ok": True, "status": "approved", "approval_id": expected_id,
                "incident_id": incident_id, "approved_by": actor,
                "approved_at": approved_at, "expires_in_sec": self._approval_ttl_sec}

    def recover(self, body: Dict[str, Any], token: str) -> Dict[str, Any]:
        self._authenticate(token)
        actor = self.actor(body)
        incident_id, proposal = self._current_plan()
        if not proposal or str(proposal.get("validation_result", "")).upper() != "PASS":
            raise WorkflowError("A currently validated plan is required for recovery.", 409)
        if incident_id is None:
            raise WorkflowError("A persisted incident is required before recovery.", 409)
        expected_id = self.approval_id(incident_id, proposal)
        supplied_id = body.get("approval_id")
        if not isinstance(supplied_id, str) or not hmac.compare_digest(supplied_id, expected_id):
            raise WorkflowError("Recovery request does not match the validated plan.", 409)
        if body.get("incident_id") != incident_id:
            raise WorkflowError("Recovery incident does not match the approved plan.", 409)
        with self._lock:
            approval = dict(self._approval or {})
            if not approval:
                raise WorkflowError("No explicit human approval is recorded for this plan.", 403)
            if approval.get("expires_at_epoch", 0) < time.time():
                self._approval = None
                raise WorkflowError("Approval expired; validate and approve the current plan again.", 403)
            if (approval.get("approval_id") != expected_id
                    or approval.get("incident_id") != incident_id
                    or approval.get("plan_fingerprint") != self.approval_id(incident_id, proposal)
                    or approval.get("actor") != actor):
                raise WorkflowError("Recorded approval is stale or does not match this request.", 403)
            # Consume before execution so concurrent requests and retries cannot replay it.
            self._approval = None

        before_snapshot = self._sample()
        try:
            result = governance.execute(actor)
        except governance.ApprovalError as exc:
            governance.log_audit(actor, "RECOVERY_FAILED", str(incident_id), "HIGH",
                                 {"incident_id": incident_id, "reason": "governance_denied"})
            governance.clear_pending()
            raise WorkflowError(str(exc), exc.status) from exc
        except Exception as exc:
            if self._logger:
                self._logger.error("bounded recovery failed (%s)", type(exc).__name__)
            governance.log_audit(actor, "RECOVERY_FAILED", str(incident_id), "HIGH",
                                 {"incident_id": incident_id, "reason": type(exc).__name__})
            governance.clear_pending()
            raise WorkflowError("Recovery failed; inspect the audit trail.", 500) from exc

        after_snapshot = self._sample()
        governance.log_audit(actor, "RECOVERY_SNAPSHOTS_CAPTURED", str(incident_id), "LOW",
                             {"incident_id": incident_id, "before": before_snapshot,
                              "after": after_snapshot, "simulated_telemetry": True})
        result["mode"] = "simulated"
        result["simulation_only"] = True
        result["steps_simulated"] = result.pop("steps_executed", 0)
        result.pop("command_log", None)
        result["incident_id"] = result.get("incident_id") or incident_id
        self._set_stage(7, "done", f"Approved by {result.get('approved_by')}")
        self._set_stage(8, "done", f"Stored {result['playbook_id']} v{result['playbook_version']}")
        self._complete(result)
        governance.clear_pending()
        return result

    def clear_approval(self) -> None:
        with self._lock:
            self._approval = None
