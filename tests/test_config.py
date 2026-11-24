from __future__ import annotations

import os
from pathlib import Path

from daily_shot.config import load_config
from daily_shot.state import (
    load_state,
    mark_slot_completed,
    prune_completed_slots,
    record_failure,
    reset_failure,
    save_state,
)


def test_load_config_defaults_and_env(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
[location]
latitude = -19.9167
longitude = -43.9345

[storage]
archive_dir = "./shots"
state_file = "./state.json"

[backup]
drive_root_folder_id = "example-drive-folder-id"
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "BLUESKY_HANDLE=dailyshot.bsky.social\nBLUESKY_APP_PASSWORD=secret-app-pw\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)

    cfg = load_config(cfg_file)
    assert cfg.location.latitude == -19.9167
    assert cfg.location.longitude == -43.9345
    assert cfg.storage.archive_dir == (tmp_path / "shots").resolve()
    assert cfg.storage.state_file == (tmp_path / "state.json").resolve()
    assert cfg.backup.drive_root_folder_id == "example-drive-folder-id"
    assert cfg.bluesky.handle == "dailyshot.bsky.social"
    assert cfg.bluesky.app_password == "secret-app-pw"


def test_env_precedence_over_dotenv(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("BLUESKY_APP_PASSWORD=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "from-os-env")

    cfg = load_config(cfg_file)
    assert cfg.bluesky.app_password == "from-os-env"
    assert os.environ["BLUESKY_APP_PASSWORD"] == "from-os-env"


def test_state_atomic_roundtrip_and_helpers(tmp_path: Path) -> None:
    state_file = tmp_path / "data" / "state.json"
    st = load_state(state_file)
    assert st["sync_pending"] is False

    mark_slot_completed(st, "2026-11-23", "-15m")
    mark_slot_completed(st, "2026-11-23", "-15m")
    mark_slot_completed(st, "2026-10-01", "0m")
    assert st["completed_slots"]["2026-11-23"] == ["-15m"]

    assert record_failure(st, "capture", "cam err") == 1
    assert record_failure(st, "capture", "cam err 2") == 2
    reset_failure(st, "capture")
    assert st["failures"]["capture"] == 0

    from datetime import date

    prune_completed_slots(st, keep_days=30, today=date(2026, 11, 23))
    assert "2026-10-01" not in st["completed_slots"]
    assert "2026-11-23" in st["completed_slots"]

    save_state(state_file, st)
    reloaded = load_state(state_file)
    assert reloaded["completed_slots"]["2026-11-23"] == ["-15m"]

    state_file.write_text("{corrupt json", encoding="utf-8")
    recovered = load_state(state_file)
    assert recovered["completed_slots"] == {}
