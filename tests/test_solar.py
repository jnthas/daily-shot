from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from daily_shot.config import LocationConfig, ScheduleConfig
from daily_shot.solar import build_daily_schedule, calculate_sunset


def test_belo_horizonte_sunset_seasons() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    lat, lon = -19.9167, -43.9345

    # Late November (spring/summer): sunset in BH is ~18:16 BRT
    nov_sunset = calculate_sunset(date(2026, 11, 23), lat, lon, tz)
    assert nov_sunset.hour == 18
    assert 13 <= nov_sunset.minute <= 21

    # June winter solstice: earliest sunset in BH (~17:20 BRT)
    jun_sunset = calculate_sunset(date(2026, 6, 21), lat, lon, tz)
    assert jun_sunset.hour == 17
    assert 15 <= jun_sunset.minute <= 25

    # December summer solstice: latest sunset in BH (~18:30 BRT)
    dec_sunset = calculate_sunset(date(2026, 12, 21), lat, lon, tz)
    assert dec_sunset.hour == 18
    assert 26 <= dec_sunset.minute <= 36


def test_sunset_offset_and_burst_slots() -> None:
    loc = LocationConfig(
        latitude=-19.9167,
        longitude=-43.9345,
        timezone="America/Sao_Paulo",
        sunset_offset_minutes=-10,
    )
    sched_cfg = ScheduleConfig(
        shots_before_sunset=6,
        shots_after_sunset=6,
        interval_before_minutes=15,
        interval_after_minutes=15,
        include_sunset_shot=True,
    )
    base_sunset = calculate_sunset(date(2026, 11, 23), loc.latitude, loc.longitude, ZoneInfo(loc.timezone))
    daily = build_daily_schedule(date(2026, 11, 23), loc, sched_cfg, publish_offset_after_window_minutes=5)

    assert daily.sunset_at == base_sunset - timedelta(minutes=10)
    assert len(daily.slots) == 13
    assert daily.slots[0].slot_key == "-90m"
    assert daily.slots[6].slot_key == "+0m"
    assert daily.slots[6].scheduled_at == daily.sunset_at
    assert daily.slots[-1].slot_key == "+90m"
    assert daily.sync_at == daily.slots[-1].scheduled_at + timedelta(minutes=1)
    assert daily.publish_at == daily.slots[-1].scheduled_at + timedelta(minutes=5)


def test_burst_without_center_shot_and_asymmetric_intervals() -> None:
    loc = LocationConfig()
    sched_cfg = ScheduleConfig(
        shots_before_sunset=6,
        shots_after_sunset=6,
        interval_before_minutes=10,
        interval_after_minutes=20,
        include_sunset_shot=False,
    )
    daily = build_daily_schedule(date(2026, 11, 23), loc, sched_cfg)
    assert len(daily.slots) == 12
    assert [s.offset_minutes for s in daily.slots] == [
        -60, -50, -40, -30, -20, -10, 20, 40, 60, 80, 100, 120
    ]
