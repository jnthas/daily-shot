"""Orchestrator for solar sunset burst captures, resumable backup, publishing, and recovery."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .backup import BackupError, SyncInProgressError, free_disk_mb, prune_old_shots, sync_to_cloud
from .bluesky import BlueskyClient, BlueskyError, publish_daily_photo
from .camera import (
    CameraError,
    apply_controls,
    build_archive_path,
    capture_photo,
    cleanup_stale_camera_signals,
    load_effective_controls,
    resolve_device,
)
from .config import AppConfig
from .solar import build_daily_schedule, resolve_timezone
from .state import (
    load_state,
    mark_slot_completed,
    prune_completed_slots,
    queue_alert,
    record_failure,
    reset_failure,
    save_state,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TickReport:
    boot_recovered: bool = False
    ntp_synced: bool = True
    captured_slots: list[str] | None = None
    synced: bool = False
    published_uri: str | None = None
    alerts_sent: int = 0


def read_boot_id(boot_id_path: Path = Path("/proc/sys/kernel/random/boot_id")) -> str | None:
    try:
        if boot_id_path.is_file():
            return boot_id_path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return None


def is_ntp_synchronized() -> bool:
    """Return False only when timedatectl explicitly reports NTPSynchronized=no."""
    try:
        proc = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            val = proc.stdout.strip().lower()
            if val in ("no", "false", "0"):
                return False
            if val in ("yes", "true", "1"):
                return True
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return True


def _cleanup_stale_tmp_files(archive_dir: Path) -> None:
    if not archive_dir.exists():
        return
    for tmp in archive_dir.rglob("*.tmp"):
        if tmp.is_file():
            tmp.unlink(missing_ok=True)


def _should_alert_cooldown(
    state: dict[str, Any],
    alert_key: str,
    cooldown_hours: int,
    now: datetime,
) -> bool:
    last_iso = state.get("last_alert_at", {}).get(alert_key)
    if not last_iso:
        return True
    try:
        last_dt = datetime.fromisoformat(last_iso)
        if last_dt.tzinfo is None and now.tzinfo is not None:
            last_dt = last_dt.replace(tzinfo=now.tzinfo)
        return (now - last_dt) >= timedelta(hours=cooldown_hours)
    except ValueError:
        return True


def maybe_queue_failure_alert(
    cfg: AppConfig,
    state: dict[str, Any],
    subsystem: str,
    error_msg: str,
    now: datetime,
    *,
    force: bool = False,
) -> None:
    if not cfg.alerts.enabled:
        return
    count = int(state.get("failures", {}).get(subsystem, 0))
    if not force and count < cfg.alerts.alert_after_failures:
        return
    alert_key = f"failure:{subsystem}"
    if not _should_alert_cooldown(state, alert_key, cfg.alerts.cooldown_hours, now):
        return

    msg = (
        f"[daily-shot alert] {subsystem} failed ({count} consecutive). "
        f"Time: {now.strftime('%Y-%m-%d %H:%M')}. Error: {error_msg[:180]}"
    )
    queue_alert(state, alert_key, msg, now.isoformat())
    state.setdefault("last_alert_at", {})[alert_key] = now.isoformat()


def handle_boot_recovery(
    cfg: AppConfig,
    state: dict[str, Any],
    now: datetime,
    *,
    boot_id: str | None = None,
) -> bool:
    """Detect new Linux boot, clean stale lock/temp files, reapply camera controls, and queue boot DM."""
    current_boot = boot_id if boot_id is not None else read_boot_id()
    if not current_boot or current_boot == state.get("last_boot_id"):
        return False

    logger.info("Detected new boot (%s); running unattended recovery.", current_boot[:8])
    lock_dir = cfg.storage.state_file.parent
    cleanup_stale_camera_signals(lock_dir)
    _cleanup_stale_tmp_files(cfg.storage.archive_dir)

    controls_status = "controls reapplied"
    try:
        device = resolve_device(cfg.camera)
        apply_controls(device, load_effective_controls(cfg.camera))
    except CameraError as exc:
        controls_status = f"camera control reapply deferred ({exc})"
        logger.warning("Boot recovery camera setup failed: %s", exc)

    if cfg.alerts.enabled and cfg.alerts.notify_on_boot:
        msg = (
            f"[daily-shot boot] System recovered after reboot/power cycle at "
            f"{now.strftime('%Y-%m-%d %H:%M:%S')} (boot {current_boot[:8]}). "
            f"Status: {controls_status}."
        )
        queue_alert(state, f"boot:{current_boot}", msg, now.isoformat())

    state["last_boot_id"] = current_boot
    state["last_boot_at"] = now.isoformat()
    return True


def flush_pending_alerts(
    cfg: AppConfig,
    state: dict[str, Any],
    *,
    client: BlueskyClient | None = None,
) -> int:
    """Deliver queued Bluesky DM alerts if enabled and configured."""
    pending = state.get("pending_alerts") or []
    if not pending or not cfg.alerts.enabled:
        return 0
    if not cfg.alerts.recipient_handle:
        logger.warning("Pending alerts exist, but alerts.recipient_handle is empty.")
        return 0

    bsky = client or BlueskyClient(pds_url=cfg.bluesky.pds_url)
    try:
        bsky.create_session(cfg.bluesky.handle, cfg.bluesky.app_password)
    except BlueskyError as exc:
        logger.warning("Unable to authenticate for DM alert delivery: %s", exc)
        return 0

    sent = 0
    remaining: list[dict[str, Any]] = []
    for idx, item in enumerate(pending):
        try:
            bsky.send_dm(cfg.alerts.recipient_handle, item["message"])
            sent += 1
        except BlueskyError as exc:
            logger.warning("Failed to deliver queued DM alert: %s", exc)
            remaining = pending[idx:]
            break
    state["pending_alerts"] = remaining
    return sent


def _sync_cooldown_elapsed(state: dict[str, Any], cooldown_minutes: int, now: datetime) -> bool:
    last_attempt = state.get("last_sync_attempt_at")
    if not last_attempt or cooldown_minutes <= 0:
        return True
    try:
        last_dt = datetime.fromisoformat(last_attempt)
        if last_dt.tzinfo is None and now.tzinfo is not None:
            last_dt = last_dt.replace(tzinfo=now.tzinfo)
        return (now - last_dt) >= timedelta(minutes=cooldown_minutes)
    except ValueError:
        return True


def run_tick(
    cfg: AppConfig,
    *,
    now: datetime | None = None,
    client: BlueskyClient | None = None,
    boot_id: str | None = None,
    ntp_synced: bool | None = None,
) -> TickReport:
    """Execute one scheduled tick: boot recovery, due captures, resumable sync, publish, alerts."""
    tz = resolve_timezone(cfg.location.timezone)
    current_now = now.astimezone(tz) if now else datetime.now(tz)
    state = load_state(cfg.storage.state_file)
    lock_dir = cfg.storage.state_file.parent

    report = TickReport(captured_slots=[])
    report.boot_recovered = handle_boot_recovery(cfg, state, current_now, boot_id=boot_id)

    synced_clock = ntp_synced if ntp_synced is not None else is_ntp_synchronized()
    report.ntp_synced = synced_clock
    if not synced_clock:
        logger.warning("System clock not NTP-synchronized yet; deferring solar slot checks.")
        report.alerts_sent = flush_pending_alerts(cfg, state, client=client)
        save_state(cfg.storage.state_file, state)
        return report

    today = current_now.date()
    today_str = today.isoformat()
    sched = build_daily_schedule(
        today,
        cfg.location,
        cfg.schedule,
        publish_offset_after_window_minutes=cfg.bluesky.publish_offset_after_window_minutes,
    )

    # 1. Capture any due slot for today (100% offline-capable)
    completed_today = set(state.get("completed_slots", {}).get(today_str, []))
    grace = timedelta(minutes=cfg.schedule.slot_grace_minutes)
    for slot in sched.slots:
        if slot.slot_key in completed_today:
            continue
        if slot.scheduled_at <= current_now <= (slot.scheduled_at + grace):
            output_path = build_archive_path(cfg.storage.archive_dir, current_now)
            try:
                capture_photo(
                    cfg.camera,
                    output_path,
                    watermark_config=cfg.watermark,
                    watermark=cfg.watermark.enabled,
                    timestamp=current_now,
                    lock_dir=lock_dir,
                )
                mark_slot_completed(state, today_str, slot.slot_key)
                prune_completed_slots(state, today=today)
                state["sync_pending"] = True
                reset_failure(state, "capture")
                assert report.captured_slots is not None
                report.captured_slots.append(slot.slot_key)
            except CameraError as exc:
                logger.error("Capture failed for slot %s: %s", slot.slot_key, exc)
                record_failure(state, "capture", str(exc))
                maybe_queue_failure_alert(cfg, state, "capture", str(exc), current_now)

    # 2. Resumable Google Drive sync (after burst window or whenever sync_pending is set)
    need_daily_sync = current_now >= sched.sync_at and state.get("last_synced_date") != today_str
    should_sync = cfg.backup.enabled and (bool(state.get("sync_pending")) or need_daily_sync)
    if should_sync and _sync_cooldown_elapsed(state, cfg.backup.retry_cooldown_minutes, current_now):
        state["last_sync_attempt_at"] = current_now.isoformat()
        try:
            synced = sync_to_cloud(cfg.storage.archive_dir, cfg.backup, lock_dir=lock_dir)
            if synced:
                state["sync_pending"] = False
                state["last_synced_date"] = today_str
                state["last_synced_at"] = current_now.isoformat()
                reset_failure(state, "backup")
                prune_old_shots(
                    cfg.storage.archive_dir,
                    cfg.storage.local_retention_days,
                    sync_pending=False,
                    last_synced_date=today_str,
                    now=current_now,
                )
                report.synced = True
        except SyncInProgressError:
            logger.info("rclone sync already running in another process; skipping this tick.")
        except BackupError as exc:
            logger.error("Cloud backup failed: %s", exc)
            record_failure(state, "backup", str(exc))
            maybe_queue_failure_alert(cfg, state, "backup", str(exc), current_now)

    # 3. Low-disk space guard (especially during multi-day Wi-Fi outages)
    free_mb = free_disk_mb(cfg.storage.archive_dir)
    if free_mb < cfg.storage.min_free_mb:
        alert_key = "storage:low_disk"
        if cfg.alerts.enabled and _should_alert_cooldown(
            state, alert_key, cfg.alerts.cooldown_hours, current_now
        ):
            queue_alert(
                state,
                alert_key,
                f"[daily-shot storage] Low SD card space: {free_mb} MiB free "
                f"(threshold {cfg.storage.min_free_mb} MiB).",
                current_now.isoformat(),
            )
            state.setdefault("last_alert_at", {})[alert_key] = current_now.isoformat()

    # 4. Daily Bluesky publishing (after sunset burst window)
    if (
        cfg.bluesky.enabled
        and current_now >= sched.publish_at
        and state.get("last_published_date") != today_str
    ):
        try:
            uri = publish_daily_photo(
                cfg.bluesky,
                cfg.storage.archive_dir,
                cfg.storage.state_file,
                sched.sunset_at,
                target_date=today,
                state=state,
                client=client,
            )
            if uri:
                reset_failure(state, "bluesky")
                report.published_uri = uri
        except (BlueskyError, OSError, ValueError) as exc:
            logger.error("Bluesky daily publish failed: %s", exc)
            record_failure(state, "bluesky", str(exc))
            maybe_queue_failure_alert(cfg, state, "bluesky", str(exc), current_now)

    # 5. Flush any queued DM alerts (boot recovery, failure alerts, low disk)
    report.alerts_sent = flush_pending_alerts(cfg, state, client=client)
    save_state(cfg.storage.state_file, state)
    return report
