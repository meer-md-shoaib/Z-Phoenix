"""
Z-PHOENIX :: attack.py
=====================
Bounded chaos engine.

Injects a *sandboxed* fault without ever endangering the host:

  * CPU burners run in child processes (spawn context) so the control plane
    stays responsive even under heavy load.
  * Worker count is hard-capped at config.CHAOS_MAX_WORKERS (default 4).
  * Every worker self-terminates after config.CHAOS_MAX_DURATION_SEC (60 s).
  * A parent-side timer enforces the same 60 s emergency auto-kill.
  * stop() terminates every child instantly (kill-switch) and snaps the
    telemetry engine back to its healthy baseline.
  * Each worker runs a duty-cycled busy loop (work slice + pause slice), so
    the LPAR degrades but never locks up.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import threading
import time
from typing import Any, Dict, List, Optional

import config
import workload

_CTX = mp.get_context("spawn")

_STATE: Dict[str, Any] = {
    "active": False,
    "fault": None,
    "intensity": config.CHAOS_DEFAULT_INTENSITY,
    "started": 0.0,
    "workers": [],
    "auto_kill": None,
    "stop_reason": "never-run",
}

_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# worker (runs in a child process - keep it dependency free)
# ---------------------------------------------------------------------------
def _burn_worker(fault_type: str, intensity: int, duration_sec: float) -> None:
    """
    Duty-cycled CPU burner.

    Burns for `work` seconds, idles for `pause` seconds, repeats until the
    self-imposed deadline or until the parent terminates the process.
    """
    deadline = time.monotonic() + duration_sec
    work = 0.015 + (config.CHAOS_WORK_SLICE_SEC * intensity / 100.0)
    pause = config.CHAOS_WORKER_PAUSE_SEC
    if fault_type == "DB2_LOCK_CONTENTION":
        # Lock storms are bursty: shorter burn, longer pause.
        work = work * 0.75
        pause = pause * 2.0
    acc = 0
    while time.monotonic() < deadline:
        slice_start = time.perf_counter()
        while time.perf_counter() - slice_start < work:
            for i in range(4000):
                acc += (i * i) ^ 1
        time.sleep(pause)


# ---------------------------------------------------------------------------
# lifecycle helpers
# ---------------------------------------------------------------------------
def _kill_processes(procs: List[mp.Process]) -> None:
    for proc in procs:
        try:
            if proc.is_alive():
                proc.terminate()
        except Exception:
            pass
    for proc in procs:
        try:
            proc.join(timeout=1.0)
            if proc.is_alive() and hasattr(proc, "kill"):
                proc.kill()
                proc.join(timeout=0.5)
        except Exception:
            pass


def _auto_kill(reason: str = "AUTO_KILL_60S") -> None:
    """Emergency backstop: the fault may never outlive 60 seconds."""
    stop(reason=reason)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def _clamp_intensity(intensity: Any) -> int:
    try:
        value = int(float(intensity))
    except (TypeError, ValueError):
        value = config.CHAOS_DEFAULT_INTENSITY
    return max(config.CHAOS_MIN_INTENSITY, min(config.CHAOS_MAX_INTENSITY, value))


def status() -> Dict[str, Any]:
    with _LOCK:
        active = _STATE["active"]
        started = _STATE["started"]
        procs = list(_STATE["workers"])
        fault = _STATE["fault"]
        intensity = _STATE["intensity"]
        stop_reason = _STATE["stop_reason"]
    alive = sum(1 for p in procs if p.is_alive())
    elapsed = round(time.time() - started, 1) if (active and started) else 0.0
    remaining = round(max(0.0, config.CHAOS_MAX_DURATION_SEC - elapsed), 1)
    return {
        "active": active,
        "fault": fault,
        "intensity": intensity if active else 0,
        "elapsed_sec": elapsed,
        "auto_kill_in_sec": remaining if active else 0.0,
        "workers_alive": alive if active else 0,
        "max_workers": config.CHAOS_MAX_WORKERS,
        "max_duration_sec": config.CHAOS_MAX_DURATION_SEC,
        "stop_reason": stop_reason,
    }


def inject(fault_type: str, intensity: Optional[int] = None) -> Dict[str, Any]:
    """
    Start a bounded fault. Raises ValueError on an unknown fault type.
    Only one fault can be live at a time (re-inject supersedes).
    """
    fault = str(fault_type or "").upper().strip()
    if fault not in config.SUPPORTED_FAULTS:
        raise ValueError(
            f"Unsupported fault_type '{fault_type}'. "
            f"Supported: {', '.join(config.SUPPORTED_FAULTS)}"
        )
    if not os.cpu_count():  # paranoia: never run chaos on an unknown host
        raise RuntimeError("Host topology unknown - chaos injection blocked")

    stop(reason="SUPERSEDED")

    value = _clamp_intensity(intensity)
    workers = 1 + int(value / 34)                       # 1..4
    workers = max(1, min(workers, config.CHAOS_MAX_WORKERS))

    procs: List[mp.Process] = []
    for _ in range(workers):
        proc = _CTX.Process(
            target=_burn_worker,
            args=(fault, value, float(config.CHAOS_MAX_DURATION_SEC)),
            daemon=True,
        )
        proc.start()
        procs.append(proc)

    workload.engine.set_fault(fault, value)

    auto_kill = threading.Timer(config.CHAOS_MAX_DURATION_SEC,
                                _auto_kill, args=("AUTO_KILL_60S",))
    auto_kill.daemon = True
    auto_kill.start()

    with _LOCK:
        _STATE.update({
            "active": True,
            "fault": fault,
            "intensity": value,
            "started": time.time(),
            "workers": procs,
            "auto_kill": auto_kill,
            "stop_reason": None,
        })

    result = status()
    result["message"] = (
        f"Bounded {fault} fault injected at {value}% intensity "
        f"with {workers} worker(s); auto-kill in "
        f"{config.CHAOS_MAX_DURATION_SEC}s."
    )
    return result


def stop(reason: str = "EMERGENCY_KILL_SWITCH") -> Dict[str, Any]:
    """Instant kill-switch: terminate every child and restore the baseline."""
    with _LOCK:
        procs = list(_STATE["workers"])
        timer = _STATE["auto_kill"]
        was_active = _STATE["active"]
        _STATE.update({
            "active": False,
            "fault": None,
            "workers": [],
            "auto_kill": None,
            "stop_reason": reason,
        })

    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass

    _kill_processes(procs)
    workload.engine.clear_fault()

    result = status()
    result["message"] = (
        f"Chaos stopped ({reason}); all burner processes terminated and "
        f"telemetry restored to baseline."
        if was_active else f"Chaos already idle ({reason})."
    )
    return result
