"""Host metrics for the node agent (CPU / RAM / disk / network)."""
from __future__ import annotations

import time
from typing import Any, Optional

import psutil

_BOOT = psutil.boot_time()
_NET_BASELINE: Optional[tuple[int, int, float]] = None


def _network_delta() -> tuple[int, int]:
    global _NET_BASELINE
    counters = psutil.net_io_counters()
    now = time.time()
    if _NET_BASELINE is None:
        _NET_BASELINE = (counters.bytes_recv, counters.bytes_sent, now)
        return 0, 0
    prev_recv, prev_sent, _ = _NET_BASELINE
    _NET_BASELINE = (counters.bytes_recv, counters.bytes_sent, now)
    return max(counters.bytes_recv - prev_recv, 0), max(counters.bytes_sent - prev_sent, 0)


def snapshot() -> dict[str, Any]:
    """Cheap snapshot used by the 30s health poll."""
    try:
        cpu = psutil.cpu_percent(interval=0.15)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net_in, net_out = _network_delta()
        return {
            "cpu_percent": round(cpu, 2),
            "memory_percent": round(memory.percent, 2),
            "memory_used_bytes": memory.used,
            "memory_total_bytes": memory.total,
            "disk_percent": round(disk.percent, 2),
            "disk_used_bytes": disk.used,
            "disk_total_bytes": disk.total,
            "uptime_seconds": int(time.time() - _BOOT),
            "load_average": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else [],
            "net_in_bytes": net_in,
            "net_out_bytes": net_out,
            "cpu_count": psutil.cpu_count(logical=True),
        }
    except Exception as exc:  # pragma: no cover - platform specific
        return {"error": f"{type(exc).__name__}: {exc}"}
