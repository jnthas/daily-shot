"""Pure-Python NOAA solar sunset calculator and burst schedule generator."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from .config import LocationConfig, ScheduleConfig

_SUNSET_ZENITH_DEG = 90.833


@dataclass(frozen=True, slots=True)
class CaptureSlot:
    slot_key: str
    offset_minutes: int
    scheduled_at: datetime


@dataclass(frozen=True, slots=True)
class DailySchedule:
    target_date: date
    sunset_at: datetime
    slots: tuple[CaptureSlot, ...]
    sync_at: datetime
    publish_at: datetime


def resolve_timezone(tz_name: str) -> tzinfo:
    """Resolve an IANA timezone string or 'local' to a tzinfo object."""
    if not tz_name or tz_name.lower() == "local":
        return datetime.now().astimezone().tzinfo or timezone.utc
    return ZoneInfo(tz_name)


def _noaa_solar_params(day_of_year: int, hour_utc: float) -> tuple[float, float]:
    """Return (equation_of_time_minutes, solar_declination_radians) via NOAA formulas."""
    gamma = (2.0 * math.pi / 365.0) * (day_of_year - 1 + (hour_utc - 12.0) / 24.0)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2.0 * gamma)
        - 0.040849 * math.sin(2.0 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2.0 * gamma)
        + 0.000907 * math.sin(2.0 * gamma)
        - 0.002697 * math.cos(3.0 * gamma)
        + 0.00148 * math.sin(3.0 * gamma)
    )
    return eqtime, decl


def _sunset_utc_minutes(day_of_year: int, latitude: float, longitude: float, hour_utc: float) -> float:
    eqtime, decl = _noaa_solar_params(day_of_year, hour_utc)
    lat_rad = math.radians(latitude)
    zenith_rad = math.radians(_SUNSET_ZENITH_DEG)
    cos_ha = (math.cos(zenith_rad) / (math.cos(lat_rad) * math.cos(decl))) - (
        math.tan(lat_rad) * math.tan(decl)
    )
    if cos_ha < -1.0 or cos_ha > 1.0:
        raise ValueError(f"Sun does not set at latitude {latitude} on day {day_of_year}")
    ha_deg = math.degrees(math.acos(cos_ha))
    return 720.0 - 4.0 * (longitude - ha_deg) - eqtime


def calculate_sunset(
    target_date: date,
    latitude: float,
    longitude: float,
    tz: tzinfo,
    offset_minutes: int = 0,
) -> datetime:
    """Calculate local sunset datetime using two-pass NOAA solar equations."""
    day_of_year = target_date.timetuple().tm_yday
    est_min = _sunset_utc_minutes(day_of_year, latitude, longitude, 18.0)
    sunset_utc_min = _sunset_utc_minutes(day_of_year, latitude, longitude, est_min / 60.0)

    utc_midnight = datetime.combine(target_date, time(0, 0), tzinfo=timezone.utc)
    sunset_utc = utc_midnight + timedelta(minutes=sunset_utc_min + offset_minutes)
    local_sunset = sunset_utc.astimezone(tz)

    # If UTC sunset wrapped past UTC midnight relative to local date, shift by +/- 1 day if needed.
    if local_sunset.date() < target_date:
        day_of_year_next = (target_date + timedelta(days=1)).timetuple().tm_yday
        est_min = _sunset_utc_minutes(day_of_year_next, latitude, longitude, 18.0)
        sunset_utc_min = _sunset_utc_minutes(day_of_year_next, latitude, longitude, est_min / 60.0)
        utc_midnight = datetime.combine(target_date + timedelta(days=1), time(0, 0), tzinfo=timezone.utc)
        local_sunset = (utc_midnight + timedelta(minutes=sunset_utc_min + offset_minutes)).astimezone(tz)
    elif local_sunset.date() > target_date:
        day_of_year_prev = (target_date - timedelta(days=1)).timetuple().tm_yday
        est_min = _sunset_utc_minutes(day_of_year_prev, latitude, longitude, 18.0)
        sunset_utc_min = _sunset_utc_minutes(day_of_year_prev, latitude, longitude, est_min / 60.0)
        utc_midnight = datetime.combine(target_date - timedelta(days=1), time(0, 0), tzinfo=timezone.utc)
        local_sunset = (utc_midnight + timedelta(minutes=sunset_utc_min + offset_minutes)).astimezone(tz)

    return local_sunset.replace(second=0, microsecond=0)


def build_daily_schedule(
    target_date: date,
    location: LocationConfig,
    schedule: ScheduleConfig,
    publish_offset_after_window_minutes: int = 5,
) -> DailySchedule:
    """Build the ordered sunset capture slots, post-burst sync time, and Bluesky publish time."""
    tz = resolve_timezone(location.timezone)
    sunset_at = calculate_sunset(
        target_date=target_date,
        latitude=location.latitude,
        longitude=location.longitude,
        tz=tz,
        offset_minutes=location.sunset_offset_minutes,
    )

    offsets: list[int] = []
    for i in range(schedule.shots_before_sunset, 0, -1):
        offsets.append(-i * schedule.interval_before_minutes)
    if schedule.include_sunset_shot:
        offsets.append(0)
    for i in range(1, schedule.shots_after_sunset + 1):
        offsets.append(i * schedule.interval_after_minutes)

    slots = tuple(
        CaptureSlot(
            slot_key=f"{offset:+d}m",
            offset_minutes=offset,
            scheduled_at=sunset_at + timedelta(minutes=offset),
        )
        for offset in offsets
    )

    last_slot_at = slots[-1].scheduled_at if slots else sunset_at
    sync_at = last_slot_at + timedelta(minutes=1)
    publish_at = last_slot_at + timedelta(minutes=publish_offset_after_window_minutes)
    return DailySchedule(
        target_date=target_date,
        sunset_at=sunset_at,
        slots=slots,
        sync_at=sync_at,
        publish_at=publish_at,
    )
