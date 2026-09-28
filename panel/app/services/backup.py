"""Database backup / restore / retention.

Two strategies:
  * `pg_dump` for Postgres when the binary is available (Render images include it
    via the `postgresql-client` package - see Dockerfile notes).
  * SQLAlchemy-level logical dump for SQLite or when pg_dump is missing.

Backups are gzip-compressed, checksummed and recorded in `backup_records`.
Optional off-site upload: set `BACKUP_S3_*` style env vars and implement
`_upload_remote` (hook left explicit so no cloud SDK becomes a hard dependency).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models import BackupRecord

log = get_logger(__name__)


def backup_dir() -> Path:
    path = Path(settings.backup_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _checksum(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _timestamp() -> str:
    return utcnow().strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------- #
#  Postgres
# --------------------------------------------------------------------------- #
def _pg_dump(target: Path) -> tuple[bool, Optional[str]]:
    url = settings.sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)
    binary = shutil.which("pg_dump")
    if not binary:
        return False, "pg_dump not found in PATH"
    try:
        with gzip.open(target, "wb") as out:
            proc = subprocess.run(
                [binary, "--no-owner", "--no-privileges", "--clean", "--if-exists", url],
                stdout=out,
                stderr=subprocess.PIPE,
                timeout=600,
                check=False,
            )
        if proc.returncode != 0:
            target.unlink(missing_ok=True)
            return False, proc.stderr.decode("utf-8", "ignore")[:500]
        return True, None
    except (OSError, subprocess.SubprocessError) as exc:
        target.unlink(missing_ok=True)
        return False, f"{type(exc).__name__}: {exc}"


def _pg_restore(source: Path) -> tuple[bool, Optional[str]]:
    url = settings.sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)
    binary = shutil.which("psql")
    if not binary:
        return False, "psql not found in PATH"
    try:
        with gzip.open(source, "rb") as handle:
            proc = subprocess.run(
                [binary, url], stdin=handle, stderr=subprocess.PIPE, timeout=900, check=False
            )
        if proc.returncode != 0:
            return False, proc.stderr.decode("utf-8", "ignore")[:500]
        return True, None
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
#  Logical dump (portable)
# --------------------------------------------------------------------------- #
TABLES = [
    "users",
    "bot_users",
    "nodes",
    "inbounds",
    "plans",
    "services",
    "payments",
    "traffic_daily",
    "node_metrics",
    "alerts",
    "audit_logs",
    "settings",
    "backup_records",
]


def _logical_dump(db: Session, target: Path) -> tuple[bool, Optional[str]]:
    from sqlalchemy import text

    payload: dict[str, Any] = {
        "generated_at": utcnow().isoformat(),
        "app": settings.app_name,
        "version": __import__("app").__version__,
        "tables": {},
    }
    try:
        for table in TABLES:
            rows = db.execute(text(f"SELECT * FROM {table}")).mappings().all()
            payload["tables"][table] = [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}
                for row in rows
            ]
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        with gzip.open(target, "wb") as handle:
            handle.write(raw)
        return True, None
    except Exception as exc:
        target.unlink(missing_ok=True)
        return False, f"{type(exc).__name__}: {exc}"


def _logical_restore(db: Session, source: Path) -> tuple[bool, Optional[str]]:
    from sqlalchemy import text

    try:
        with gzip.open(source, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"cannot read backup: {exc}"

    try:
        # Disable FK checks for the duration of the restore (SQLite only).
        if settings.is_sqlite:
            db.execute(text("PRAGMA foreign_keys=OFF"))
        for table in reversed(TABLES):
            db.execute(text(f"DELETE FROM {table}"))
        for table, rows in payload.get("tables", {}).items():
            if not rows:
                continue
            columns = list(rows[0].keys())
            placeholders = ", ".join(f":{c}" for c in columns)
            stmt = text(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})")
            for row in rows:
                db.execute(stmt, row)
        db.commit()
        return True, None
    except Exception as exc:
        db.rollback()
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if settings.is_sqlite:
            db.execute(text("PRAGMA foreign_keys=ON"))


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #
def create_backup(db: Session, *, label: str = "auto", include_files: bool = False) -> BackupRecord:
    directory = backup_dir()
    stamp = _timestamp()
    record = BackupRecord(filename="", size_bytes=0, location="local", status="pending")
    db.add(record)
    db.flush()

    if settings.is_sqlite:
        target = directory / f"panel-{label}-{stamp}.json.gz"
        ok, error = _logical_dump(db, target)
    else:
        target = directory / f"panel-{label}-{stamp}.sql.gz"
        ok, error = _pg_dump(target)
        if not ok:
            log.warning("pg_dump failed, falling back to logical dump", extra={"error": error})
            target = directory / f"panel-{label}-{stamp}.json.gz"
            ok, error = _logical_dump(db, target)

    if ok and include_files:
        bundle = directory / f"panel-{label}-{stamp}.tar.gz"
        with tarfile.open(bundle, "w:gz") as tar:
            tar.add(target, arcname=target.name)
        target.unlink(missing_ok=True)
        target = bundle

    record.filename = target.name
    record.status = "ok" if ok else "failed"
    record.error = error
    if ok:
        record.size_bytes = target.stat().st_size
        record.checksum = _checksum(target)
        _upload_remote(target)

    db.commit()
    log.info("backup finished", extra={"file": record.filename, "status": record.status, "bytes": record.size_bytes})
    return record


def _upload_remote(path: Path) -> bool:
    """Hook for off-site storage (S3/R2/B2). Implement with your SDK of choice.

    Keeping this as an explicit no-op means the panel never hard-depends on a
    cloud SDK; wire in boto3 / httpx PUT as needed for your environment.
    """
    return False


def restore_backup(db: Session, filename: str) -> tuple[bool, Optional[str]]:
    path = backup_dir() / filename
    if not path.exists():
        return False, "backup file not found"
    if path.suffixes[-2:] == [".json", ".gz"]:
        return _logical_restore(db, path)
    if path.suffix == ".gz":
        return _pg_restore(path)
    return False, f"unsupported backup format: {path.name}"


def prune_backups(db: Session, *, retention_days: Optional[int] = None) -> int:
    days = retention_days or settings.backup_retention_days
    cutoff = utcnow() - timedelta(days=days)
    removed = 0
    for record in db.execute(select(BackupRecord).where(BackupRecord.created_at < cutoff)).scalars():
        (backup_dir() / record.filename).unlink(missing_ok=True)
        db.delete(record)
        removed += 1
    if removed:
        db.commit()
    return removed


def list_backups(db: Session, limit: int = 50) -> list[BackupRecord]:
    return list(
        db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(limit)).scalars()
    )


def disk_usage() -> dict[str, int]:
    directory = backup_dir()
    total = 0
    count = 0
    for entry in directory.glob("*"):
        if entry.is_file():
            total += entry.stat().st_size
            count += 1
    return {"files": count, "bytes": total, "free_bytes": shutil.disk_usage(directory).free}
