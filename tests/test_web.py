from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from daily_shot.config import AppConfig, CameraConfig, StorageConfig, WatermarkConfig, WebConfig
from daily_shot.state import load_state
from daily_shot.web import is_client_allowed, make_web_handler

SAMPLE_CTRLS = """
User Controls

                     brightness 0x00980900 (int)    : min=1 max=64 step=1 default=27 value=32 flags=0x00001000

Camera Controls

                 focus_absolute 0x009a090a (int)    : min=0 max=1023 step=1 default=192 value=0 flags=0x00001000
     focus_automatic_continuous 0x009a090c (bool)   : default=1 value=0
"""


def test_is_client_allowed() -> None:
    cidrs = ("127.0.0.0/8", "::1/128", "192.168.0.0/16")
    assert is_client_allowed("127.0.0.1", cidrs) is True
    assert is_client_allowed("::ffff:127.0.0.1", cidrs) is True
    assert is_client_allowed("192.168.1.44", cidrs) is True
    assert is_client_allowed("8.8.8.8", cidrs) is False


def test_web_api_controls_save_and_manual_capture(tmp_path: Path, monkeypatch) -> None:
    cfg = AppConfig(
        camera=CameraConfig(
            device="/dev/video2",
            warmup_seconds=0.0,
            controls_override_file=tmp_path / "data" / "camera_controls.json",
        ),
        watermark=WatermarkConfig(enabled=True, show_background=True),
        storage=StorageConfig(
            archive_dir=tmp_path / "shots",
            state_file=tmp_path / "data" / "state.json",
        ),
        web=WebConfig(host="127.0.0.1", port=0, manual_capture_watermark_default=True),
        base_dir=tmp_path,
    )

    state_ctrls = {"brightness": 32, "focus_absolute": 0, "focus_automatic_continuous": 0}

    def fake_run(cmd, **kwargs):
        if "--list-formats-ext" in cmd:
            out = (
                "ioctl: VIDIOC_ENUM_FMT\n"
                "\t[0]: 'MJPG' (Motion-JPEG, compressed)\n"
                "\t\tSize: Discrete 4000x3000\n"
                "\t\tSize: Discrete 1920x1080\n"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if "--list-ctrls-menus" in cmd:
            out = (
                f"\nUser Controls\n\n"
                f"                     brightness 0x00980900 (int)    : min=1 max=64 step=1 default=27 value={state_ctrls['brightness']} flags=0x00001000\n\n"
                f"Camera Controls\n\n"
                f"                 focus_absolute 0x009a090a (int)    : min=0 max=1023 step=1 default=192 value={state_ctrls['focus_absolute']} flags=0x00001000\n"
                f"     focus_automatic_continuous 0x009a090c (bool)   : default=1 value={state_ctrls['focus_automatic_continuous']}\n"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if any(arg.startswith("--set-ctrl=") for arg in cmd):
            arg = [a for a in cmd if a.startswith("--set-ctrl=")][0].split("=", 1)[1]
            for pair in arg.split(","):
                k, v = pair.split("=")
                state_ctrls[k] = int(v)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "ffmpeg":
            Image.new("RGB", (640, 480), (100, 150, 200)).save(Path(cmd[-1]), format="JPEG")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_web_handler(cfg))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(f"{base}/api/controls") as resp:
            data = json.loads(resp.read())
            assert data["device"] == "/dev/video2"
            assert len(data["controls"]) == 3
            assert [r["label"] for r in data["resolutions"]] == ["4000x3000", "1920x1080"]

        with urllib.request.urlopen(f"{base}/api/controls?fast=1") as resp:
            fast_data = json.loads(resp.read())
            assert len(fast_data["controls"]) == 3
            assert "resolutions" not in fast_data

        req = urllib.request.Request(
            f"{base}/api/controls",
            data=json.dumps({"controls": {"brightness": 45}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            assert state_ctrls["brightness"] == 45
            assert any(c["name"] == "brightness" and c["value"] == 45 for c in data["controls"])

        save_req = urllib.request.Request(f"{base}/api/controls/save", data=b"{}", method="POST")
        with urllib.request.urlopen(save_req) as resp:
            saved = json.loads(resp.read())
            assert saved["saved"]["brightness"] == 45
        assert cfg.camera.controls_override_file.is_file()

        cap_req = urllib.request.Request(
            f"{base}/api/capture",
            data=json.dumps({"watermark": True, "resolution": "1920x1080"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(cap_req) as resp:
            cap_data = json.loads(resp.read())
            assert cap_data["watermarked"] is True
            assert cap_data["width"] == 1920
            assert cap_data["height"] == 1080
            assert Path(cap_data["path"]).is_file()
        assert load_state(cfg.storage.state_file)["sync_pending"] is True

        # Path traversal should be blocked (404)
        try:
            urllib.request.urlopen(f"{base}/shots/../../etc/passwd")
            raise AssertionError("Expected HTTPError")
        except urllib.error.HTTPError as err:
            assert err.code == 404
    finally:
        server.shutdown()
        server.server_close()
