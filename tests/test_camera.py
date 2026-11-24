from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from daily_shot.camera import (
    CameraError,
    apply_controls,
    build_archive_path,
    capture_photo,
    is_capture_requested,
    parse_shot_timestamp,
    parse_v4l2_controls,
    parse_v4l2_devices,
    resolve_device,
)
from daily_shot.config import CameraConfig, WatermarkConfig

SAMPLE_LIST_DEVICES = """Fic760xUsbCamera: USB Video Ymz (usb-0000:00:14.0-1):
\t/dev/video2
\t/dev/video3
\t/dev/media1

USB2.0 HD UVC WebCam: USB2.0 HD (usb-0000:00:14.0-6):
\t/dev/video0
\t/dev/video1
"""

SAMPLE_CTRLS_OUTPUT = """
User Controls

                     brightness 0x00980900 (int)    : min=1 max=64 step=1 default=27 value=32 flags=0x00001000
        white_balance_automatic 0x0098090c (bool)   : default=1 value=1
           power_line_frequency 0x00980918 (menu)   : min=0 max=2 default=1 value=1 (50 Hz)
\t\t\t\t0: Disabled
\t\t\t\t1: 50 Hz
\t\t\t\t2: 60 Hz
      white_balance_temperature 0x0098091a (int)    : min=1800 max=10000 step=1 default=5000 value=5000 flags=inactive, 0x00001000

Camera Controls

                  auto_exposure 0x009a0901 (menu)   : min=0 max=3 default=3 value=3 (Aperture Priority Mode)
\t\t\t\t1: Manual Mode
\t\t\t\t3: Aperture Priority Mode
                 focus_absolute 0x009a090a (int)    : min=0 max=1023 step=1 default=192 value=388 flags=inactive, 0x00001000
     focus_automatic_continuous 0x009a090c (bool)   : default=1 value=1
"""


def test_parse_v4l2_devices_and_controls() -> None:
    assert parse_v4l2_devices(SAMPLE_LIST_DEVICES, "Fic760xUsbCamera") == "/dev/video2"
    assert parse_v4l2_devices(SAMPLE_LIST_DEVICES, "NonExistent") is None

    ctrls = parse_v4l2_controls(SAMPLE_CTRLS_OUTPUT)
    by_name = {c.name: c for c in ctrls}
    assert by_name["brightness"].kind == "int"
    assert by_name["brightness"].min_val == 1
    assert by_name["brightness"].max_val == 64
    assert by_name["brightness"].value == 32
    assert by_name["power_line_frequency"].menu_items == {0: "Disabled", 1: "50 Hz", 2: "60 Hz"}
    assert by_name["focus_absolute"].inactive is True
    assert by_name["focus_automatic_continuous"].group == "Camera Controls"


def test_archive_path_and_timestamp_parsing(tmp_path: Path) -> None:
    ts = datetime(2026, 11, 23, 18, 30, 5)
    p = build_archive_path(tmp_path, ts)
    assert p == tmp_path / "2026-11-23" / "2026-11-23_18-30-05.jpg"
    assert parse_shot_timestamp(p) == ts
    assert parse_shot_timestamp(tmp_path / "shot_20261123_183005.jpg") == ts


def test_two_phase_apply_controls(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    apply_controls(
        "/dev/video2",
        {
            "focus_absolute": 0,
            "focus_automatic_continuous": 0,
            "power_line_frequency": 2,
            "auto_exposure": 3,
            "exposure_time_absolute": 100,
            "white_balance_automatic": 1,
            "white_balance_temperature": 5000,
        },
    )
    assert len(calls) == 2
    assert "focus_automatic_continuous=0" in calls[0][-1]
    assert "auto_exposure=3" in calls[0][-1]
    assert "focus_absolute=0" in calls[1][-1]
    assert "power_line_frequency=2" in calls[1][-1]
    assert "exposure_time_absolute" not in calls[1][-1]
    assert "white_balance_temperature" not in calls[1][-1]


def test_capture_photo_cleanup_on_failure_and_success(tmp_path: Path, monkeypatch) -> None:
    cfg = CameraConfig(device="/dev/video2", warmup_seconds=1.5)
    out = build_archive_path(tmp_path / "shots", datetime(2026, 11, 23, 18, 15, 0))
    lock_dir = tmp_path / "locks"

    def fake_run_ok(cmd, **kwargs):
        if cmd[0] == "ffmpeg":
            tmp_out = Path(cmd[-1])
            tmp_out.write_bytes(b"\xff\xd8\xff\xe0dummyjpeg\xff\xd9")
            assert is_capture_requested(lock_dir) is True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_ok)
    res = capture_photo(
        cfg,
        out,
        watermark_config=WatermarkConfig(enabled=False),
        watermark=False,
        lock_dir=lock_dir,
    )
    assert res == out
    assert out.is_file()
    assert is_capture_requested(lock_dir) is False

    def fake_run_fail(cmd, **kwargs):
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, cmd, stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_fail)
    out2 = build_archive_path(tmp_path / "shots", datetime(2026, 11, 23, 18, 30, 0))
    with pytest.raises(CameraError):
        capture_photo(cfg, out2, lock_dir=lock_dir)
    assert not out2.exists()
    assert not out2.with_name(f".{out2.stem}.tmp.jpg").exists()
    assert is_capture_requested(lock_dir) is False


def test_resolve_device_auto(monkeypatch) -> None:
    cfg = CameraConfig(device="auto", device_name_match="Fic760xUsbCamera")
    monkeypatch.setattr(
        subprocess,
        "run",
        MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout=SAMPLE_LIST_DEVICES, stderr="")),
    )
    assert resolve_device(cfg) == "/dev/video2"


def test_parse_resolution_and_formats() -> None:
    from daily_shot.camera import parse_resolution, parse_v4l2_resolutions

    assert parse_resolution("1920x1080") == (1920, 1080)
    with pytest.raises(ValueError):
        parse_resolution("bad-res")

    sample_formats = """ioctl: VIDIOC_ENUM_FMT
\t[0]: 'MJPG' (Motion-JPEG, compressed)
\t\tSize: Discrete 1920x1080
\t\tSize: Discrete 4000x3000
\t\tSize: Discrete 3840x2160
\t[1]: 'YUYV' (YUYV 4:2:2)
\t\tSize: Discrete 640x480
"""
    assert parse_v4l2_resolutions(sample_formats, "mjpeg") == [
        (4000, 3000),
        (3840, 2160),
        (1920, 1080),
    ]
