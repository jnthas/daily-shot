# `daily-shot` Requirements Index

This directory breaks down the [`daily-shot`](../initial-specs.md) product and engineering plan into modular requirements for implementation and testing.

## System Summary
- **Development Workflow:** Built and tested end-to-end on the local dev machine (with the IMX362 camera attached), keeping all dependencies ARMv6-compatible for final deployment to the Raspberry Pi Zero W v1.
- **Target Controller:** Raspberry Pi Zero W v1 (32-bit ARMv6, 512 MB RAM, Raspberry Pi OS)
- **Camera:** IMX362 12MP USB UVC camera (`Fic760xUsbCamera`, `4000x3000` MJPEG, [cam_specs.avif](../cam_specs.avif))
- **Location:** Configurable `latitude` / `longitude` (example `-19.9167, -43.9345`, `America/Sao_Paulo` / UTC-3, 60 Hz power line frequency)
- **Tech Stack:** Python 3.11+ (`stdlib`: `tomllib`, `math`, `json`, `subprocess`), `python3-pil`, `python3-requests`, `v4l-utils`, `ffmpeg`, `rclone`, `systemd`

## Requirement Documents

| # | Document | Scope |
|---|---|---|
| 01 | [01-project-config-and-state.md](./01-project-config-and-state.md) | Package layout, TOML + `.env` configuration, ARMv6 dependency rules, atomic `state.json` |
| 02 | [02-solar-scheduler.md](./02-solar-scheduler.md) | Offline NOAA sunset math, sunset burst slot generation, `systemd` 1-minute `tick` orchestrator |
| 03 | [03-camera-capture-and-calibration.md](./03-camera-capture-and-calibration.md) | V4L2 discovery, two-phase UVC controls, `ffmpeg` warmup + MJPEG copy capture, focus calibration CLI |
| 04 | [04-cloud-backup-and-retention.md](./04-cloud-backup-and-retention.md) | Daily post-burst `rclone copy` sync, retries, sync-verified 14-day local pruning |
| 05 | [05-bluesky-publisher.md](./05-bluesky-publisher.md) | Exact-sunset photo selection, Pillow byte-budget optimization, ATProto posting, offline catch-up |
| 06 | [06-monitoring-and-alerts.md](./06-monitoring-and-alerts.md) | `journald` logging, `status` CLI, failure counters, Bluesky DM alerts (`chat.bsky.convo`) |
| 07 | [07-testing-and-deployment.md](./07-testing-and-deployment.md) | Unit tests, live `/dev/video2` smoke checks, Pi Zero W v1 deployment checklist |
| 08 | [08-local-web-ui.md](./08-local-web-ui.md) | Local-only web UI: live MJPEG preview, all 18 camera parameters, save defaults, manual 12MP capture |
