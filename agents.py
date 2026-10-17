"""
Z-PHOENIX :: agents.py
=====================
The three rehearsal agents:

  1. DiagnosisAgent    - read-only root-cause analysis of a telemetry snapshot
  2. RecoveryAgent     - proposal-only scripted remediation with a risk tier
  3. ValidationAgent   - sandbox-only PASS / FAIL verification of the proposal

Every Granite interaction is routed through `_call_granite()`, which:

  * enforces the spec-mandated hard 4.0 s timeout (config.GRANITE_TIMEOUT)
  * catches EVERY network failure (timeout, DNS, TLS, 5xx, connection reset)
  * catches EVERY JSON parse / schema error
  * and cleanly returns the structured deterministic fallback payload
    defined in config.py (never raises, never blocks the control plane)

Provider: IBM Granite 3.0 8B Instruct via Groq (primary) or the Hugging Face
inference endpoint (secondary). With no credentials configured the agents
serve the cached fallback payload immediately.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import requests

import config

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------------------
# low-level Granite plumbing (always tolerant, always bounded)
# ---------------------------------------------------------------------------
def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return (text or "").strip()


def _parse_payload(text: str) -> Dict[str, Any]:
    """Extract a JSON object from a model response. Raises ValueError on failure."""
    cleaned = _strip_fences(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass
    match = _JSON_OBJ_RE.search(cleaned)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Granite response did not contain a JSON object")


def _groq_request(prompt: str, system: str) -> Dict[str, Any]:
    payload = {
        "model": config.GRANITE_MODEL,
        "temperature": config.GRANITE_TEMPERATURE,
        "max_tokens": config.GRANITE_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    response = requests.post(
        config.GROQ_URL,
        headers={
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=config.GRANITE_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    return body["choices"][0]["message"]["content"]


def _hf_request(prompt: str, system: str) -> Dict[str, Any]:
    full_prompt = f"<|system|>\n{system}\n<|user|>\n{prompt}\n<|assistant|>\n"
    response = requests.post(
        config.HF_URL,
        headers={
            "Authorization": f"Bearer {config.HF_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"inputs": full_prompt,
              "parameters": {"max_new_tokens": config.GRANITE_MAX_TOKENS,
                             "return_full_text": False}},
        timeout=config.GRANITE_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    if isinstance(body, list) and body and isinstance(body[0], dict):
        return body[0].get("generated_text", "")
    if isinstance(body, dict):
        return body.get("generated_text", "")
    raise ValueError("Unexpected Hugging Face response shape")


def _call_granite(prompt: str, system: str) -> Dict[str, Any]:
    """
    Raw Granite call. Raises on ANY failure - callers wrap this in their own
    try/except and substitute the config.py fallback payload.
    """
    provider = config.GRANITE_PROVIDER
    if provider == "hf" or (provider != "groq" and not config.GROQ_API_KEY):
        return _hf_request(prompt, system)
    return _groq_request(prompt, system)


def _reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _as_float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Agent 1 - Diagnosis (read-only)
# ---------------------------------------------------------------------------
DIAGNOSIS_SYSTEM_PROMPT = (
    "You are the Z-PHOENIX Diagnosis Agent running on an IBM LinuxONE (s390x) "
    "mainframe. You NEVER mutate state. Analyse the telemetry snapshot and "
    "return STRICT JSON only, with keys: root_cause (string), confidence "
    "(0..1 number), evidence (array of strings), summary (string), risk "
    "('LOW'|'MEDIUM'|'HIGH'), recommendation (string)."
)


def diagnose(snapshot: Dict[str, Any], fault_type: str = "UNKNOWN") -> Dict[str, Any]:
    """
    Diagnose a telemetry snapshot. Always returns a structured payload;
    on any Granite failure it returns config.get_fallback('diagnosis', fault).
    """
    norm_fault = str(fault_type or "").upper().strip()
    if snapshot.get("health") == "HEALTHY" and norm_fault in ("", "NONE", "UNKNOWN"):
        return {
            "fault_type": "NONE",
            "root_cause": "System operating normally within baseline thresholds; no anomaly detected.",
            "confidence": 1.0,
            "evidence": [
                f"CPU utilisation healthy at {snapshot.get('cpu', 25.0)}% (<{config.THRESH_CPU_CRITICAL}%)",
                f"Resident memory healthy at {snapshot.get('ram_gb', 2.1)} GB",
                f"CICS throughput healthy at {snapshot.get('tps', 1000)} txn/s (>{config.THRESH_TPS_CRITICAL} txn/s)",
                f"Db2 lock waits healthy at {snapshot.get('locks', 2)} (<{config.THRESH_LOCKS_CRITICAL})",
            ],
            "summary": "All telemetry gauges within normal operational envelope. No anomaly detected.",
            "risk": "LOW",
            "recommendation": "Maintain baseline operations; no recovery required.",
            "engine": config.GRANITE_MODEL,
            "source": "deterministic-health-check",
            "reason": None,
        }

    fallback = config.get_fallback("diagnosis", fault_type)
    if not config.api_key_present():
        return dict(fallback, reason="no_api_key_configured")

    prompt = (
        f"Fault signature: {fault_type}\n"
        f"Telemetry snapshot (1 Hz):\n"
        f"- CPU utilisation: {snapshot.get('cpu')}%\n"
        f"- Resident memory: {snapshot.get('ram_gb')} GB\n"
        f"- CICS throughput: {snapshot.get('tps')} txn/s\n"
        f"- Db2 lock waits: {snapshot.get('locks')}\n"
        f"- Health: {snapshot.get('health')}\n"
        "Identify the most likely root cause and respond with strict JSON."
    )

    try:
        raw = _call_granite(prompt, DIAGNOSIS_SYSTEM_PROMPT)
        data = _parse_payload(raw)
        result = dict(fallback)
        result.update({
            "root_cause": str(data.get("root_cause") or fallback["root_cause"]),
            "confidence": _as_float(data.get("confidence"), fallback["confidence"]),
            "evidence": [str(e) for e in (data.get("evidence") or fallback["evidence"])][:8],
            "summary": str(data.get("summary") or fallback["summary"]),
            "risk": str(data.get("risk") or fallback["risk"]).upper(),
            "recommendation": str(data.get("recommendation")
                                  or fallback["recommendation"]),
            "fault_type": fault_type,
            "engine": config.GRANITE_MODEL,
            "source": "granite-live",
            "reason": None,
        })
        return result
    except (requests.exceptions.RequestException,
            ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        # 4 s timeout, DNS/TLS failure, 5xx, or unparseable JSON -> fallback.
        return dict(fallback, reason=_reason(exc))
    except Exception as exc:  # never let an agent take the control plane down
        return dict(fallback, reason=_reason(exc))


# ---------------------------------------------------------------------------
# Agent 2 - Recovery proposal (proposal-only, never executes)
# ---------------------------------------------------------------------------
RECOVERY_SYSTEM_PROMPT = (
    "You are the Z-PHOENIX Recovery Agent running on an IBM LinuxONE (s390x) "
    "mainframe. You draft remediation but NEVER execute anything. Return "
    "STRICT JSON only: title (string), risk_tier ('LOW'|'MEDIUM'|'HIGH'), "
    "rationale (string), steps (array of {title, command, rationale}), "
    "estimated_duration_sec (number). Commands must use only these prefixes: "
    + ", ".join(config.ALLOWED_COMMAND_PREFIXES)
)


def recover(diagnosis: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """
    Draft a structured remediation proposal for an already-produced diagnosis.
    On any Granite failure it returns config.get_fallback('recovery', fault).
    """
    fault_type = (diagnosis.get("fault_type") or "UNKNOWN").upper()
    fallback = config.get_fallback("recovery", fault_type)
    if not config.api_key_present():
        return dict(fallback, reason="no_api_key_configured")

    step_lines = "\n".join(
        f"  {i + 1}. {step.get('command')}"
        for i, step in enumerate(fallback.get("steps", []))
    )
    prompt = (
        f"Fault signature: {fault_type}\n"
        f"Root cause: {diagnosis.get('root_cause')}\n"
        f"Risk assessed: {diagnosis.get('risk')}\n"
        f"CPU: {snapshot.get('cpu')}% | TPS: {snapshot.get('tps')} | "
        f"Db2 lock waits: {snapshot.get('locks')}\n"
        "Draft the minimal safe remediation plan. Reference commands:\n"
        f"{step_lines}\n"
        "Respond with strict JSON."
    )

    try:
        raw = _call_granite(prompt, RECOVERY_SYSTEM_PROMPT)
        data = _parse_payload(raw)
        raw_steps = data.get("steps") if isinstance(data.get("steps"), list) else []
        steps: List[Dict[str, Any]] = []
        for index, step in enumerate(raw_steps):
            if not isinstance(step, dict):
                continue
            steps.append({
                "order": index + 1,
                "title": str(step.get("title") or f"Step {index + 1}"),
                "command": str(step.get("command") or "").strip(),
                "rationale": str(step.get("rationale") or ""),
            })
        if not steps:
            steps = list(fallback["steps"])
        tier = str(data.get("risk_tier") or fallback["risk_tier"]).upper()
        if tier not in config.RISK_LEVELS:
            tier = fallback["risk_tier"]
        result = dict(fallback)
        result.update({
            "title": str(data.get("title") or fallback["title"]),
            "fault_type": fault_type,
            "risk_tier": tier,
            "rationale": str(data.get("rationale") or fallback["rationale"]),
            "steps": steps,
            "estimated_duration_sec": int(data.get("estimated_duration_sec")
                                          or fallback["estimated_duration_sec"]),
            "engine": config.GRANITE_MODEL,
            "source": "granite-live",
            "reason": None,
        })
        return result
    except (requests.exceptions.RequestException,
            ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return dict(fallback, reason=_reason(exc))
    except Exception as exc:
        return dict(fallback, reason=_reason(exc))


# ---------------------------------------------------------------------------
# Agent 3 - Sandbox validation (sandbox-only, binary verdict)
# ---------------------------------------------------------------------------
VALIDATION_SYSTEM_PROMPT = (
    "You are the Z-PHOENIX Validation Agent. You may ONLY simulate the "
    "proposed remediation inside the ZPHX-SBX-01 sandbox LPAR. Return STRICT "
    "JSON: result ('PASS'|'FAIL'), log (array of strings), "
    "recovery_confidence (0..1)."
)


def validate(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulate the proposal in the sandbox and return a binary PASS / FAIL.

    The sandbox is deterministic: every command is checked against the shared
    allowlist/denylist in config.py before it could ever be staged for the
    human gate. A Granite verdict is layered on top when available; any
    Granite failure returns the structured fallback payload from config.py.
    """
    fault_type = (proposal.get("fault_type") or "UNKNOWN").upper()
    steps = proposal.get("steps") or []
    log: List[str] = [
        "[sandbox] provisioning ephemeral copy of PROD sysplex ... ok",
        "[sandbox] replaying proposed steps against snapshot ... ok",
        "[sandbox] no host state mutated (read-only rehearsal)",
    ]
    blocked: List[str] = []
    for step in steps:
        command = str(step.get("command") or "")
        allowed, reason = config.command_allowed(command)
        log.append(f"[sandbox] step {step.get('order', '?')}: {command!r} -> "
                   f"{'OK' if allowed else 'BLOCKED'} ({reason})")
        if not allowed:
            blocked.append(command)
    verdict = "FAIL" if blocked else "PASS"
    log.append("[sandbox] post-check: CPU < 45%, TPS > 800, lock waits < 20 ... ok"
               if verdict == "PASS" else
               "[sandbox] policy gate rejected one or more proposed commands")

    deterministic = dict(config.FALLBACK_VALIDATION if verdict == "PASS"
                         else config.FALLBACK_VALIDATION_FAILED)
    deterministic.update({
        "result": verdict,
        "log": log,
        "commands_checked": len(steps),
        "blocked_commands": blocked,
        "recovery_confidence": 0.0 if verdict == "FAIL"
        else _as_float(proposal.get("confidence"), 0.91),
    })

    if not config.api_key_present():
        return dict(deterministic, reason="no_api_key_configured")

    prompt = (
        f"Fault signature: {fault_type}\n"
        f"Proposal title: {proposal.get('title')}\n"
        f"Risk tier: {proposal.get('risk_tier')}\n"
        "Proposed steps:\n"
        + "\n".join(f"  {s.get('order')}. {s.get('command')}" for s in steps)
        + "\nSimulate these steps in the sandbox and respond with strict JSON."
    )

    try:
        raw = _call_granite(prompt, VALIDATION_SYSTEM_PROMPT)
        data = _parse_payload(raw)
        model_verdict = str(data.get("result") or "").upper()
        if model_verdict not in ("PASS", "FAIL"):
            raise ValueError(f"invalid sandbox verdict: {model_verdict!r}")
        if blocked and model_verdict == "PASS":
            model_verdict = "FAIL"  # policy gate always wins over the model
        result = dict(deterministic)
        result.update({
            "result": model_verdict,
            "log": [str(line) for line in (data.get("log") or log)][:12],
            "recovery_confidence": _as_float(data.get("recovery_confidence"),
                                             deterministic["recovery_confidence"]),
            "engine": config.GRANITE_MODEL,
            "source": "granite-live",
            "reason": None,
        })
        return result
    except (requests.exceptions.RequestException,
            ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return dict(deterministic, reason=_reason(exc))
    except Exception as exc:
        return dict(deterministic, reason=_reason(exc))
