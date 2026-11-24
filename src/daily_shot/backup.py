"""Resumable rclone Google Drive backup, singleton locking, and sync-gated pruning."""

from __future__ import annotations

import fcntl
import logging
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import BackupConfig

logger = logging.getLogger(__name__)


class BackupError(RuntimeError):
    """Raised when cloud backup fails after all retries."""


class SyncInProgressError(RuntimeError):
    """Raised when another sync process holds the singleton lock."""


def build_rclone_command(archive_dir: Path, config: BackupConfig, *, dry_run: bool = False) -> list[str]:
    remote_target = f"{config.rclone_remote}:{config.rclone_path}".rstrip("/")
    if ":" not in remote_target or remote_target.endswith(config.rclone_remote):
        remote_target = f"{config.rclone_remote}:{config.rclone_path}"

    cmd = [
        "rclone",
        "copy",
        str(archive_dir),
        remote_target,
        "--transfers",
        "2",
        "--checkers",
        "2",
        "--retries",
        "2",
        "--low-level-retries",
        "5",
        "--contimeout",
        "15s",
        "--timeout",
        "60s",
        "--create-empty-src-dirs=false",
    ]
    if config.drive_root_folder_id:
        cmd.extend(["--drive-root-folder-id", config.drive_root_folder_id])
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def sync_to_cloud(
    archive_dir: Path,
    config: BackupConfig,
    *,
    dry_run: bool = False,
    lock_dir: Path | None = None,
) -> bool:
    """Run resumable `rclone copy` across archive_dir with singleton lock and retries."""
    if not config.enabled:
        logger.info("Cloud backup is disabled; skipping.")
        return False

    if not archive_dir.exists():
        logger.info("Archive directory %s does not exist yet; nothing to sync.", archive_dir)
        return True

    lock_fp = None
    if lock_dir is not None:
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_fp = (lock_dir / "backup.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_fp.close()
            raise SyncInProgressError("Another rclone sync is already running.") from exc

    try:
        cmd = build_rclone_command(archive_dir, config, dry_run=dry_run)
        attempts = max(1, config.max_retries)
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            logger.info("Running rclone copy (%d/%d): %s", attempt, attempts, " ".join(cmd))
            try:
                subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=600)
                logger.info("Cloud backup completed cleanly.")
                return True
            except (subprocess.SubprocessError, FileNotFoundError) as exc:
                last_err = exc
                stderr = getattr(exc, "stderr", "")
                logger.warning("rclone attempt %d/%d failed: %s %s", attempt, attempts, exc, stderr)
                if attempt < attempts and config.retry_delay_seconds > 0:
                    time.sleep(config.retry_delay_seconds * attempt)

        raise BackupError(f"rclone copy failed after {attempts} attempts: {last_err}")
    finally:
        if lock_fp is not None:
            try:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            finally:
                lock_fp.close()


def prune_old_shots(
    archive_dir: Path,
    retention_days: int,
    *,
    sync_pending: bool = False,
    last_synced_date: str | None = None,
    now: datetime | None = None,
) -> int:
    """Delete local .jpg files older than retention_days only when cloud sync is clean."""
    if retention_days <= 0 or not archive_dir.exists():
        return 0
    if sync_pending or not last_synced_date:
        logger.info("Skipping local retention pruning until cloud sync completes cleanly.")
        return 0

    cutoff = (now or datetime.now()).timestamp() - timedelta(days=retention_days).total_seconds()
    removed = 0
    for jpg in archive_dir.rglob("*.jpg"):
        if jpg.is_file() and jpg.stat().st_mtime < cutoff:
            jpg.unlink()
            removed += 1

    for path in sorted(archive_dir.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass

    if removed:
        logger.info("Pruned %d local photo(s) older than %d days.", removed, retention_days)
    return removed


def free_disk_mb(path: Path) -> int:
    """Return free disk space in MiB on the filesystem containing path."""
    target = path if path.exists() else path.parent
    while not target.exists() and target != target.parent:
        target = target.parent
    usage = shutil.disk_usage(target)
    return int(usage.free // (1024 * 1024))
