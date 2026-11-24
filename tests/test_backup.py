from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from daily_shot.backup import BackupError, build_rclone_command, prune_old_shots, sync_to_cloud
from daily_shot.config import BackupConfig


def test_build_rclone_command_includes_drive_folder_id(tmp_path: Path) -> None:
    cfg = BackupConfig(
        rclone_remote="gdrive",
        rclone_path="",
        drive_root_folder_id="example-drive-folder-id",
    )
    cmd = build_rclone_command(tmp_path, cfg, dry_run=True)
    assert cmd[:4] == ["rclone", "copy", str(tmp_path), "gdrive:"]
    assert "--drive-root-folder-id" in cmd
    assert "example-drive-folder-id" in cmd
    assert "--dry-run" in cmd


def test_sync_to_cloud_retries_and_succeeds(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "shots"
    archive.mkdir()
    cfg = BackupConfig(max_retries=3, retry_delay_seconds=0)

    attempts = {"n": 0}

    def fake_run(cmd, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise subprocess.CalledProcessError(1, cmd, stderr="network down")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sync_to_cloud(archive, cfg, lock_dir=tmp_path) is True
    assert attempts["n"] == 2


def test_sync_to_cloud_raises_after_exhausting_retries(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "shots"
    archive.mkdir()
    cfg = BackupConfig(max_retries=2, retry_delay_seconds=0)

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="still down")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(BackupError):
        sync_to_cloud(archive, cfg)


def test_prune_old_shots_respects_sync_gate(tmp_path: Path) -> None:
    now = datetime(2026, 11, 23, 20, 0, 0)
    old_dir = tmp_path / "2026-11-01"
    new_dir = tmp_path / "2026-11-22"
    old_dir.mkdir()
    new_dir.mkdir()

    old_file = old_dir / "2026-11-01_18-00-00.jpg"
    new_file = new_dir / "2026-11-22_18-00-00.jpg"
    old_file.write_bytes(b"old")
    new_file.write_bytes(b"new")

    old_ts = (now - timedelta(days=20)).timestamp()
    os.utime(old_file, (old_ts, old_ts))

    # Sync pending -> must NOT prune!
    assert prune_old_shots(tmp_path, 14, sync_pending=True, last_synced_date="2026-11-23", now=now) == 0
    assert old_file.exists()

    # Clean sync -> prunes old file and removes empty directory
    removed = prune_old_shots(tmp_path, 14, sync_pending=False, last_synced_date="2026-11-23", now=now)
    assert removed == 1
    assert not old_file.exists()
    assert not old_dir.exists()
    assert new_file.exists()
