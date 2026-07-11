#!/usr/bin/env python3
"""Normalize and strictly validate a TradingView CRYPTOCAP:BTC.D daily CSV.

No interpolation, no backdating, no current-day inclusion.
PASS requires continuous daily coverage from 2023-01-01 through the latest
complete UTC date.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATE_CANDIDATES = ("time", "timestamp", "date", "datetime")
CLOSE_CANDIDATES = ("close", "btc.d", "cryptocap:btc.d", "btc_d", "value", "series")

OUTPUT_FIELDS = [
    "date_utc",
    "btc_d_close",
    "source_symbol",
    "source_provider",
    "source_convention",
    "settled_timezone",
    "source_timestamp",
    "source_verified_timestamp",
    "print_status",
    "revision_delta",
    "data_quality",
    "source_status",
    "notes",
]


def canonical(name: str) -> str:
    return re.sub(r"[^a-z0-9:.]+", "", name.strip().lower())


def choose_column(headers: list[str], candidates: tuple[str, ...], kind: str) -> str:
    normalized = {canonical(header): header for header in headers}
    for candidate in candidates:
        if canonical(candidate) in normalized:
            return normalized[canonical(candidate)]
    if kind == "close":
        close_like = [header for header in headers if "close" in canonical(header)]
        if len(close_like) == 1:
            return close_like[0]
    raise ValueError(f"Could not identify {kind} column. Headers: {headers}")


def parse_timestamp(raw: str) -> datetime:
    value = raw.strip()
    if not value:
        raise ValueError("empty timestamp")
    if re.fullmatch(r"-?\d+(\.\d+)?", value):
        number = float(value)
        if abs(number) > 10_000_000_000:
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc)

    iso = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(f"unrecognized timestamp: {value}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_decimal(raw: str) -> float:
    value = float(raw.strip().replace("%", "").replace(",", ""))
    if not 0.0 <= value <= 100.0:
        raise ValueError(f"dominance outside 0..100: {value}")
    return value


def dispersed_anchors(dates: list[date], count: int = 12) -> list[date]:
    if len(dates) < count:
        return dates
    indexes = sorted(
        {round(i * (len(dates) - 1) / (count - 1)) for i in range(count)}
    )
    return [dates[index] for index in indexes]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--json-report")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)

    verified_at = datetime.now(timezone.utc).replace(microsecond=0)
    latest_complete = verified_at.date() - timedelta(days=1)
    required_start = date(2023, 1, 1)

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        time_column = choose_column(headers, DATE_CANDIDATES, "timestamp")
        close_column = choose_column(headers, CLOSE_CANDIDATES, "close")
        raw_rows = list(reader)

    by_date: dict[date, dict[str, Any]] = {}
    issues: list[str] = []
    duplicates: list[str] = []
    excluded_current_or_future = 0
    excluded_pre_start = 0

    for row_number, row in enumerate(raw_rows, start=2):
        try:
            timestamp = parse_timestamp(row.get(time_column, ""))
            close = parse_decimal(row.get(close_column, ""))
        except Exception as exc:
            issues.append(f"row {row_number}: {exc}")
            continue

        day = timestamp.date()
        if day > latest_complete:
            excluded_current_or_future += 1
            continue
        if day < required_start:
            excluded_pre_start += 1
            continue
        if day in by_date:
            duplicates.append(day.isoformat())
            continue
        by_date[day] = {"timestamp": timestamp, "close": close}

    dates = sorted(by_date)
    gaps: list[str] = []
    if dates:
        expected = required_start
        while expected <= latest_complete:
            if expected not in by_date:
                gaps.append(expected.isoformat())
            expected += timedelta(days=1)

    rows = []
    verified_at_z = verified_at.isoformat().replace("+00:00", "Z")
    for day in dates:
        item = by_date[day]
        rows.append(
            {
                "date_utc": day.isoformat(),
                "btc_d_close": f"{item['close']:.10f}".rstrip("0").rstrip("."),
                "source_symbol": "CRYPTOCAP:BTC.D",
                "source_provider": "TradingView",
                "source_convention": "DIRECT_SOURCE_CONVENTION",
                "settled_timezone": "UTC",
                "source_timestamp": item["timestamp"].isoformat().replace(
                    "+00:00", "Z"
                ),
                "source_verified_timestamp": verified_at_z,
                "print_status": "SETTLED_COMPLETE_DATE",
                "revision_delta": "NOT_COMPUTABLE",
                "data_quality": "PASS",
                "source_status": "PUBLIC_SOURCE_BACKED",
                "notes": "User-exported unedited TradingView chart CSV.",
            }
        )

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    latest_three_required = [
        latest_complete - timedelta(days=2),
        latest_complete - timedelta(days=1),
        latest_complete,
    ]
    anchors = dispersed_anchors(dates)

    checks = {
        "has_rows": bool(dates),
        "starts_2023_01_01": bool(dates) and dates[0] == required_start,
        "ends_latest_complete_utc": bool(dates) and dates[-1] == latest_complete,
        "latest_three_complete_dates_present": all(
            day in by_date for day in latest_three_required
        ),
        "twelve_anchors_available": len(anchors) == 12,
        "no_duplicate_dates": not duplicates,
        "no_parse_issues": not issues,
        "no_calendar_gaps": not gaps,
        "no_interpolation": True,
        "no_backdating": True,
    }
    status = "PASS" if all(checks.values()) else "FAIL"

    input_sha = hashlib.sha256(input_path.read_bytes()).hexdigest()
    anchor_lines = [
        f"- {day.isoformat()}: {by_date[day]['close']}" for day in anchors
    ]
    report = [
        "# BTC.D Strict Validation Report",
        "",
        f"- Input: `{input_path.name}`",
        f"- Input SHA-256: `{input_sha}`",
        f"- Raw rows: {len(raw_rows)}",
        f"- Normalized rows: {len(rows)}",
        f"- Required start: {required_start}",
        f"- Latest complete UTC date: {latest_complete}",
        f"- First normalized date: {dates[0] if dates else 'DATA_MISSING'}",
        f"- Last normalized date: {dates[-1] if dates else 'DATA_MISSING'}",
        f"- Current/future rows excluded: {excluded_current_or_future}",
        f"- Pre-start rows excluded: {excluded_pre_start}",
        f"- Duplicate dates rejected: {len(duplicates)}",
        f"- Parse issues: {len(issues)}",
        f"- Missing calendar dates: {len(gaps)}",
        f"- Final status: `{status}`",
        "",
        "## Validation gates",
        "",
        *[f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in checks.items()],
        "",
        "## Twelve dispersed source anchors",
        "",
        *anchor_lines,
        "",
        "## Latest three required complete dates",
        "",
        *[
            f"- {day.isoformat()}: "
            + (str(by_date[day]["close"]) if day in by_date else "DATA_MISSING")
            for day in latest_three_required
        ],
        "",
        "## Missing dates",
        "",
        *([f"- {day}" for day in gaps[:200]] or ["- None"]),
        "",
        "## Parse and duplicate issues",
        "",
        *(
            (issues + [f"duplicate date: {day}" for day in duplicates])[:200]
            or ["- None"]
        ),
    ]
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")

    machine = {
        "input_sha256": input_sha,
        "status": status,
        "normalized_rows": len(rows),
        "first_date": dates[0].isoformat() if dates else None,
        "last_date": dates[-1].isoformat() if dates else None,
        "latest_complete_utc_date": latest_complete.isoformat(),
        "missing_calendar_dates": len(gaps),
        "duplicates": len(duplicates),
        "parse_issues": len(issues),
        "checks": checks,
    }
    if args.json_report:
        Path(args.json_report).write_text(
            json.dumps(machine, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(machine, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
