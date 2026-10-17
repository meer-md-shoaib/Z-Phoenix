"""
Z-PHOENIX :: workload.py
=======================
Telemetry engine for the simulated LinuxONE LPAR.

  * CPU  - real host CPU folded into a simulated healthy gauge (~25%)
  * RAM  - real host resident memory (fallback: simulated ~2.1 GB baseline)
  * TPS  - simulated CICS transaction rate (healthy ~1000 txn/s)
  * Db2  - simulated lock-wait gauge (healthy ~2)

When attack.py injects a bounded fault the engine ramps every metric toward
its breach target over ~2.5 s:

  CPU_RUNAWAY          CPU -> 92-98%,  TPS -> 60-140,  lock waits -> ~340-460
  DB2_LOCK_CONTENTION  CPU -> 91-97%,  TPS -> 60-140,  lock waits -> ~420-650

Clearing the fault snaps the gauges back to their healthy baseline, which is
what makes "approve -> restore metrics" instantaneous in the rehearsal loop.
"""

from __future__ import annotations

import os
import random
import threading
import time
from typing import Any, Dict, Optional

import config


def _read_proc_stat() -> Optional[tuple]:
    """Return (idle_ticks, total_ticks) from /proc/stat, or None off-Linux."""
    try:
        with open("/proc/stat", "r", encoding="ascii") as handle:
            parts = handle.readline().split()[1:]
        values = [float(v) for v in parts]
        idle = values[3] + (values[4] if len(values) > 4 else 0.0)  # idle + iowait
        return idle, sum(values)
    except Exception:
        return None


def _read_mem_used_gb() -> Optional[float]:
    """Resident memory used on the real host (MemTotal - MemAvailable)."""
    try:
        total = available = None
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = float(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available = float(line.split()[1])
                if total is not None and available is not None:
                    break
        if total and available is not None:
            return (total - available) / (1024.0 * 1024.0)
        return None
    except Exception:
        return None


class TelemetryEngine:
    """1 Hz sample generator for the Z-PHOENIX command center."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fault: Optional[str] = None
        self._intensity: int = config.CHAOS_DEFAULT_INTENSITY
        self._fault_since: float = 0.0
        self._prev_idle: Optional[float] = None
        self._prev_total: Optional[float] = None

    # ------------------------------------------------------------------
    # fault control (called by attack.py)
    # ------------------------------------------------------------------
    def set_fault(self, fault_type: str, intensity: int) -> None:
        with self._lock:
            self._fault = fault_type
            self._intensity = max(config.CHAOS_MIN_INTENSITY,
                                  min(config.CHAOS_MAX_INTENSITY, int(intensity)))
            self._fault_since = time.time()

    def clear_fault(self) -> None:
        with self._lock:
            self._fault = None
            self._fault_since = 0.0

    @property
    def fault(self) -> Optional[str]:
        with self._lock:
            return self._fault

    # ------------------------------------------------------------------
    # real host probes
    # ------------------------------------------------------------------
    def _read_cpu_percent(self) -> Optional[float]:
        """Real host CPU utilisation derived from successive /proc/stat reads."""
        sample = _read_proc_stat()
        if sample is None:
            try:  # non-Linux dev hosts
                count = os.cpu_count() or 1
                return min(100.0, os.getloadavg()[0] / count * 100.0)
            except Exception:
                return None
        idle, total = sample
        percent: Optional[float] = None
        if self._prev_idle is not None and self._prev_total is not None:
            d_total = total - self._prev_total
            d_idle = idle - self._prev_idle
            if d_total > 0:
                percent = max(0.0, min(100.0, 100.0 * (1.0 - d_idle / d_total)))
        self._prev_idle, self._prev_total = idle, total
        return percent

    # ------------------------------------------------------------------
    # sampling
    # ------------------------------------------------------------------
    def sample(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            fault = self._fault
            intensity = self._intensity
            since = self._fault_since

        ramp = min(1.0, (now - since) / 2.5) if (fault and since) else 0.0
        jitter = random.uniform(-1.5, 1.5)

        if fault == "CPU_RUNAWAY":
            cpu_target = 92.0 + 6.0 * (intensity / 100.0)
            cpu = 25.0 + (cpu_target - 25.0) * ramp + jitter
            tps = 1000.0 - 920.0 * ramp + random.uniform(-40.0, 25.0)
            locks = 2.0 + (360.0 - 2.0) * ramp * (0.80 + 0.20 * intensity / 100.0) \
                + random.uniform(-5.0, 12.0)
            ram = 2.1 + 0.55 * ramp * (0.5 + intensity / 100.0) \
                + random.uniform(-0.05, 0.08)
        elif fault == "DB2_LOCK_CONTENTION":
            cpu_target = 91.0 + 6.0 * (intensity / 100.0)
            cpu = 25.0 + (cpu_target - 25.0) * ramp + jitter
            tps = 1000.0 - 930.0 * ramp + random.uniform(-35.0, 20.0)
            locks = 2.0 + (520.0 - 2.0) * ramp * (0.75 + 0.25 * intensity / 100.0) \
                + random.uniform(-8.0, 15.0)
            ram = 2.1 + 0.35 * ramp + random.uniform(-0.05, 0.06)
        else:
            cpu = config.BASELINE["cpu"] + jitter
            tps = config.BASELINE["tps"] + random.uniform(-35.0, 35.0)
            locks = max(0.0, config.BASELINE["locks"] + random.uniform(-1.0, 1.4))
            ram = config.BASELINE["ram_gb"] + random.uniform(-0.06, 0.08)

        real_cpu = self._read_cpu_percent()
        real_ram = _read_mem_used_gb()

        # The real host always wins when it is under more pressure than the gauge.
        cpu = max(cpu, real_cpu or 0.0)
        ram = max(ram, real_ram or 0.0)

        cpu = round(max(0.0, min(100.0, cpu)), 1)
        tps = round(max(0.0, tps))
        locks = int(max(0, round(locks)))
        ram = round(ram, 2)

        if (cpu >= config.THRESH_CPU_CRITICAL
                or tps <= config.THRESH_TPS_CRITICAL
                or locks >= config.THRESH_LOCKS_CRITICAL):
            health = "CRITICAL"
        elif (cpu >= config.DEGRADED_CPU
              or tps <= config.DEGRADED_TPS
              or locks >= config.DEGRADED_LOCKS):
            health = "DEGRADED"
        else:
            health = "HEALTHY"

        return {
            "ts": now,
            "cpu": cpu,
            "ram_gb": ram,
            "tps": tps,
            "locks": locks,
            "health": health,
            "fault": fault or "NONE",
            "chaos_active": bool(fault),
            "intensity": intensity if fault else 0,
            "ramp": round(ramp, 2),
            "real_cpu": None if real_cpu is None else round(real_cpu, 1),
            "real_ram_gb": None if real_ram is None else round(real_ram, 2),
        }


# Module-level singleton shared by attack.py, governance.py and app.py.
engine = TelemetryEngine()
