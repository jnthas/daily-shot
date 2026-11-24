# `daily-shot` Development Task List

Master checklist tracking implementation against [`requirements/`](./requirements/README.md).
- **Location:** Configurable coordinates (example: `-19.9167, -43.9345`, `America/Sao_Paulo`, 60 Hz)
- **Bluesky Profile:** `dailyshot.bsky.social`
- **Google Drive Target:** Configurable `drive_root_folder_id` in `config.toml`
- **Development Target:** Build & smoke-test locally with `/dev/video2` (`Fic760xUsbCamera`), keeping all dependencies ARMv6-compatible for Raspberry Pi Zero W v1.

---

## Phase 1 — Project Scaffolding, Configuration & State ([Req 01](./requirements/01-project-config-and-state.md))
- [x] Create `pyproject.toml` (`daily_shot` under `src/`, `daily-shot` CLI script, zero heavy ARMv6-incompatible deps)
- [x] Create `.gitignore` (ignore `.env`, `config.toml`, `data/`, `__pycache__/`, etc.)
- [x] Create `.env.example` (`BLUESKY_HANDLE=dailyshot.bsky.social`, `BLUESKY_APP_PASSWORD`)
- [x] Create `config.example.toml` with `[location]`, `[schedule]`, `[camera]`, `[camera.controls]`, `[storage]`, `[backup]`, `[bluesky]`, `[alerts]`, `[web]`, `[watermark]`
- [x] Implement `src/daily_shot/config.py` (stdlib `tomllib` + `.env` loader + dataclasses + relative path resolution)
- [x] Implement `src/daily_shot/state.py` (atomic `state.json` read/write via `.tmp` + `os.replace`, `completed_slots`, `sync_pending`, `last_synced_*`, `last_published_*`, `failures`, `alerts` + `pending_alerts`, `last_boot_id`, `last_boot_at`)
- [x] Add unit tests in `tests/test_config.py` and state tests

## Phase 2 — Solar Sunset Math & Scheduler ([Req 02](./requirements/02-solar-scheduler.md))
- [x] Implement `src/daily_shot/solar.py` using pure-Python NOAA equations (`math`, zenith `90.833°`, `sunset_offset_minutes`)
- [x] Implement configurable sunset burst slot generator (`shots_before_sunset`, `shots_after_sunset`, `interval_before_minutes`, `interval_after_minutes`, `include_sunset_shot`)
- [x] Implement `daily-shot schedule` output formatter (shows sunset time, capture slots, sync trigger, publish trigger)
- [x] Add unit tests in `tests/test_solar.py` verifying sunset accuracy across seasons and slot counts

## Phase 3 — Camera Discovery, Controls, Watermark & Capture ([Req 03](./requirements/03-camera-capture-and-calibration.md))
- [x] Implement V4L2 device auto-discovery (`v4l2-ctl --list-devices` matching `Fic760xUsbCamera`) and `--list-ctrls-menus` parser in `src/daily_shot/camera.py`
- [x] Implement two-phase `v4l2-ctl --set-ctrl` (mode switches `focus_automatic_continuous`, `auto_exposure`, `white_balance_automatic` before dependent numeric values) + saved override merging (`data/camera_controls.json`)
- [x] Implement exclusive camera lock coordination (`data/camera.lock` via `fcntl.flock` + `data/capture.requested` stream-yield signal)
- [x] Implement `ffmpeg` warmup (`-ss <warmup_seconds>`) + `-c:v copy` `4000x3000` MJPEG capture into `<archive_dir>/YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS.jpg` (atomic `.tmp` rename)
- [x] Implement Pillow timestamp watermarking (`watermark.enabled`, `format`, `position`, `font_path`, `font_size_percent`, semi-transparent backdrop, Pillow 9.4 & 10+ compatibility, `jpeg_quality=95`)
- [x] Implement `calibrate_focus()` focus-sweep helper (`daily-shot calibrate`)
- [x] Add unit tests in `tests/test_camera.py`

## Phase 4 — Image Optimization & Daily Bluesky Publishing ([Req 05](./requirements/05-bluesky-publisher.md))
- [x] Implement `src/daily_shot/image_prep.py` (EXIF transpose, Lanczos downscale to `max_dimension`, iterative JPEG quality/scale compression under `max_bytes`)
- [x] Implement exact-sunset photo selector (`select_daily_photo` matching `YYYY-MM-DD_HH-MM-SS.jpg` closest to `sunset_dt`) in `src/daily_shot/bluesky.py`
- [x] Implement `BlueskyClient` (`createSession`, `uploadBlob`, `createRecord` with `app.bsky.embed.images`, `alt`, `aspectRatio`, and HTTP retry/backoff)
- [x] Implement templated caption & alt text formatting (`{date}`, `{time}`, `{datetime}`, `{sunset_time}`, `{location}`)
- [x] Implement same-day offline retry & next-day skip deduplication (`last_published_date`)
- [x] Add unit tests in `tests/test_image_prep.py` and `tests/test_bluesky.py`

## Phase 5 — Resumable Cloud Backup (`rclone`) & Retention ([Req 04](./requirements/04-cloud-backup-and-retention.md))
- [x] Implement `src/daily_shot/backup.py` with `rclone copy` targeting `--drive-root-folder-id` and `--transfers 2 --checkers 2 --retries 2 --low-level-retries 5 --contimeout 15s --timeout 60s`
- [x] Implement singleton sync lock + `sync_pending` state lifecycle (catch-up across multi-day Wi-Fi outages, resume where stopped)
- [x] Implement sync-gated `prune_old_shots()` (deletes `.jpg` > `local_retention_days` and empty `YYYY-MM-DD/` dirs only when `sync_pending == false`)
- [x] Implement SD card free-space check (`min_free_mb`)
- [x] Add unit tests in `tests/test_backup.py`

## Phase 6 — Monitoring, Power-Outage Recovery & Bluesky DM Alerts ([Req 06](./requirements/06-monitoring-and-alerts.md))
- [x] Extend `BlueskyClient` with DM alerting (`resolveHandle`, `chat.bsky.convo.getConvoForMembers`, `chat.bsky.convo.sendMessage` with `Atproto-Proxy: did:web:api.bsky.chat#bsky_chat`)
- [x] Implement failure counters, cooldown suppression (`cooldown_hours`), and offline `pending_alerts` queue + flush
- [x] Implement power-outage / boot detection (`last_boot_id` vs `/proc/sys/kernel/random/boot_id`), stale `.tmp`/`capture.requested` cleanup, immediate UVC control re-application, NTP check (`timedatectl`), and queued boot-recovery Bluesky DM
- [x] Implement `src/daily_shot/scheduler.py` (`tick` orchestrator combining boot recovery, capture slots, resumable sync/prune, publish, and alert flushing)
- [x] Implement `daily-shot status` (`--json`), `daily-shot camera-info`, and `daily-shot test-alert`
- [x] Add unit tests in `tests/test_scheduler.py` (and DM tests in `tests/test_bluesky.py`)

## Phase 7 — Local-Only Web UI ([Req 08](./requirements/08-local-web-ui.md))
- [x] Implement `src/daily_shot/web.py` using stdlib `ThreadingHTTPServer`
- [x] Enforce local-only `allowed_cidrs` IP allowlist (`403 Forbidden` for non-local IPs)
- [x] Implement `/stream.mjpg` live MJPEG preview (`1280x720`) with `capture.requested` / `camera.lock` yielding
- [x] Implement `GET /api/controls`, `POST /api/controls` (live two-phase V4L2 updates), `POST /api/controls/save` (`camera_controls.json`), and `POST /api/controls/reset`
- [x] Implement `POST /api/capture` (with `{"watermark": bool, "resolution": "WxH"}` options, saving into `<archive_dir>/YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS.jpg` + setting `sync_pending = true`) and `/shots/...` viewer
- [x] Build clean single-page dashboard HTML/CSS/JS rendering all 18 IMX362 controls dynamically (with dependent control disabling for auto toggles) + live preview + resolution selector + watermark checkbox + manual capture button
- [x] Add unit tests in `tests/test_web.py`

## Phase 8 — CLI, Systemd Units, Verification & Pi Deployment Docs ([Req 07](./requirements/07-testing-and-deployment.md))
- [x] Implement `src/daily_shot/cli.py` (`tick`, `schedule`, `capture`, `sync`, `publish`, `calibrate`, `status`, `camera-info`, `test-alert`, `web`, global `-v/--verbose`, `--dry-run`, `--resolution WxH`, `--watermark/--no-watermark`)
- [x] Create `systemd/daily-shot-tick.service`, `systemd/daily-shot-tick.timer`, and `systemd/daily-shot-web.service`
- [x] Write `README.md` (dev testing guide, Pi Zero W v1 ARMv6 install, SSH + key auth + `avahi` + SSH tunnel, `rclone` Google Drive setup, Bluesky App Password with DM permission, power-outage watchdog/`fsck`/`timesyncd` hardening, troubleshooting cheat sheet)
- [x] Run full `pytest` unit test suite and ensure 100% pass
- [x] Run live smoke tests on dev machine with `/dev/video2` (`camera-info`, `schedule`, `calibrate`, `capture`, `web`, `publish --dry-run`)
