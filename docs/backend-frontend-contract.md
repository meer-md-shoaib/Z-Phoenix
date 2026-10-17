# Backend and frontend workflow contract

The browser and Flask API use a two-step human gate. `POST /api/approve` only
records approval. It never starts recovery. The browser must then issue a
separate `POST /api/recover` request.

## Configuration and credentials

Set `Z_PHOENIX_APPROVAL_TOKEN` on the server. If it is unset, approval,
rejection, and recovery return HTTP 503. Send the configured value in the
`X-Z-Phoenix-Approval-Token` header on each protected request. Never put it in
the JSON body, URL, logs, or source control. The command-center UI accepts the
token for the current page session and sends it only in that header.

The token is shared across operators. `actor` is an audit label supplied by
the client, not verified identity; use an identity-aware proxy or SSO before
relying on individual attribution.

Chaos injection also requires `Z_PHOENIX_ENABLE_CHAOS=1` (or `true` / `yes`)
on the server, in addition to the approval token. The Chaos Lab and Playbook
Library each provide a page-session password field; the browser sends the
value only in the `X-Z-Phoenix-Approval-Token` header and does not persist it.

The Playbook Library's 1-Click Replay uses the authenticated legacy route
`POST /api/replay/{playbook_ref}` and sends an `actor` label. This is distinct
from `POST /api/replay`, which is a side-effect-free safe-simulation endpoint.

## Automatically validated incidents

When the backend detects, proposes, and validates an incident automatically,
the frontend receives the current `approval_context` inside the flow snapshot
on `stages` events and telemetry updates:

```json
{"approval_context":{"incident_id":42,"approval_id":"<plan-bound ID>"}}
```

The context is present only while the current incident is at the human gate
with a passing validation. It contains no credential. The frontend uses it to
send the same separate approve-then-recover requests described below; the
backend still checks the current incident, plan, token, and single-use grant.

## Sequence

1. Run diagnosis, propose a recovery, then call `POST /api/validate`.
2. On HTTP 200 with `validation.result == "PASS"`, keep `approval_id` and
   `incident_id` from that response.
3. After the operator confirms, call `POST /api/approve` with the same IDs,
   the operator's `actor` label, and the token header.
4. Only after approval returns HTTP 200 and `status == "approved"`, call
   `POST /api/recover` separately with the same body and token header.
5. Treat a recovery failure as an unresolved operation. The approval is
   single-use and may already have been consumed; check status/audit and
   validate and approve again when appropriate.

```http
POST /api/approve
Content-Type: application/json
X-Z-Phoenix-Approval-Token: <configured token>

{"actor":"sre.oncall","incident_id":42,"approval_id":"<from validation>"}
```

```json
{"ok":true,"status":"approved","approval_id":"...","incident_id":42,
 "approved_by":"sre.oncall","approved_at":"...","expires_in_sec":300}
```

Then, as a distinct request:

```http
POST /api/recover
Content-Type: application/json
X-Z-Phoenix-Approval-Token: <configured token>

{"actor":"sre.oncall","incident_id":42,"approval_id":"<same ID>"}
```

Recovery returns the simulated outcome, including `simulation_only: true`.
The service binds the grant to the validated proposal and incident, expires it
after five minutes, consumes it before execution, and invalidates it on process
restart. A changed plan requires validation and approval again.

## Errors and client behavior

All error bodies have shape `{"ok":false,"error":"...","status":N}`.

| Status | Meaning | Client action |
|---|---|---|
| 400 | Missing/invalid actor or malformed request | Correct the request. |
| 401 | Missing or incorrect token | Re-enter the configured token. |
| 403 | No valid human gate, stale grant, or expired approval | Revalidate and obtain a new approval. |
| 409 | No persisted incident, validation is not PASS, or IDs do not match the current plan | Refresh the current workflow and validate again. |
| 503 | Server token is not configured | Configure the server before enabling approvals. |
| 500 | Recovery operation failed | Inspect audit/status; do not blindly retry the consumed grant. |

Approval expires after 300 seconds, is single-use, and is volatile across
server restarts. The same `actor`, `incident_id`, `approval_id`, and token are
required for the separate recovery call. The frontend must not display an
approved response as a completed recovery; use the recovery response for that
state transition.
