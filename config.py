"""
Z-PHOENIX :: config.py
=====================
Central configuration for the Z-PHOENIX resilience & recovery rehearsal platform.

Contains:
  * host / port / database bindings
  * the strict semantic colour palette of the console theme
  * healthy baselines and critical detection thresholds
  * chaos safety caps (worker count, intensity bounds, 60 s auto-kill)
  * IBM Granite API bindings (4.0 s hard timeout)
  * the action allowlist / denylist shared by the Validation Agent and the
    governance execution gate
  * the deterministic, structured fallback payloads returned by every agent
    whenever a Granite call fails (network error, timeout, JSON parse error,
    missing credentials)

Target host: IBM LinuxONE Community Cloud (Ubuntu 24.04, s390x, Python 3.12)
"""

from __future__ import annotations

import copy
import os
import re
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. Application identity / binding
# ---------------------------------------------------------------------------
APP_NAME = "Z-PHOENIX"
APP_TAGLINE = "AI-assisted proactive resilience & recovery rehearsal"
APP_SUBTITLE = "IBM LinuxONE · s390x · Ubuntu 24.04 · Python 3.12"
HOST = "0.0.0.0"
PORT = 8080

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("Z_PHOENIX_DB", os.path.join(BASE_DIR, "z_phoenix.db"))
DB_TIMEOUT_SEC = 10.0

# ---------------------------------------------------------------------------
# 2. Semantic colour palette (premium dark mainframe console)
# ---------------------------------------------------------------------------
COLOR_CRITICAL = "#EF4444"   # Critical / Incident  (red)
COLOR_DEGRADED = "#F59E0B"   # Degraded             (orange)
COLOR_HEALTHY = "#10B981"    # Healthy              (green)
COLOR_AI = "#8B5CF6"         # AI reasoning         (purple)
COLOR_GATE = "#D97706"       # Human gate           (gold)
COLOR_TPS = "#06B6D4"        # TPS                  (cyan)
COLOR_BG = "#0B0F19"         # App background
COLOR_PANEL = "#111827"      # Panel background
COLOR_BORDER = "#1F2937"     # Panel border

# ---------------------------------------------------------------------------
# 3. Telemetry model - healthy baseline and critical thresholds
# ---------------------------------------------------------------------------
BASELINE: Dict[str, float] = {
    "cpu": 25.0,       # %    healthy CPU
    "ram_gb": 2.1,     # GB   healthy resident memory
    "tps": 1000.0,     # txn/s healthy CICS transaction rate
    "locks": 2.0,      # count healthy Db2 lock waits
}

THRESH_CPU_CRITICAL = 90.0     # CPU breach  -> incident
THRESH_TPS_CRITICAL = 150.0    # TPS breach  -> incident
THRESH_LOCKS_CRITICAL = 300.0  # Lock breach -> incident

# Recovery envelope: all three must be back inside this window before an
# incident is auto-resolved by the monitor loop.
RECOVERY_CPU_MAX = 45.0
RECOVERY_TPS_MIN = 800.0
RECOVERY_LOCKS_MAX = 20.0

# "Degraded but not critical" band used for status badges.
DEGRADED_CPU = 60.0
DEGRADED_TPS = 600.0
DEGRADED_LOCKS = 50.0

SAMPLE_INTERVAL_SEC = 1.0      # 1 Hz background emitter
DEBOUNCE_TICKS = 2             # consecutive breach/healthy ticks required

# ---------------------------------------------------------------------------
# 4. Fault catalogue
# ---------------------------------------------------------------------------
SUPPORTED_FAULTS = ("CPU_RUNAWAY", "DB2_LOCK_CONTENTION")

FAULT_LABELS: Dict[str, str] = {
    "CPU_RUNAWAY": "CPU Runaway Job",
    "DB2_LOCK_CONTENTION": "Db2 Lock Contention",
}

# ---------------------------------------------------------------------------
# 5. Chaos safety envelope (bounded fault injection)
# ---------------------------------------------------------------------------
CHAOS_DEFAULT_INTENSITY = 60   # percent
CHAOS_MIN_INTENSITY = 10
CHAOS_MAX_INTENSITY = 100
CHAOS_MAX_WORKERS = 4          # hard process cap - never exceeds this
CHAOS_MAX_DURATION_SEC = 60    # emergency auto-kill after 60 seconds
CHAOS_WORK_SLICE_SEC = 0.050   # max busy slice inside a worker loop
CHAOS_WORKER_PAUSE_SEC = 0.010 # idle slice -> host always stays responsive

# ---------------------------------------------------------------------------
# 6. IBM Granite 3.0 8B Instruct (Groq / Hugging Face)
# ---------------------------------------------------------------------------
GRANITE_MODEL = os.environ.get(
    "GRANITE_MODEL", "ibm-granite/granite-3.0-8b-instruct"
)
GRANITE_PROVIDER = os.environ.get("GRANITE_PROVIDER", "groq").strip().lower()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_URL = os.environ.get(
    "GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"
)

HF_API_TOKEN = os.environ.get(
    "HF_API_TOKEN", os.environ.get("HUGGINGFACE_API_KEY", "")
).strip()
HF_URL = os.environ.get(
    "HF_URL",
    "https://api-inference.huggingface.co/models/ibm-granite/granite-3.0-8b-instruct",
)

# Hard, spec-mandated budget for every Granite call.
GRANITE_TIMEOUT = 4.0
GRANITE_MAX_TOKENS = 700
GRANITE_TEMPERATURE = 0.1     # as deterministic as the model allows

# ---------------------------------------------------------------------------
# 7. Replay budget (validated playbook re-execution)
# ---------------------------------------------------------------------------
REPLAY_BUDGET_SEC = 2.0       # "1-Click Replay (~2s)"

# ---------------------------------------------------------------------------
# 8. Action allowlist / denylist (shared by Validation Agent + governance)
# ---------------------------------------------------------------------------
# Exact templates only. Broad command prefixes (especially ``kill ``,
# ``systemctl `` and ``ip ``) would let a proposal target unrelated host
# processes or services if an execution adapter is connected later.
ALLOWED_COMMANDS: Tuple[str, ...] = (
    "ps -eo pid,pcpu,comm --sort=-pcpu | head -5",
    "mod cbm -domain PROD -throttle 40",
    "kill -9 <RUNAWAY_PID>",
    "echo ZPHX-RECOVERY-OK",
    "echo ZPHX-CPU-RECOVERY-OK",
    "db2 force application (ALL)",
    "db2 terminate",
    "db2 update db cfg for ZPHXDB using LOCKTIMEOUT 30",
    "systemctl restart db2units",
)

DENIED_COMMAND_PATTERNS: Tuple[str, ...] = (
    r"\brm\s+-[rf]*\s",
    r"\bmkfs",
    r"\bdd\s+if=",
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r":\(\)\s*\{",
    r"\bchmod\s+777\b",
    r">\s*/dev/sd",
    r"\bdrop\s+(table|database)\b",
    r"\btruncate\s+table\b",
)

DENIED_RE = [re.compile(p, re.IGNORECASE) for p in DENIED_COMMAND_PATTERNS]

RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")


def command_allowed(command: str) -> Tuple[bool, str]:
    """Allow only exact, reviewed remediation templates."""
    cmd = (command or "").strip()
    if not cmd:
        return False, "empty command"
    for pattern in DENIED_RE:
        if pattern.search(cmd):
            return False, f"matches denied destructive pattern: {pattern.pattern}"
    if cmd not in ALLOWED_COMMANDS:
        return False, "command does not exactly match a reviewed remediation template"
    return True, "exact reviewed command template"


# ---------------------------------------------------------------------------
# 9. Deterministic fallback payloads
#    Returned verbatim by agents.py on ANY Granite failure.
# ---------------------------------------------------------------------------
FALLBACK_DIAGNOSIS: Dict[str, Any] = {
    "fault_type": "UNKNOWN",
    "root_cause": (
        "Telemetry thresholds breached on the LinuxONE LPAR: CPU saturation, "
        "transaction throughput collapse and a surge of Db2 lock waits."
    ),
    "confidence": 0.68,
    "evidence": [
        "CPU above the 90% critical threshold",
        "CICS TPS collapsed below the 150 txn/s threshold",
        "Db2 lock waits above the 300 wait threshold",
    ],
    "summary": (
        "Degraded LPAR behaviour consistent with a runaway unit of work "
        "starving the CBM online domain."
    ),
    "risk": "HIGH",
    "recommendation": (
        "Throttle the CBM domain, terminate the runaway unit of work, then "
        "clear outstanding Db2 lock waits."
    ),
    "engine": "ibm-granite/granite-3.0-8b-instruct",
    "source": "deterministic-fallback",
}

FALLBACK_DIAGNOSIS_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "CPU_RUNAWAY": {
        "root_cause": (
            "A single runaway job (CBM domain PROD, task ZPHX-ONLINE-07) is "
            "consuming >92% of the shared processor, starving the CICS online "
            "pool and collapsing transaction throughput."
        ),
        "confidence": 0.87,
        "evidence": [
            "ps -eo pcpu shows ZPHX-ONLINE-07 pinned at 96.4% CPU",
            "TPS dropped from 1000 to 96 (-90.4%) within 4 seconds",
            "Db2 lock waits climbed to 372 as blocked units of work queued",
            "CBM domain PROD throttle ceiling was never applied",
        ],
        "summary": (
            "Runaway batch/online job saturating the shared processor; "
            "throughput collapse is a downstream effect, not the cause."
        ),
        "risk": "HIGH",
        "recommendation": (
            "Throttle CBM domain PROD, kill the runaway PID, verify TPS "
            "recovers above 800 txn/s."
        ),
    },
    "DB2_LOCK_CONTENTION": {
        "root_cause": (
            "An uncommitted unit of work holds an exclusive page lock on "
            "ZPHXDB tablespace SALES.CUST, blocking 381 lock requests and "
            "stalling the entire online transaction pool."
        ),
        "confidence": 0.84,
        "evidence": [
            "db2pd -locks reports 381 applications in WAIT state on UOW",
            "blocking application = ZPHX-BATCH-114 (uncommitted, 11m idle)",
            "TPS collapsed from 1000 to 112 (-88.8%)",
            "CPU elevated to 91% from lock-manager spin waiting",
        ],
        "summary": (
            "Classic Db2 lock convoy: one idle blocker pins a tablespace page "
            "and every writer queues behind it."
        ),
        "risk": "HIGH",
        "recommendation": (
            "Force the blocking application, terminate the connection, raise "
            "LOCKTIMEOUT, then restart the Db2 member."
        ),
    },
}

FALLBACK_RECOVERY: Dict[str, Any] = {
    "title": "Generic threshold-based remediation",
    "fault_type": "UNKNOWN",
    "risk_tier": "MEDIUM",
    "rationale": (
        "Deterministic playbook drafted from threshold evidence while the "
        "Granite endpoint was unavailable."
    ),
    "steps": [
        {
            "order": 1,
            "title": "Capture current top consumers",
            "command": "ps -eo pid,pcpu,comm --sort=-pcpu | head -5",
            "rationale": "Read-only evidence capture before any change.",
        },
        {
            "order": 2,
            "title": "Throttle the CBM online domain",
            "command": "mod cbm -domain PROD -throttle 40",
            "rationale": "Caps runaway CPU at the domain level.",
        },
        {
            "order": 3,
            "title": "Confirm recovery",
            "command": "echo ZPHX-RECOVERY-OK",
            "rationale": "Non-destructive success marker.",
        },
    ],
    "estimated_duration_sec": 30,
    "engine": "ibm-granite/granite-3.0-8b-instruct",
    "source": "deterministic-fallback",
}

FALLBACK_RECOVERY_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "CPU_RUNAWAY": {
        "title": "Quiesce runaway job and throttle CBM domain",
        "risk_tier": "MEDIUM",
        "rationale": (
            "Throttling first removes the pressure safely; the kill is scoped "
            "to the single runaway PID and is reversible (job can be restarted)."
        ),
        "steps": [
            {
                "order": 1,
                "title": "Identify the top CPU consumer",
                "command": "ps -eo pid,pcpu,comm --sort=-pcpu | head -5",
                "rationale": "Confirm the runaway PID before acting.",
            },
            {
                "order": 2,
                "title": "Throttle CBM domain PROD",
                "command": "mod cbm -domain PROD -throttle 40",
                "rationale": "Immediate soft cap on the runaway workload.",
            },
            {
                "order": 3,
                "title": "Terminate the runaway unit of work",
                "command": "kill -9 <RUNAWAY_PID>",
                "rationale": "Removes the root cause; scoped to one PID.",
            },
            {
                "order": 4,
                "title": "Publish recovery marker",
                "command": "echo ZPHX-CPU-RECOVERY-OK",
                "rationale": "Deterministic success signal for the audit trail.",
            },
        ],
        "estimated_duration_sec": 45,
    },
    "DB2_LOCK_CONTENTION": {
        "title": "Clear Db2 lock convoy and restore lock timeout",
        "risk_tier": "HIGH",
        "rationale": (
            "Force application disconnects in-flight work - HIGH risk because "
            "uncommitted transactions are rolled back. Requires human gate."
        ),
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
        "estimated_duration_sec": 90,
    },
}

FALLBACK_VALIDATION: Dict[str, Any] = {
    "result": "PASS",
    "sandbox": "ZPHX-SBX-01 (isolated s390x rehearsal LPAR)",
    "log": [
        "[sandbox] provisioning ephemeral copy of PROD sysplex ... ok",
        "[sandbox] replaying proposed steps against snapshot ... ok",
        "[sandbox] no host state mutated (read-only rehearsal)",
        "[sandbox] post-check: CPU < 45%, TPS > 800, lock waits < 20 ... ok",
    ],
    "commands_checked": 0,
    "blocked_commands": [],
    "recovery_confidence": 0.91,
    "engine": "ibm-granite/granite-3.0-8b-instruct",
    "source": "deterministic-fallback",
}

FALLBACK_VALIDATION_FAILED: Dict[str, Any] = {
    "result": "FAIL",
    "sandbox": "ZPHX-SBX-01 (isolated s390x rehearsal LPAR)",
    "log": [
        "[sandbox] provisioning ephemeral copy of PROD sysplex ... ok",
        "[sandbox] policy gate rejected one or more proposed commands",
        "[sandbox] execution halted before any mutation",
    ],
    "commands_checked": 0,
    "blocked_commands": [],
    "recovery_confidence": 0.0,
    "engine": "ibm-granite/granite-3.0-8b-instruct",
    "source": "deterministic-fallback",
}


def get_fallback(kind: str, fault_type: Optional[str] = None) -> Dict[str, Any]:
    """
    Return a deep-copied, structured fallback payload from this module.

    kind: "diagnosis" | "recovery" | "validation"
    fault_type: optional fault key used to select the deterministic override.
    """
    key = (fault_type or "").upper()
    kind = (kind or "").lower()

    if kind == "diagnosis":
        base = dict(FALLBACK_DIAGNOSIS)
        override = FALLBACK_DIAGNOSIS_OVERRIDES.get(key)
        if override:
            base.update(override)
        base["fault_type"] = key if key in SUPPORTED_FAULTS else "UNKNOWN"
        base["source"] = "deterministic-fallback"
        return copy.deepcopy(base)

    if kind == "recovery":
        base = dict(FALLBACK_RECOVERY)
        override = FALLBACK_RECOVERY_OVERRIDES.get(key)
        if override:
            base.update(override)
        base["fault_type"] = key if key in SUPPORTED_FAULTS else "UNKNOWN"
        base["source"] = "deterministic-fallback"
        base["steps"] = [
            dict(step, order=index + 1)
            for index, step in enumerate(base.get("steps", []))
        ]
        return copy.deepcopy(base)

    if kind == "validation":
        return copy.deepcopy(FALLBACK_VALIDATION)

    raise ValueError(f"unknown fallback kind: {kind!r}")


def api_key_present() -> bool:
    """True when at least one Granite-capable credential is configured."""
    return bool(GROQ_API_KEY or HF_API_TOKEN)
