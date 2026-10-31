"""Generate the date-only calendar seed; regional holiday data stays blank."""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path


START_DATE = date(2026, 1, 1)
END_DATE = date(2027, 12, 31)
OUTPUT = Path(__file__).with_name("dim_calendar.csv")
FIELDS = (
    "date_key",
    "day_of_week",
    "is_weekend",
    "month_num",
    "season_name",
    "is_origin_public_holiday",
    "is_destination_public_holiday",
    "school_break_flag",
)


def season_for(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def build_calendar() -> None:
    with OUTPUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        day = START_DATE
        while day <= END_DATE:
            writer.writerow(
                {
                    "date_key": day.isoformat(),
                    "day_of_week": day.isoweekday(),
                    "is_weekend": str(day.isoweekday() >= 6).lower(),
                    "month_num": day.month,
                    "season_name": season_for(day.month),
                    "is_origin_public_holiday": "",
                    "is_destination_public_holiday": "",
                    "school_break_flag": "",
                }
            )
            day += timedelta(days=1)


if __name__ == "__main__":
    build_calendar()
