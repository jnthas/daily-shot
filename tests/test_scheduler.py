from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from daily_shot.config import (
    AlertsConfig,
    AppConfig,
    BackupConfig,
    BlueskyConfig,
    CameraConfig,
    LocationConfig,
    ScheduleConfig,
    StorageConfig,
    WatermarkConfig,
)
from daily_shot.scheduler import run_tick
from daily_shot.solar import build_daily_schedule
from daily_shot.state import load_state


def _make_cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        location=LocationConfig(
            latitude=-19.9167, longitude=-43.9345, timezone="America/Sao_Paulo"
        ),
        schedule=ScheduleConfig(
            shots_before_sunset=1,
            shots_after_sunset=1,
            interval_before_minutes=15,
            interval_after_minutes=15,
            include_sunset_shot=True,
            slot_grace_minutes=5,
        ),
        camera=CameraConfig(
            device="/dev/video2",
            warmup_seconds=0.0,
            controls_override_file=tmp_path / "camera_controls.json",
        ),
        watermark=WatermarkConfig(enabled=False),
        storage=StorageConfig(
            archive_dir=tmp_path / "shots",
            state_file=tmp_path / "state.json",
            local_retention_days=14,
            min_free_mb=10,
        ),
        backup=BackupConfig(enabled=True, max_retries=1, retry_delay_seconds=0, retry_cooldown_minutes=0),
        bluesky=BlueskyConfig(enabled=True, handle="dailyshot.bsky.social", app_password="pw"),
        alerts=AlertsConfig(enabled=True, recipient_handle="owner.bsky.social", alert_after_failures=2),
        base_dir=tmp_path,
    )


class _StubBskyClient:
    def __init__(self, fail_dm: bool = False) -> None:
        self.fail_dm = fail_dm
        self.dms: list[str] = []
        self.posts: list[str] = []

    def create_session(self, handle: str, app_password: str) -> None:
        pass

    def upload_blob(self, data: bytes) -> dict:
        return {"$type": "blob", "size": len(data)}

    def create_image_post(self, *, text: str, **kwargs) -> dict:
        self.posts.append(text)
        return {"uri": "at://did:plc:bot/app.bsky.feed.post/1"}

    def send_dm(self, recipient: str, text: str) -> dict:
        if self.fail_dm:
            from daily_shot.bluesky import BlueskyError

            raise BlueskyError("offline")
        self.dms.append(text)
        return {"id": "dm-1"}


def test_tick_boot_recovery_and_offline_capture_then_sync_and_publish(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_cfg(tmp_path)
    sched = build_daily_schedule(
        datetime(2026, 11, 23).date(),
        cfg.location,
        cfg.schedule,
        publish_offset_after_window_minutes=cfg.bluesky.publish_offset_after_window_minutes,
    )
    center_time = sched.sunset_at + timedelta(minutes=1)

    wifi_up = {"ok": False}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffmpeg":
            Image.new("RGB", (640, 480), (20, 40, 80)).save(Path(cmd[-1]), format="JPEG")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "rclone":
            if not wifi_up["ok"]:
                raise subprocess.CalledProcessError(1, cmd, stderr="no wifi")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Tick 1: boot detected + center capture succeeds offline + sync fails + boot DM queued
    client_offline = _StubBskyClient(fail_dm=True)
    rep1 = run_tick(cfg, now=center_time, client=client_offline, boot_id="boot-uuid-1111", ntp_synced=True)
    assert rep1.boot_recovered is True
    assert rep1.captured_slots == ["+0m"]
    assert rep1.synced is False
    assert rep1.alerts_sent == 0

    st1 = load_state(cfg.storage.state_file)
    assert st1["sync_pending"] is True
    assert st1["last_boot_id"] == "boot-uuid-1111"
    assert len(st1["pending_alerts"]) == 1

    # Tick 2: after publish window and Wi-Fi returns -> catches up rclone sync, publishes sunset photo, flushes boot DM
    wifi_up["ok"] = True
    client_online = _StubBskyClient(fail_dm=False)
    rep2 = run_tick(
        cfg,
        now=sched.publish_at + timedelta(minutes=1),
        client=client_online,
        boot_id="boot-uuid-1111",
        ntp_synced=True,
    )
    assert rep2.boot_recovered is False
    assert rep2.synced is True
    assert rep2.published_uri == "at://did:plc:bot/app.bsky.feed.post/1"
    assert rep2.alerts_sent == 1
    assert len(client_online.dms) == 1
    assert "boot-uui" in client_online.dms[0]

    st2 = load_state(cfg.storage.state_file)
    assert st2["sync_pending"] is False
    assert st2["pending_alerts"] == []


def test_tick_defers_solar_slots_when_ntp_unsynced(tmp_path: Path, monkeypatch) -> None:
    cfg = _make_cfg(tmp_path)
    sched = build_daily_schedule(datetime(2026, 11, 23).date(), cfg.location, cfg.schedule)
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    )
    rep = run_tick(
        cfg,
        now=sched.sunset_at,
        client=_StubBskyClient(),
        boot_id="boot-2222",
        ntp_synced=False,
    )
    assert rep.ntp_synced is False
    assert rep.captured_slots == []
