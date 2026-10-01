"""Background scheduler + one-shot job runner.

Runs inside the web service by default (Render starter/free has no separate
worker). A Postgres advisory lock guarantees exactly one instance executes the
jobs even when `WEB_CONCURRENCY > 1` or several instances are running.

One-shot mode is used by the Render cron services:
    python -m app.workers.scheduler --once backup
    python -m app.workers.scheduler --once sweep
    python -m app.workers.scheduler --once traffic
    python -m app.workers.scheduler --once health
"""
from __future__ import annotations

import argparse
import sys
import threading
from typing import Any, Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Node, Service, ServiceStatus
from app.db.session import SessionLocal, engine, session_scope

log = get_logger(__name__)

_scheduler: Optional[BackgroundScheduler] = None
_lock_state: dict[str, Any] = {"running": False, "detail": None, "lock_conn": None}
ADVISORY_LOCK_KEY = 987654321


def scheduler_state() -> dict[str, Any]:
    return {"running": _lock_state["running"], "detail": _lock_state["detail"]}


# --------------------------------------------------------------------------- #
#  Advisory lock (Postgres only; SQLite deployments are single-process)
# --------------------------------------------------------------------------- #
def _acquire_lock() -> bool:
    if settings.is_sqlite:
        return True
    try:
        conn = engine.raw_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        acquired = bool(cursor.fetchone()[0])
        cursor.close()
        if acquired:
            _lock_state["lock_conn"] = conn
        else:
            conn.close()
        return acquired
    except Exception as exc:  # pragma: no cover
        log.warning("advisory lock unavailable", extra={"error": str(exc)})
        return True  # fail open so jobs still run on exotic setups


def _release_lock() -> None:
    conn = _lock_state.get("lock_conn")
    if conn is not None:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
            cursor.close()
            conn.close()
        except Exception:  # pragma: no cover
            pass
        _lock_state["lock_conn"] = None


# --------------------------------------------------------------------------- #
#  Jobs
# --------------------------------------------------------------------------- #
def job_sync_traffic() -> dict[str, Any]:
    """Pull per-user counters from every active node and persist the deltas."""
    if settings.live_proxy_enabled:
        return {"updated_services": 0, "source": "built-in WebSocket relay records usage live"}
    from app.services.provisioning import collect_node_stats

    total = 0
    with session_scope() as db:
        nodes = list(db.execute(select(Node).where(Node.is_active.is_(True))).scalars())
    for node in nodes:
        with session_scope() as db:
            fresh = db.get(Node, node.id)
            if fresh is None:
                continue
            try:
                total += collect_node_stats(db, fresh, reset=False)
            except Exception as exc:  # pragma: no cover
                log.warning("traffic sync failed", extra={"node": fresh.name, "error": str(exc)})
    return {"updated_services": total}


def job_sweep() -> dict[str, Any]:
    """Quota enforcement + expiry + user notifications."""
    from app.bot.router import notify_expired, notify_quota_reached
    from app.services.provisioning import enforce_quota, expire_services

    result: dict[str, Any] = {"limited": [], "expired": []}
    with session_scope() as db:
        limited = enforce_quota(db)
        result["limited"] = limited
        for item in limited:
            service = db.get(Service, item["service_id"])
            if service:
                try:
                    notify_quota_reached(db, service)
                except Exception as exc:  # pragma: no cover
                    log.debug("quota notification failed", extra={"error": str(exc)})

    with session_scope() as db:
        expired = expire_services(db)
        result["expired"] = expired
        for item in expired:
            service = db.get(Service, item["service_id"])
            if service:
                try:
                    notify_expired(db, service)
                except Exception as exc:  # pragma: no cover
                    log.debug("expiry notification failed", extra={"error": str(exc)})
    return result


def job_expiry_reminders() -> dict[str, Any]:
    """Warn users whose service expires within the configured window."""
    from app.api.v1.settings import get_setting
    from app.bot.router import notify_expiring
    from app.services.provisioning import services_expiring_soon

    sent = 0
    with session_scope() as db:
        days = int(get_setting(db, "notifications.expiry_reminder_days", 3) or 3)
        for service in services_expiring_soon(db, days=days):
            try:
                if notify_expiring(db, service):
                    sent += 1
            except Exception as exc:  # pragma: no cover
                log.debug("reminder failed", extra={"error": str(exc)})
    return {"reminders_sent": sent}


def job_health() -> dict[str, Any]:
    from app.services.alerts import check_expiring_services, check_node_health

    with session_scope() as db:
        from app.core.config import settings
        node_alerts = 0 if settings.live_proxy_enabled else len(check_node_health(db))
        expiry_alerts = len(check_expiring_services(db))
    return {"node_alerts": node_alerts, "expiry_alerts": expiry_alerts}


def job_backup() -> dict[str, Any]:
    from app.services.backup import create_backup, prune_backups

    with session_scope() as db:
        record = create_backup(db, label="auto")
        removed = prune_backups(db)
    return {"file": record.filename, "status": record.status, "pruned": removed}


def job_maintenance() -> dict[str, Any]:
    """Retention for metrics + stale alerts."""
    from app.services.alerts import prune_metrics

    with session_scope() as db:
        metrics = prune_metrics(db, days=30)
    return {"metrics_pruned": metrics}


JOBS: dict[str, tuple[Callable[[], dict], int]] = {
    "traffic": (job_sync_traffic, max(settings.traffic_sync_interval_seconds, 30)),
    "sweep": (job_sweep, 300),
    "reminders": (job_expiry_reminders, 3600 * 6),
    "health": (job_health, 120),
    "backup": (job_backup, max(settings.backup_interval_hours, 1) * 3600),
    "maintenance": (job_maintenance, 3600 * 12),
}


def _run_job(name: str) -> None:
    func, _ = JOBS[name]
    try:
        result = func()
        log.info("job finished", extra={"job": name, "result": result})
    except Exception as exc:  # pragma: no cover
        log.exception("job failed", extra={"job": name, "error": str(exc)})


# --------------------------------------------------------------------------- #
#  Scheduler lifecycle
# --------------------------------------------------------------------------- #
def start_scheduler() -> Optional[BackgroundScheduler]:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    if not settings.enable_scheduler:
        _lock_state.update(running=False, detail="disabled by ENABLE_SCHEDULER=false")
        log.info("scheduler disabled")
        return None

    if not _acquire_lock():
        _lock_state.update(running=False, detail="another instance holds the advisory lock")
        log.info("scheduler skipped: another instance is the leader")
        return None

    scheduler = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120})
    for name, (_, interval) in JOBS.items():
        if name == "backup" and not settings.backup_enabled:
            continue
        scheduler.add_job(
            _run_job,
            IntervalTrigger(seconds=interval),
            args=[name],
            id=name,
            name=name,
            next_run_time=None if name != "health" else None,
        )
    scheduler.start()
    _scheduler = scheduler
    _lock_state.update(running=True, detail=f"{len(scheduler.get_jobs())} jobs registered")

    # Kick off a first pass shortly after boot so the dashboard is never empty.
    threading.Timer(5.0, lambda: _run_job("health")).start()
    threading.Timer(12.0, lambda: _run_job("traffic")).start()
    threading.Timer(20.0, lambda: _run_job("sweep")).start()

    log.info("scheduler started", extra={"jobs": [j.id for j in scheduler.get_jobs()]})
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    _release_lock()
    _lock_state.update(running=False, detail="stopped")


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="app.workers.scheduler", description="Xray Panel job runner")
    parser.add_argument("--once", choices=sorted(JOBS.keys()), help="run a single job and exit")
    parser.add_argument("--list", action="store_true", help="list available jobs")
    args = parser.parse_args(argv)

    from app.core.logging import setup_logging
    from app.db.session import init_db

    setup_logging()

    if args.list:
        for name, (_, interval) in JOBS.items():
            print(f"{name:<12} every {interval}s")
        return 0

    if not args.once:
        parser.print_help()
        return 2

    init_db()
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    result = JOBS[args.once][0]()
    print(f"[{args.once}] {result}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
