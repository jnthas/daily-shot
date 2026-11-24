# Requirement 01: Project Scaffolding, Configuration & State

## Goal
Provide a lightweight, ARMv6-compatible Python package structure with clear configuration management and crash-safe persistent state.

## Platform & Dependency Constraints
- Must run cleanly on **Raspberry Pi Zero W v1 (32-bit ARMv6, 512 MB RAM)** without compiling Rust/C++ extensions.
- Allowed Python dependencies:
  - Python 3.11+ standard library (`tomllib`, `math`, `json`, `subprocess`, `logging`, `datetime`, `pathlib`, `dataclasses`)
  - `Pillow` (`python3-pil` via `apt`)
  - `requests` (`python3-requests` via `apt`)
- System CLI tools: `v4l2-ctl` (`v4l-utils`), `ffmpeg`, `rclone`, `fonts-dejavu-core` (for timestamp watermark rendering).

## Repository Structure
- `pyproject.toml`: defines package `daily_shot` under `src/daily_shot/` and console script `daily-shot = "daily_shot.cli:main"`.
- `config.example.toml`: documented template copied to `config.toml` (gitignored).
- `.env.example`: template for secrets (`BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD`) copied to `.env` (gitignored).

## Configuration Schema (`config.py`)
1. `[location]`
   - `latitude = -19.9167` (example; set your exact latitude in `config.toml`)
   - `longitude = -43.9345` (example; set your exact longitude in `config.toml`)
   - `timezone = "America/Sao_Paulo"` (or `"local"` to use OS timezone)
   - `sunset_offset_minutes = 0` (adjusts for hills/buildings blocking the western horizon)
2. `[schedule]`
   - `shots_before_sunset = 6`
   - `shots_after_sunset = 6`
   - `interval_before_minutes = 15` (or shared `interval_minutes = 15`)
   - `interval_after_minutes = 15`
   - `include_sunset_shot = true`
   - `slot_grace_minutes = 5`
3. `[camera]`
   - `device = "auto"`, `device_name_match = "Fic760xUsbCamera"`
   - `width = 4000`, `height = 3000`, `input_format = "mjpeg"`
   - `warmup_seconds = 3.0`
   - `[camera.controls]` (e.g., `focus_automatic_continuous = 0`, `focus_absolute = 0`, `auto_exposure = 3`, `white_balance_automatic = 1`, `power_line_frequency = 2`, plus optional brightness/contrast/saturation/sharpness/gain/exposure overrides)
4. `[storage]`
   - `archive_dir = "./data/shots"`
   - `state_file = "./data/state.json"`
   - `local_retention_days = 14`
   - `min_free_mb = 500` (triggers low-disk alert if SD card space gets low during extended offline periods)
5. `[backup]`
   - `enabled = true`, `rclone_remote = "gdrive"`, `rclone_path = ""`
   - `drive_root_folder_id = "<your-gdrive-folder-id>"` (passed via `--drive-root-folder-id` so daily `YYYY-MM-DD/` folders sync directly into this Google Drive folder)
   - `max_retries = 3`, `retry_delay_seconds = 10`
6. `[bluesky]`
   - `enabled = true`, `pds_url = "https://bsky.social"`
   - `handle = "dailyshot.bsky.social"` (overridable via `BLUESKY_HANDLE` in `.env`, paired with `BLUESKY_APP_PASSWORD`)
   - `publish_offset_after_window_minutes = 5`
   - `max_bytes = 950000`, `max_dimension = 2048`, `initial_quality = 92`, `min_quality = 70`
   - `location_name`, `post_template`, `alt_template`
7. `[alerts]`
   - `enabled = true`, `recipient_handle = ""`
   - `alert_after_failures = 2`, `cooldown_hours = 12`
   - `notify_on_boot = true` (sends a Bluesky DM log whenever a reboot / power-outage recovery is detected)
8. `[web]`
   - `enabled = true`, `host = "127.0.0.1"`, `port = 8080`
   - `allowed_cidrs = ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]`
   - `preview_width = 1280`, `preview_height = 720`, `preview_fps = 10`
   - `controls_override_file = "./data/camera_controls.json"` (optional persisted overrides saved from the Web UI)
   - `manual_capture_watermark_default = true`
9. `[watermark]`
   - `enabled = true` (applied automatically to scheduled captures)
   - `format = "%Y-%m-%d %H:%M:%S"` (local date & time)
   - `position = "bottom-right"` (`bottom-right`, `bottom-left`, `top-right`, `top-left`)
   - `font_path = "auto"` (auto-discovers system TrueType font such as `DejaVuSansMono.ttf`, with safe fallback)
   - `font_size_percent = 2.2` (scales proportionally to image height, ~66px at 4000×3000)
   - `show_background = true` (semi-transparent dark pill behind white text for legibility at sunset and dusk)
   - `jpeg_quality = 95` (preserves full 12MP archive sharpness when re-saving watermarked frame)

## Persistent State (`state.py`)
Store state in `state.json` using atomic writes (write `.tmp`, then `os.replace`) so power loss never corrupts JSON:
- `completed_slots`: map of `YYYY-MM-DD -> list[str]` (captured slot keys/timestamps)
- `sync_pending`: boolean (`true` whenever new photos are captured or a sync was interrupted; cleared only on exit-0 `rclone copy`)
- `last_synced_date`, `last_synced_at`, `last_sync_attempt_at`: timestamps of last successful & attempted `rclone` backup
- `last_published_date`: `YYYY-MM-DD`, `last_published_photo`, `last_post_uri`
- `failures`: consecutive failure counters per subsystem (`capture`, `backup`, `bluesky`)
- `alerts`: last alert timestamps per subsystem + `pending_alerts` queue for offline recovery
- `last_boot_id`: Linux `/proc/sys/kernel/random/boot_id` UUID of last handled boot, plus `last_boot_at` timestamp

## Acceptance Criteria
- `load_config()` loads TOML + `.env` without overriding already-set environment variables and resolves relative paths against the config file directory.
- `save_state()` writes atomically and recovers gracefully (`{}`) if `state.json` is missing or empty.
