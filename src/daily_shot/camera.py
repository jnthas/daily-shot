"""V4L2 camera discovery, locking, two-phase controls, capture, and calibration."""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import CameraConfig, WatermarkConfig
from .image_prep import apply_timestamp_watermark

logger = logging.getLogger(__name__)

_MODE_CONTROLS = (
    "focus_automatic_continuous",
    "auto_exposure",
    "white_balance_automatic",
)

_CTRL_RE = re.compile(
    r"^\s*([a-z0-9_]+)\s+0x[0-9a-f]+\s+\((int|bool|menu)\)\s*:\s*(.+)$"
)
_MENU_ITEM_RE = re.compile(r"^\s+(\d+):\s*(.+)$")


class CameraError(RuntimeError):
    """Raised when camera discovery, locking, configuration, or capture fails."""


@dataclass(slots=True)
class CameraControl:
    name: str
    kind: str
    group: str
    value: int
    default: int
    min_val: int | None = None
    max_val: int | None = None
    step: int | None = None
    inactive: bool = False
    menu_items: dict[int, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "group": self.group,
            "value": self.value,
            "default": self.default,
            "min": self.min_val,
            "max": self.max_val,
            "step": self.step,
            "inactive": self.inactive,
            "menu_items": {str(k): v for k, v in self.menu_items.items()},
        }


def parse_v4l2_devices(list_output: str, name_match: str) -> str | None:
    """Return the first /dev/video* path matching name_match in `v4l2-ctl --list-devices` output."""
    in_matching_block = False
    for line in list_output.splitlines():
        if not line.strip():
            in_matching_block = False
            continue
        if not line.startswith(("\t", " ")):
            in_matching_block = name_match.lower() in line.lower()
            continue
        if in_matching_block:
            candidate = line.strip()
            if candidate.startswith("/dev/video"):
                return candidate
    return None


def parse_v4l2_controls(ctrls_output: str) -> list[CameraControl]:
    """Parse `v4l2-ctl --list-ctrls-menus` output into structured CameraControl items."""
    controls: list[CameraControl] = []
    current_group = "Controls"
    last_ctrl: CameraControl | None = None

    for line in ctrls_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith((" ", "\t")):
            current_group = stripped
            last_ctrl = None
            continue

        ctrl_match = _CTRL_RE.match(line)
        if ctrl_match:
            name, kind, meta = ctrl_match.groups()
            kv: dict[str, int] = {}
            for key in ("min", "max", "step", "default", "value"):
                m = re.search(rf"\b{key}=(-?\d+)", meta)
                if m:
                    kv[key] = int(m.group(1))
            inactive = "inactive" in meta
            ctrl = CameraControl(
                name=name,
                kind=kind,
                group=current_group,
                value=kv.get("value", kv.get("default", 0)),
                default=kv.get("default", 0),
                min_val=kv.get("min"),
                max_val=kv.get("max"),
                step=kv.get("step"),
                inactive=inactive,
            )
            controls.append(ctrl)
            last_ctrl = ctrl
            continue

        if last_ctrl is not None and last_ctrl.kind == "menu":
            menu_match = _MENU_ITEM_RE.match(line)
            if menu_match:
                idx, label = menu_match.groups()
                last_ctrl.menu_items[int(idx)] = label.strip()

    return controls


def resolve_device(config: CameraConfig) -> str:
    """Resolve the V4L2 device path, auto-discovering via v4l2-ctl when set to 'auto'."""
    if config.device and config.device.lower() != "auto":
        return config.device

    try:
        proc = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise CameraError(f"Failed to list V4L2 devices: {exc}") from exc

    device = parse_v4l2_devices(proc.stdout, config.device_name_match)
    if not device:
        raise CameraError(
            f"No V4L2 device matching '{config.device_name_match}' found in:\n{proc.stdout}"
        )
    return device


def parse_resolution(spec: str) -> tuple[int, int]:
    """Parse a 'WIDTHxHEIGHT' resolution string into positive integers."""
    m = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", spec)
    if not m:
        raise ValueError(f"Invalid resolution '{spec}'; expected WIDTHxHEIGHT (e.g. 1920x1080)")
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        raise ValueError(f"Resolution dimensions must be positive: '{spec}'")
    return w, h


def parse_v4l2_resolutions(formats_output: str, input_format: str = "mjpeg") -> list[tuple[int, int]]:
    """Parse supported discrete WxH resolutions for input_format from `v4l2-ctl --list-formats-ext`."""
    fmt_aliases = {"mjpeg": "MJPG", "mjpg": "MJPG", "yuyv": "YUYV"}
    target_fourcc = fmt_aliases.get(input_format.lower(), input_format.upper())

    in_target_fmt = False
    seen: set[tuple[int, int]] = set()
    resolutions: list[tuple[int, int]] = []
    for line in formats_output.splitlines():
        fmt_match = re.match(r"^\s*\[\d+\]:\s*'([^']+)'", line)
        if fmt_match:
            in_target_fmt = fmt_match.group(1).upper() == target_fourcc
            continue
        if in_target_fmt:
            size_match = re.search(r"Size:\s*Discrete\s+(\d+)x(\d+)", line)
            if size_match:
                pair = (int(size_match.group(1)), int(size_match.group(2)))
                if pair not in seen:
                    seen.add(pair)
                    resolutions.append(pair)
    resolutions.sort(key=lambda wh: (wh[0] * wh[1], wh[0]), reverse=True)
    return resolutions


def list_camera_controls(device: str) -> list[CameraControl]:
    """Query live V4L2 controls and menu options for a device."""
    try:
        proc = subprocess.run(
            ["v4l2-ctl", "-d", device, "--list-ctrls-menus"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        stderr = getattr(exc, "stderr", "")
        raise CameraError(f"Failed to query controls on {device}: {exc} {stderr}") from exc
    return parse_v4l2_controls(proc.stdout)


def list_camera_resolutions(device: str, input_format: str = "mjpeg") -> list[tuple[int, int]]:
    """Query supported discrete resolutions for input_format from the V4L2 device."""
    try:
        proc = subprocess.run(
            ["v4l2-ctl", "-d", device, "--list-formats-ext"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        stderr = getattr(exc, "stderr", "")
        raise CameraError(f"Failed to query formats on {device}: {exc} {stderr}") from exc
    return parse_v4l2_resolutions(proc.stdout, input_format=input_format)


def get_camera_info(config: CameraConfig) -> dict[str, Any]:
    """Return resolved device path, controls output, and formats output for debugging."""
    device = resolve_device(config)
    ctrls_proc = subprocess.run(
        ["v4l2-ctl", "-d", device, "--list-ctrls-menus"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    fmts_proc = subprocess.run(
        ["v4l2-ctl", "-d", device, "--list-formats-ext"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return {
        "device": device,
        "controls_text": ctrls_proc.stdout,
        "formats_text": fmts_proc.stdout,
        "controls": [c.to_dict() for c in parse_v4l2_controls(ctrls_proc.stdout)],
    }


def load_effective_controls(config: CameraConfig) -> dict[str, int]:
    """Merge configured [camera.controls] with persisted Web UI overrides if present."""
    controls = dict(config.controls)
    override_file = config.controls_override_file
    if override_file.is_file():
        try:
            raw = json.loads(override_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                controls.update({str(k): int(v) for k, v in raw.items()})
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Ignoring invalid controls override file %s: %s", override_file, exc)
    return controls


def save_control_overrides(override_file: Path, controls: dict[str, int]) -> None:
    """Persist tuned camera controls atomically for future scheduled captures."""
    override_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = override_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(controls, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(override_file)


def _set_ctrl_batch(device: str, controls: dict[str, int]) -> None:
    if not controls:
        return
    ctrl_arg = ",".join(f"{k}={v}" for k, v in controls.items())
    try:
        subprocess.run(
            ["v4l2-ctl", "-d", device, f"--set-ctrl={ctrl_arg}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        stderr = getattr(exc, "stderr", "")
        raise CameraError(f"Failed to apply V4L2 controls ({ctrl_arg}) on {device}: {exc} {stderr}") from exc


def apply_controls(device: str, controls: dict[str, int]) -> None:
    """Apply V4L2 controls in two phases, skipping dependent controls locked by auto modes."""
    filtered = dict(controls)
    if filtered.get("white_balance_automatic") == 1:
        filtered.pop("white_balance_temperature", None)
    if filtered.get("auto_exposure", 3) != 1:
        filtered.pop("exposure_time_absolute", None)
    if filtered.get("focus_automatic_continuous") == 1:
        filtered.pop("focus_absolute", None)

    mode_ctrls = {k: v for k, v in filtered.items() if k in _MODE_CONTROLS}
    value_ctrls = {k: v for k, v in filtered.items() if k not in _MODE_CONTROLS}
    _set_ctrl_batch(device, mode_ctrls)
    _set_ctrl_batch(device, value_ctrls)


def _lock_paths(lock_dir: Path) -> tuple[Path, Path]:
    return lock_dir / "camera.lock", lock_dir / "capture.requested"


def is_capture_requested(lock_dir: Path) -> bool:
    """Return True if another process requested exclusive camera access."""
    _, req_path = _lock_paths(lock_dir)
    return req_path.exists()


def cleanup_stale_camera_signals(lock_dir: Path) -> None:
    """Remove leftover capture.requested signal after an unclean shutdown."""
    _, req_path = _lock_paths(lock_dir)
    req_path.unlink(missing_ok=True)


@contextlib.contextmanager
def camera_exclusive_lock(
    lock_dir: Path | None,
    *,
    request_capture: bool = False,
    blocking: bool = True,
) -> Iterator[None]:
    """Coordinate exclusive /dev/videoX access between preview streaming and captures."""
    if lock_dir is None:
        yield
        return

    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path, req_path = _lock_paths(lock_dir)
    if request_capture:
        req_path.write_text("1", encoding="utf-8")

    lock_fp = lock_path.open("a+", encoding="utf-8")
    try:
        flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock_fp.fileno(), flags)
        try:
            yield
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
    finally:
        lock_fp.close()
        if request_capture:
            req_path.unlink(missing_ok=True)


def build_archive_path(archive_dir: Path, timestamp: datetime) -> Path:
    """Build daily subfolder path: <archive_dir>/YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS.jpg."""
    day_str = timestamp.strftime("%Y-%m-%d")
    stamp = timestamp.strftime("%Y-%m-%d_%H-%M-%S")
    return archive_dir / day_str / f"{stamp}.jpg"


def parse_shot_timestamp(path: Path) -> datetime | None:
    """Extract capture datetime from YYYY-MM-DD_HH-MM-SS.jpg (or legacy shot_YYYYMMDD_HHMMSS.jpg)."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.jpe?g$", path.name, re.IGNORECASE)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            return None
    m_legacy = re.match(r"^shot_(\d{8}_\d{6})\.jpe?g$", path.name, re.IGNORECASE)
    if m_legacy:
        try:
            return datetime.strptime(m_legacy.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            return None
    return None


def capture_photo(
    config: CameraConfig,
    output_path: Path,
    *,
    width: int | None = None,
    height: int | None = None,
    controls_override: dict[str, int] | None = None,
    watermark_config: WatermarkConfig | None = None,
    watermark: bool = False,
    timestamp: datetime | None = None,
    lock_dir: Path | None = None,
) -> Path:
    """Acquire camera lock, apply controls, warm up AE/AWB, capture MJPEG, and optionally watermark."""
    shot_ts = timestamp or datetime.now()
    target_w = width if width is not None else config.width
    target_h = height if height is not None else config.height
    controls = controls_override if controls_override is not None else load_effective_controls(config)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.stem}.tmp.jpg")

    with camera_exclusive_lock(lock_dir, request_capture=True):
        device = resolve_device(config)
        apply_controls(device, controls)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "v4l2",
            "-input_format",
            config.input_format,
            "-video_size",
            f"{target_w}x{target_h}",
            "-i",
            device,
            "-ss",
            f"{max(0.0, config.warmup_seconds):.2f}",
            "-frames:v",
            "1",
            "-c:v",
            "copy",
            "-f",
            "image2",
            "-update",
            "1",
            "-y",
            str(tmp_path),
        ]

        logger.info(
            "Capturing %dx%d %s from %s (warmup %.1fs) -> %s",
            target_w,
            target_h,
            config.input_format,
            device,
            config.warmup_seconds,
            output_path,
        )
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=max(30, int(config.warmup_seconds) + 25),
            )
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            tmp_path.unlink(missing_ok=True)
            stderr = getattr(exc, "stderr", "")
            raise CameraError(f"ffmpeg capture failed on {device}: {exc} {stderr}") from exc

    if not tmp_path.is_file() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        raise CameraError(f"Captured file is missing or empty: {tmp_path}")

    if watermark and watermark_config is not None:
        apply_timestamp_watermark(tmp_path, shot_ts, watermark_config)

    tmp_path.replace(output_path)
    return output_path


def calibrate_focus(
    config: CameraConfig,
    output_dir: Path,
    focus_values: list[int],
    *,
    lock_dir: Path | None = None,
) -> list[Path]:
    """Capture labeled test shots across focus_absolute values for manual infinity calibration."""
    output_dir.mkdir(parents=True, exist_ok=True)
    base_controls = load_effective_controls(config)
    captured: list[Path] = []
    for focus_val in focus_values:
        controls = {
            **base_controls,
            "focus_automatic_continuous": 0,
            "focus_absolute": focus_val,
        }
        out_path = output_dir / f"focus_{focus_val:04d}.jpg"
        capture_photo(
            config,
            out_path,
            controls_override=controls,
            watermark=False,
            lock_dir=lock_dir,
        )
        captured.append(out_path)
    return captured
