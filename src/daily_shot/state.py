"""Crash-safe atomic JSON state persistence."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_STATE: dict[str, Any] = {
    "completed_slots": {},
    "sync_pending": False,
    "last_synced_date": None,
    "last_synced_at": None,
    "last_sync_attempt_at": None,
    "last_published_date": None,
    "last_published_photo": None,
    "last_post_uri": None,
    "failures": {"capture": 0, "backup": 0, "bluesky": 0},
    "last_errors": {},
    "last_alert_at": {},
    "pending_alerts": [],
    "last_boot_id": None,
    "last_boot_at": None,
}


def default_state() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_STATE)


def load_state(state_file: Path) -> dict[str, Any]:
    """Load state JSON, returning clean defaults if missing or corrupt."""
    state = default_state()
    if not state_file.is_file():
        return state
    try:
        loaded = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if not isinstance(loaded, dict):
        return state

    for key, default_val in DEFAULT_STATE.items():
        if key not in loaded:
            continue
        if isinstance(default_val, dict) and isinstance(loaded[key], dict):
            merged = dict(default_val)
            merged.update(loaded[key])
            state[key] = merged
        else:
            state[key] = loaded[key]
    return state


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    """Write state atomically via temporary file + replace."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_file.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(state_file)


def mark_slot_completed(state: dict[str, Any], day_str: str, slot_key: str) -> None:
    completed = state.setdefault("completed_slots", {})
    day_slots = completed.setdefault(day_str, [])
    if slot_key not in day_slots:
        day_slots.append(slot_key)


def prune_completed_slots(state: dict[str, Any], keep_days: int = 30, today: date | None = None) -> None:
    """Keep completed_slots map bounded to recent days."""
    completed = state.get("completed_slots")
    if not isinstance(completed, dict):
        return
    cutoff = (today or datetime.now().date()) - timedelta(days=keep_days)
    stale_keys = []
    for day_str in completed:
        try:
            if date.fromisoformat(day_str) < cutoff:
                stale_keys.append(day_str)
        except ValueError:
            stale_keys.append(day_str)
    for k in stale_keys:
        completed.pop(k, None)


def record_failure(state: dict[str, Any], subsystem: str, error_msg: str) -> int:
    failures = state.setdefault("failures", {})
    count = int(failures.get(subsystem, 0)) + 1
    failures[subsystem] = count
    state.setdefault("last_errors", {})[subsystem] = error_msg
    return count


def reset_failure(state: dict[str, Any], subsystem: str) -> None:
    state.setdefault("failures", {})[subsystem] = 0
    state.setdefault("last_errors", {}).pop(subsystem, None)


def queue_alert(state: dict[str, Any], key: str, message: str, created_at: str) -> None:
    pending = state.setdefault("pending_alerts", [])
    if any(item.get("key") == key and item.get("message") == message for item in pending):
        return
    pending.append({"key": key, "message": message, "created_at": created_at})
