"""Configuration and lightweight .env loader for daily-shot."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONTROLS: dict[str, int] = {
    "focus_automatic_continuous": 0,
    "focus_absolute": 0,
    "auto_exposure": 3,
    "white_balance_automatic": 1,
    "power_line_frequency": 2,
}

DEFAULT_ALLOWED_CIDRS: tuple[str, ...] = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
)


@dataclass(slots=True)
class LocationConfig:
    latitude: float = -19.9167
    longitude: float = -43.9345
    timezone: str = "America/Sao_Paulo"
    sunset_offset_minutes: int = 0


@dataclass(slots=True)
class ScheduleConfig:
    shots_before_sunset: int = 6
    shots_after_sunset: int = 6
    interval_before_minutes: int = 15
    interval_after_minutes: int = 15
    include_sunset_shot: bool = True
    slot_grace_minutes: int = 5


@dataclass(slots=True)
class CameraConfig:
    device: str = "auto"
    device_name_match: str = "Fic760xUsbCamera"
    width: int = 4000
    height: int = 3000
    input_format: str = "mjpeg"
    warmup_seconds: float = 3.0
    controls_override_file: Path = Path("./data/camera_controls.json")
    controls: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_CONTROLS))


@dataclass(slots=True)
class WatermarkConfig:
    enabled: bool = True
    format: str = "%Y-%m-%d %H:%M:%S"
    position: str = "bottom-right"
    font_path: str = "auto"
    font_size_percent: float = 2.2
    show_background: bool = True
    jpeg_quality: int = 95


@dataclass(slots=True)
class StorageConfig:
    archive_dir: Path = Path("./data/shots")
    state_file: Path = Path("./data/state.json")
    local_retention_days: int = 14
    min_free_mb: int = 500


@dataclass(slots=True)
class BackupConfig:
    enabled: bool = True
    rclone_remote: str = "gdrive"
    rclone_path: str = ""
    drive_root_folder_id: str = ""
    max_retries: int = 3
    retry_delay_seconds: int = 10
    retry_cooldown_minutes: int = 5


@dataclass(slots=True)
class BlueskyConfig:
    enabled: bool = True
    pds_url: str = "https://bsky.social"
    handle: str = "dailyshot.bsky.social"
    app_password: str = ""
    publish_offset_after_window_minutes: int = 5
    max_bytes: int = 950_000
    max_dimension: int = 2048
    initial_quality: int = 92
    min_quality: int = 70
    location_name: str = "Belo Horizonte"
    post_template: str = "Sunset from {location} — {date} at {time}"
    alt_template: str = (
        "Outdoor horizon view from {location} captured on {date} at {time} "
        "(local sunset {sunset_time})."
    )


@dataclass(slots=True)
class AlertsConfig:
    enabled: bool = True
    recipient_handle: str = ""
    alert_after_failures: int = 2
    cooldown_hours: int = 12
    notify_on_boot: bool = True


@dataclass(slots=True)
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    allowed_cidrs: tuple[str, ...] = DEFAULT_ALLOWED_CIDRS
    preview_width: int = 1280
    preview_height: int = 720
    preview_fps: int = 10
    manual_capture_watermark_default: bool = True


@dataclass(slots=True)
class AppConfig:
    location: LocationConfig = field(default_factory=LocationConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    watermark: WatermarkConfig = field(default_factory=WatermarkConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    bluesky: BlueskyConfig = field(default_factory=BlueskyConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    web: WebConfig = field(default_factory=WebConfig)
    base_dir: Path = Path(".")


def load_dotenv(env_path: Path) -> None:
    """Load key=value pairs from a .env file into os.environ without overwriting existing vars."""
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_path(raw: str | Path, base_dir: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return (base_dir / path).resolve()
    return path


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load AppConfig from TOML file and environment variables."""
    resolved_path: Path | None = None
    if config_path is not None:
        resolved_path = Path(config_path).expanduser().resolve()
    elif env_cfg := os.environ.get("DAILY_SHOT_CONFIG"):
        resolved_path = Path(env_cfg).expanduser().resolve()
    else:
        default_cfg = Path.cwd() / "config.toml"
        if default_cfg.is_file():
            resolved_path = default_cfg.resolve()

    base_dir = resolved_path.parent if resolved_path else Path.cwd().resolve()
    load_dotenv(base_dir / ".env")

    data: dict[str, Any] = {}
    if resolved_path is not None:
        if not resolved_path.is_file():
            raise FileNotFoundError(f"Config file not found: {resolved_path}")
        with resolved_path.open("rb") as f:
            data = tomllib.load(f)

    loc_raw = data.get("location", {})
    location = LocationConfig(
        latitude=float(loc_raw.get("latitude", -19.9167)),
        longitude=float(loc_raw.get("longitude", -43.9345)),
        timezone=str(loc_raw.get("timezone", "America/Sao_Paulo")),
        sunset_offset_minutes=int(loc_raw.get("sunset_offset_minutes", 0)),
    )

    sched_raw = data.get("schedule", {})
    shared_interval = int(sched_raw.get("interval_minutes", 15))
    schedule = ScheduleConfig(
        shots_before_sunset=int(sched_raw.get("shots_before_sunset", 6)),
        shots_after_sunset=int(sched_raw.get("shots_after_sunset", 6)),
        interval_before_minutes=int(sched_raw.get("interval_before_minutes", shared_interval)),
        interval_after_minutes=int(sched_raw.get("interval_after_minutes", shared_interval)),
        include_sunset_shot=bool(sched_raw.get("include_sunset_shot", True)),
        slot_grace_minutes=int(sched_raw.get("slot_grace_minutes", 5)),
    )

    cam_raw = data.get("camera", {})
    controls = dict(DEFAULT_CONTROLS)
    if "controls" in cam_raw:
        controls.update({k: int(v) for k, v in cam_raw["controls"].items()})
    camera = CameraConfig(
        device=str(cam_raw.get("device", "auto")),
        device_name_match=str(cam_raw.get("device_name_match", "Fic760xUsbCamera")),
        width=int(cam_raw.get("width", 4000)),
        height=int(cam_raw.get("height", 3000)),
        input_format=str(cam_raw.get("input_format", "mjpeg")),
        warmup_seconds=float(cam_raw.get("warmup_seconds", 3.0)),
        controls_override_file=_resolve_path(
            cam_raw.get("controls_override_file", "./data/camera_controls.json"), base_dir
        ),
        controls=controls,
    )

    wm_raw = data.get("watermark", {})
    watermark = WatermarkConfig(
        enabled=bool(wm_raw.get("enabled", True)),
        format=str(wm_raw.get("format", "%Y-%m-%d %H:%M:%S")),
        position=str(wm_raw.get("position", "bottom-right")),
        font_path=str(wm_raw.get("font_path", "auto")),
        font_size_percent=float(wm_raw.get("font_size_percent", 2.2)),
        show_background=bool(wm_raw.get("show_background", True)),
        jpeg_quality=int(wm_raw.get("jpeg_quality", 95)),
    )

    stor_raw = data.get("storage", {})
    storage = StorageConfig(
        archive_dir=_resolve_path(stor_raw.get("archive_dir", "./data/shots"), base_dir),
        state_file=_resolve_path(stor_raw.get("state_file", "./data/state.json"), base_dir),
        local_retention_days=int(stor_raw.get("local_retention_days", 14)),
        min_free_mb=int(stor_raw.get("min_free_mb", 500)),
    )

    bak_raw = data.get("backup", {})
    backup = BackupConfig(
        enabled=bool(bak_raw.get("enabled", True)),
        rclone_remote=str(bak_raw.get("rclone_remote", "gdrive")),
        rclone_path=str(bak_raw.get("rclone_path", "")),
        drive_root_folder_id=str(
            bak_raw.get("drive_root_folder_id", "")
        ),
        max_retries=int(bak_raw.get("max_retries", 3)),
        retry_delay_seconds=int(bak_raw.get("retry_delay_seconds", 10)),
        retry_cooldown_minutes=int(bak_raw.get("retry_cooldown_minutes", 5)),
    )

    bsky_raw = data.get("bluesky", {})
    bluesky = BlueskyConfig(
        enabled=bool(bsky_raw.get("enabled", True)),
        pds_url=str(bsky_raw.get("pds_url", "https://bsky.social")).rstrip("/"),
        handle=os.environ.get(
            "BLUESKY_HANDLE", str(bsky_raw.get("handle", "dailyshot.bsky.social"))
        ),
        app_password=os.environ.get(
            "BLUESKY_APP_PASSWORD", str(bsky_raw.get("app_password", ""))
        ),
        publish_offset_after_window_minutes=int(
            bsky_raw.get("publish_offset_after_window_minutes", 5)
        ),
        max_bytes=int(bsky_raw.get("max_bytes", 950_000)),
        max_dimension=int(bsky_raw.get("max_dimension", 2048)),
        initial_quality=int(bsky_raw.get("initial_quality", 92)),
        min_quality=int(bsky_raw.get("min_quality", 70)),
        location_name=str(bsky_raw.get("location_name", "Belo Horizonte")),
        post_template=str(
            bsky_raw.get("post_template", "Sunset from {location} — {date} at {time}")
        ),
        alt_template=str(
            bsky_raw.get(
                "alt_template",
                "Outdoor horizon view from {location} captured on {date} at {time} "
                "(local sunset {sunset_time}).",
            )
        ),
    )

    alerts_raw = data.get("alerts", {})
    alerts = AlertsConfig(
        enabled=bool(alerts_raw.get("enabled", True)),
        recipient_handle=str(alerts_raw.get("recipient_handle", "")),
        alert_after_failures=int(alerts_raw.get("alert_after_failures", 2)),
        cooldown_hours=int(alerts_raw.get("cooldown_hours", 12)),
        notify_on_boot=bool(alerts_raw.get("notify_on_boot", True)),
    )

    web_raw = data.get("web", {})
    raw_cidrs = web_raw.get("allowed_cidrs")
    allowed_cidrs = tuple(str(c) for c in raw_cidrs) if raw_cidrs is not None else DEFAULT_ALLOWED_CIDRS
    web = WebConfig(
        enabled=bool(web_raw.get("enabled", True)),
        host=str(web_raw.get("host", "127.0.0.1")),
        port=int(web_raw.get("port", 8080)),
        allowed_cidrs=allowed_cidrs,
        preview_width=int(web_raw.get("preview_width", 1280)),
        preview_height=int(web_raw.get("preview_height", 720)),
        preview_fps=int(web_raw.get("preview_fps", 10)),
        manual_capture_watermark_default=bool(
            web_raw.get("manual_capture_watermark_default", True)
        ),
    )

    return AppConfig(
        location=location,
        schedule=schedule,
        camera=camera,
        watermark=watermark,
        storage=storage,
        backup=backup,
        bluesky=bluesky,
        alerts=alerts,
        web=web,
        base_dir=base_dir,
    )
