"""Command-line entrypoint for daily-shot."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .backup import free_disk_mb, prune_old_shots, sync_to_cloud
from .bluesky import BlueskyClient, publish_daily_photo
from .camera import (
    build_archive_path,
    calibrate_focus,
    capture_photo,
    get_camera_info,
    parse_resolution,
)
from .config import AppConfig, load_config
from .scheduler import is_ntp_synchronized, read_boot_id, run_tick
from .solar import build_daily_schedule, resolve_timezone
from .state import load_state, save_state
from .web import serve_web


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _cmd_schedule(cfg: AppConfig, args: argparse.Namespace) -> int:
    target = date.fromisoformat(args.date) if args.date else datetime.now(
        resolve_timezone(cfg.location.timezone)
    ).date()
    sched = build_daily_schedule(
        target,
        cfg.location,
        cfg.schedule,
        publish_offset_after_window_minutes=cfg.bluesky.publish_offset_after_window_minutes,
    )
    print(f"Date:    {sched.target_date.isoformat()}")
    print(f"Coords:  {cfg.location.latitude}, {cfg.location.longitude} ({cfg.location.timezone})")
    print(f"Sunset:  {sched.sunset_at.strftime('%Y-%m-%d %H:%M %Z')}")
    print("Capture Slots:")
    for slot in sched.slots:
        print(f"  {slot.slot_key:>5} -> {slot.scheduled_at.strftime('%H:%M')}")
    print(f"Sync:    {sched.sync_at.strftime('%H:%M')}")
    print(f"Publish: {sched.publish_at.strftime('%H:%M')}")
    return 0


def _cmd_capture(cfg: AppConfig, args: argparse.Namespace) -> int:
    tz = resolve_timezone(cfg.location.timezone)
    now = datetime.now(tz)
    watermark = cfg.watermark.enabled if args.watermark is None else args.watermark
    width, height = parse_resolution(args.resolution) if args.resolution else (None, None)

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        capture_photo(
            cfg.camera,
            out_path,
            width=width,
            height=height,
            watermark_config=cfg.watermark,
            watermark=watermark,
            timestamp=now,
            lock_dir=cfg.storage.state_file.parent,
        )
    else:
        out_path = build_archive_path(cfg.storage.archive_dir, now)
        capture_photo(
            cfg.camera,
            out_path,
            width=width,
            height=height,
            watermark_config=cfg.watermark,
            watermark=watermark,
            timestamp=now,
            lock_dir=cfg.storage.state_file.parent,
        )
        state = load_state(cfg.storage.state_file)
        state["sync_pending"] = True
        save_state(cfg.storage.state_file, state)

    print(f"Captured: {out_path} ({out_path.stat().st_size} bytes)")
    return 0


def _cmd_calibrate(cfg: AppConfig, args: argparse.Namespace) -> int:
    values = [int(v.strip()) for v in args.values.split(",") if v.strip()]
    out_dir = Path(args.output_dir).expanduser().resolve()
    info = get_camera_info(cfg.camera)
    print(f"Device: {info['device']}")
    print(info["controls_text"])
    paths = calibrate_focus(
        cfg.camera,
        out_dir,
        values,
        lock_dir=cfg.storage.state_file.parent,
    )
    for p in paths:
        print(f"Saved calibration frame: {p} ({p.stat().st_size} bytes)")
    return 0


def _cmd_sync(cfg: AppConfig, args: argparse.Namespace) -> int:
    lock_dir = cfg.storage.state_file.parent
    ok = sync_to_cloud(cfg.storage.archive_dir, cfg.backup, dry_run=args.dry_run, lock_dir=lock_dir)
    if ok and not args.dry_run:
        tz = resolve_timezone(cfg.location.timezone)
        now = datetime.now(tz)
        state = load_state(cfg.storage.state_file)
        state["sync_pending"] = False
        state["last_synced_date"] = now.date().isoformat()
        state["last_synced_at"] = now.isoformat()
        save_state(cfg.storage.state_file, state)
        prune_old_shots(
            cfg.storage.archive_dir,
            cfg.storage.local_retention_days,
            sync_pending=False,
            last_synced_date=state["last_synced_date"],
            now=now,
        )
    return 0


def _cmd_publish(cfg: AppConfig, args: argparse.Namespace) -> int:
    tz = resolve_timezone(cfg.location.timezone)
    target = date.fromisoformat(args.date) if args.date else datetime.now(tz).date()
    sched = build_daily_schedule(
        target,
        cfg.location,
        cfg.schedule,
        publish_offset_after_window_minutes=cfg.bluesky.publish_offset_after_window_minutes,
    )
    uri = publish_daily_photo(
        cfg.bluesky,
        cfg.storage.archive_dir,
        cfg.storage.state_file,
        sched.sunset_at,
        target_date=target,
        force=args.force,
        dry_run=args.dry_run,
    )
    print(f"Publish result: {uri or 'skipped'}")
    return 0


def _cmd_status(cfg: AppConfig, args: argparse.Namespace) -> int:
    tz = resolve_timezone(cfg.location.timezone)
    now = datetime.now(tz)
    today_str = now.date().isoformat()
    state = load_state(cfg.storage.state_file)
    sched = build_daily_schedule(
        now.date(),
        cfg.location,
        cfg.schedule,
        publish_offset_after_window_minutes=cfg.bluesky.publish_offset_after_window_minutes,
    )
    completed = set(state.get("completed_slots", {}).get(today_str, []))
    payload: dict[str, Any] = {
        "now": now.isoformat(),
        "boot_id": read_boot_id(),
        "last_boot_at": state.get("last_boot_at"),
        "ntp_synchronized": is_ntp_synchronized(),
        "sunset_at": sched.sunset_at.isoformat(),
        "slots": [
            {
                "slot": s.slot_key,
                "time": s.scheduled_at.strftime("%H:%M"),
                "completed": s.slot_key in completed,
            }
            for s in sched.slots
        ],
        "sync_pending": state.get("sync_pending"),
        "last_synced_at": state.get("last_synced_at"),
        "last_published_date": state.get("last_published_date"),
        "last_post_uri": state.get("last_post_uri"),
        "failures": state.get("failures"),
        "pending_alerts": len(state.get("pending_alerts") or []),
        "free_disk_mb": free_disk_mb(cfg.storage.archive_dir),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Time:          {now.strftime('%Y-%m-%d %H:%M:%S %Z')} (NTP synced: {payload['ntp_synchronized']})")
    print(f"Last Boot:     {payload['last_boot_at']} (boot_id: {payload['boot_id']})")
    print(f"Sunset Today:  {sched.sunset_at.strftime('%H:%M')}")
    print("Today Slots:")
    for item in payload["slots"]:
        mark = "DONE" if item["completed"] else "TODO"
        print(f"  [{mark}] {item['slot']:>5} ({item['time']})")
    print(f"Sync Pending:  {payload['sync_pending']} (last: {payload['last_synced_at']})")
    print(f"Last Publish:  {payload['last_published_date']} ({payload['last_post_uri']})")
    print(f"Failures:      {payload['failures']} (queued alerts: {payload['pending_alerts']})")
    print(f"Free Disk:     {payload['free_disk_mb']} MiB")
    return 0


def _cmd_camera_info(cfg: AppConfig, args: argparse.Namespace) -> int:
    info = get_camera_info(cfg.camera)
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"Device: {info['device']}\n")
        print(info["controls_text"])
        print(info["formats_text"])
    return 0


def _cmd_test_alert(cfg: AppConfig, args: argparse.Namespace) -> int:
    if not cfg.alerts.recipient_handle:
        print("Error: alerts.recipient_handle is not configured in config.toml", file=sys.stderr)
        return 1
    client = BlueskyClient(pds_url=cfg.bluesky.pds_url)
    client.create_session(cfg.bluesky.handle, cfg.bluesky.app_password)
    msg = args.message or f"[daily-shot test] DM alert check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    client.send_dm(cfg.alerts.recipient_handle, msg)
    print(f"Sent test DM to {cfg.alerts.recipient_handle}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-shot", description="Automated sunset camera bot")
    parser.add_argument("-c", "--config", help="Path to config.toml", default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("tick", help="Run one scheduler tick (capture, sync, publish, alerts)")

    p_sched = sub.add_parser("schedule", help="Show calculated sunset and burst schedule")
    p_sched.add_argument("--date", help="Date YYYY-MM-DD (default today)")

    p_cap = sub.add_parser("capture", help="Capture a single photo immediately")
    p_cap.add_argument("--output", help="Write to an isolated debug path without updating sync state")
    p_cap.add_argument("--resolution", default=None, help="Override resolution WxH (e.g. 1920x1080)")
    wm_group = p_cap.add_mutually_exclusive_group()
    wm_group.add_argument("--watermark", dest="watermark", action="store_true", default=None)
    wm_group.add_argument("--no-watermark", dest="watermark", action="store_false")

    p_cal = sub.add_parser("calibrate", help="Sweep focus_absolute values for infinity calibration")
    p_cal.add_argument("--values", default="0,50,100,150,200,300", help="Comma-separated focus_absolute values")
    p_cal.add_argument("--output-dir", default="./data/calibration", help="Output directory")

    p_sync = sub.add_parser("sync", help="Run resumable rclone Google Drive backup")
    p_sync.add_argument("--dry-run", action="store_true")

    p_pub = sub.add_parser("publish", help="Publish daily sunset photo to Bluesky")
    p_pub.add_argument("--date", help="Date YYYY-MM-DD (default today)")
    p_pub.add_argument("--force", action="store_true", help="Ignore last_published_date guard")
    p_pub.add_argument("--dry-run", action="store_true")

    p_stat = sub.add_parser("status", help="Show system, schedule, sync, and publish status")
    p_stat.add_argument("--json", action="store_true")

    p_info = sub.add_parser("camera-info", help="Print resolved camera device, controls, and formats")
    p_info.add_argument("--json", action="store_true")

    p_alert = sub.add_parser("test-alert", help="Send a test Bluesky DM alert")
    p_alert.add_argument("--message", default="")

    p_web = sub.add_parser("web", help="Run the local-only Web UI server")
    p_web.add_argument("--host", default=None)
    p_web.add_argument("--port", type=int, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    cfg = load_config(args.config)

    if args.command == "tick":
        report = run_tick(cfg)
        logging.getLogger(__name__).debug("Tick complete: %s", report)
        return 0
    if args.command == "schedule":
        return _cmd_schedule(cfg, args)
    if args.command == "capture":
        return _cmd_capture(cfg, args)
    if args.command == "calibrate":
        return _cmd_calibrate(cfg, args)
    if args.command == "sync":
        return _cmd_sync(cfg, args)
    if args.command == "publish":
        return _cmd_publish(cfg, args)
    if args.command == "status":
        return _cmd_status(cfg, args)
    if args.command == "camera-info":
        return _cmd_camera_info(cfg, args)
    if args.command == "test-alert":
        return _cmd_test_alert(cfg, args)
    if args.command == "web":
        serve_web(cfg, host=args.host, port=args.port)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
