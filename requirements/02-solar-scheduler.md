# Requirement 02: Solar-Aware Sunset Calculation & Scheduler

## Goal
Calculate daily sunset offline for the configured coordinates and execute the configurable sunset burst, post-burst backup, and daily post reliably across reboots with minimal idle RAM.

## Solar Calculation (`solar.py`)
- Implement the **NOAA Solar Calculation equations** using Python's `math` module (no external dependencies, no internet required):
  - Input: `date`, `latitude`, `longitude`, local timezone (`zoneinfo.ZoneInfo`).
  - Use standard sunset zenith angle **`90.833°`** (accounting for solar radius and atmospheric refraction).
  - Apply configurable `sunset_offset_minutes` to shift the reference sunset time for local horizon obstructions (trees/buildings/hills).
- Output exact local `sunset_dt: datetime`.

## Burst Slot Generation
- Generate ordered capture timestamps around `sunset_dt`:
  - `shots_before_sunset` slots spaced by `interval_before_minutes` (default `15`) before `sunset_dt`
  - Optional `0m` slot at `sunset_dt` if `include_sunset_shot = true`
  - `shots_after_sunset` slots spaced by `interval_after_minutes` (default `15`) after `sunset_dt`
- Expose `daily-shot schedule` CLI command to print today's (or a given date's) calculated sunset and all planned capture/sync/publish timestamps.

## Execution Architecture (`scheduler.py` + `systemd`)
- Use a **`systemd` 1-minute timer** (`daily-shot-tick.timer` with `OnCalendar=*-*-* *:*:00`, `OnBootSec=1min`) invoking `daily-shot tick` (`Oneshot`).
  - Keeps idle memory usage at **0 MB** between runs on the 512 MB Pi Zero W v1.
  - Survives power outages and reboots automatically without losing transient timers.
- On each `tick`:
  1. Compute today's schedule slots and compare against `state.json`.
  2. If `now` falls within `[slot_time, slot_time + slot_grace_minutes]` and the slot is not yet recorded in `completed_slots[today]`, run capture (offline-safe, zero network needed), record the slot, and mark `sync_pending = true`.
  3. After the final capture slot time has passed (or whenever `sync_pending == true` from an earlier failed/interrupted sync or manual Web UI capture, subject to retry backoff), trigger resumable `rclone copy` backup + sync-gated pruning.
  4. After `last_slot + publish_offset_after_window_minutes`, trigger daily Bluesky publish if `last_published_date != today` (and retry until `23:59` local time).
  5. Flush any queued failure alerts if network is available.

## Clock & Timezone Reliability
- Because the Pi Zero W v1 lacks a hardware RTC, document and rely on `systemd-timesyncd` (NTP) + `America/Sao_Paulo` OS timezone so system time is accurate after Wi-Fi connects.

## Acceptance Criteria
- Unit tests verify NOAA sunset calculations against known reference dates within ±2 minutes.
- `tick` is idempotent: running multiple times inside a slot window captures once; missed days never retroactively burst-capture yesterday's slots.
