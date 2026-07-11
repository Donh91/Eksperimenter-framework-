#!/usr/bin/env python3
"""Fetch and normalize DeFiLlama stablecoin supply + DEX daily volume.

Truth-layer properties:
- Six entities, twelve public keyless endpoints.
- Raw JSON is preserved unchanged and checksummed.
- No TVL substitution.
- No interpolation or backdating.
- 3D/7D changes require exact prior UTC dates.
- A failed endpoint produces an auditable artifact and a non-zero final exit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ENTITIES = ["TOTAL", "Ethereum", "Solana", "BSC", "Base", "Arbitrum"]
START_DATE = date(2024, 1, 1)
USER_AGENT = "Investering-Truth-Layer-Recovery/1.1"

FIELDS = [
    "date_utc",
    "chain",
    "stablecoin_supply",
    "stablecoin_supply_change_3d",
    "stablecoin_supply_change_7d",
    "dex_volume",
    "dex_volume_change_3d",
    "dex_volume_change_7d",
    "dex_volume_to_stablecoin_supply",
    "proxy_name",
    "direct_or_derived",
    "source",
    "source_timestamp",
    "source_verified_timestamp",
    "data_quality",
    "source_status",
    "notes",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_json(url: str, attempts: int = 4) -> tuple[bytes, Any]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=180) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
                content_type = response.headers.get("content-type", "")
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            if not raw:
                raise RuntimeError("empty response body")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"non-JSON response content_type={content_type!r}: {exc}"
                ) from exc
            return raw, payload
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(30, 2**attempt))
    raise RuntimeError(f"request failed after {attempts} attempts: {last_error}")


def as_date(value: Any) -> date | None:
    try:
        if isinstance(value, (int, float)):
            seconds = float(value)
            if abs(seconds) > 10_000_000_000:
                seconds /= 1000.0
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
        text = str(value).strip()
        if text.isdigit():
            return as_date(int(text))
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).date()
    except Exception:
        return None


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def nested_value(obj: Any, paths: Iterable[tuple[str, ...]]) -> float | None:
    for path in paths:
        cur = obj
        ok = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                ok = False
                break
            cur = cur[key]
        if ok:
            value = numeric(cur)
            if value is not None:
                return value
    return None


def extract_supply(payload: Any) -> dict[date, float]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data", payload.get("chart", []))
    else:
        rows = []

    result: dict[date, float] = {}
    if not isinstance(rows, list):
        return result

    value_paths = [
        ("totalCirculatingUSD", "peggedUSD"),
        ("totalCirculating", "peggedUSD"),
        ("totalCirculatingUSD",),
        ("totalCirculating",),
        ("value",),
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = as_date(row.get("date", row.get("timestamp", row.get("time"))))
        value = nested_value(row, value_paths)
        if d and value is not None:
            result[d] = value
    return result


def extract_dex(payload: Any) -> dict[date, float]:
    if not isinstance(payload, dict):
        return {}
    chart = payload.get("totalDataChart", payload.get("data", []))
    result: dict[date, float] = {}
    if not isinstance(chart, list):
        return result

    for row in chart:
        d: date | None = None
        value: float | None = None
        if isinstance(row, list) and len(row) >= 2:
            d = as_date(row[0])
            value = numeric(row[1])
        elif isinstance(row, dict):
            d = as_date(row.get("date", row.get("timestamp", row.get("time"))))
            value = numeric(
                row.get("value", row.get("volume", row.get("totalVolume")))
            )
        if d and value is not None:
            result[d] = value
    return result


def exact_pct(series: dict[date, float], d: date, days: int) -> str:
    current = series.get(d)
    prior = series.get(d - timedelta(days=days))
    if current is None or prior in (None, 0):
        return "DATA_MISSING"
    return f"{(current / prior - 1.0) * 100.0:.10f}".rstrip("0").rstrip(".")


def fmt(value: float | None) -> str:
    if value is None:
        return "DATA_MISSING"
    return f"{value:.10f}".rstrip("0").rstrip(".")


def endpoint_urls(entity: str) -> tuple[str, str]:
    encoded = urllib.parse.quote(entity, safe="")
    supply_url = (
        "https://stablecoins.llama.fi/stablecoincharts/all"
        if entity == "TOTAL"
        else f"https://stablecoins.llama.fi/stablecoincharts/{encoded}"
    )
    dex_url = (
        "https://api.llama.fi/overview/dexs"
        if entity == "TOTAL"
        else f"https://api.llama.fi/overview/dexs/{encoded}"
    ) + (
        "?excludeTotalDataChart=false"
        "&excludeTotalDataChartBreakdown=true"
        "&dataType=dailyVolume"
    )
    return supply_url, dex_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="defillama_output")
    args = parser.parse_args()

    out = Path(args.output_dir)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    verified_at = datetime.now(timezone.utc).replace(microsecond=0)
    verified_at_z = verified_at.isoformat().replace("+00:00", "Z")
    latest_complete = verified_at.date() - timedelta(days=1)

    all_rows: list[dict[str, str]] = []
    manifest: list[dict[str, str]] = []
    coverage: list[dict[str, Any]] = []

    for entity in ENTITIES:
        supply_url, dex_url = endpoint_urls(entity)
        datasets: dict[str, tuple[str, bytes, Any]] = {}

        for metric, url in (
            ("stablecoin_supply", supply_url),
            ("dex_volume", dex_url),
        ):
            filename = f"{metric}_{entity}.json"
            try:
                raw, payload = request_json(url)
                raw_path = raw_dir / filename
                raw_path.write_bytes(raw)
                datasets[metric] = (url, raw, payload)
                manifest.append(
                    {
                        "entity": entity,
                        "metric": metric,
                        "url": url,
                        "raw_filename": filename,
                        "bytes": str(len(raw)),
                        "sha256": sha256_bytes(raw),
                        "fetch_status": "PASS",
                        "source_status": "PUBLIC_SOURCE_BACKED",
                        "notes": "",
                    }
                )
            except Exception as exc:
                manifest.append(
                    {
                        "entity": entity,
                        "metric": metric,
                        "url": url,
                        "raw_filename": filename,
                        "bytes": "0",
                        "sha256": "",
                        "fetch_status": "FAIL",
                        "source_status": "DATA_MISSING",
                        "notes": str(exc),
                    }
                )

        supply = (
            extract_supply(datasets["stablecoin_supply"][2])
            if "stablecoin_supply" in datasets
            else {}
        )
        dex = (
            extract_dex(datasets["dex_volume"][2])
            if "dex_volume" in datasets
            else {}
        )

        all_dates = sorted(set(supply) | set(dex))
        dates = [d for d in all_dates if START_DATE <= d <= latest_complete]

        for d in dates:
            s = supply.get(d)
            v = dex.get(d)
            if s is not None and v is not None:
                quality = "COMPLETE_MATCHED_DATE"
            elif s is not None:
                quality = "SUPPLY_ONLY"
            else:
                quality = "DEX_ONLY"

            ratio = None if s in (None, 0) or v is None else v / s
            all_rows.append(
                {
                    "date_utc": d.isoformat(),
                    "chain": entity,
                    "stablecoin_supply": fmt(s),
                    "stablecoin_supply_change_3d": exact_pct(supply, d, 3),
                    "stablecoin_supply_change_7d": exact_pct(supply, d, 7),
                    "dex_volume": fmt(v),
                    "dex_volume_change_3d": exact_pct(dex, d, 3),
                    "dex_volume_change_7d": exact_pct(dex, d, 7),
                    "dex_volume_to_stablecoin_supply": fmt(ratio),
                    "proxy_name": "STABLECOIN_DEPLOYMENT_PROXY",
                    "direct_or_derived": (
                        "DIRECT_SUPPLY_AND_DEX|DERIVED_CHANGES_AND_RATIO"
                    ),
                    "source": f"DeFiLlama|{supply_url}|{dex_url}",
                    "source_timestamp": f"{d.isoformat()}T00:00:00Z",
                    "source_verified_timestamp": verified_at_z,
                    "data_quality": quality,
                    "source_status": "PUBLIC_SOURCE_BACKED",
                    "notes": (
                        "Change fields are percent changes and require exact "
                        "prior UTC dates. No interpolation."
                    ),
                }
            )

        entity_rows = [r for r in all_rows if r["chain"] == entity]
        matched_rows = sum(
            1 for r in entity_rows if r["data_quality"] == "COMPLETE_MATCHED_DATE"
        )
        coverage.append(
            {
                "entity": entity,
                "supply_dates_all": len(supply),
                "dex_dates_all": len(dex),
                "normalized_rows_2024_current": len(entity_rows),
                "matched_rows": matched_rows,
                "first_normalized_date": (
                    entity_rows[0]["date_utc"] if entity_rows else "DATA_MISSING"
                ),
                "last_normalized_date": (
                    entity_rows[-1]["date_utc"] if entity_rows else "DATA_MISSING"
                ),
                "supply_parse_status": "PASS" if supply else "FAIL",
                "dex_parse_status": "PASS" if dex else "FAIL",
            }
        )

    all_rows.sort(key=lambda r: (r["chain"], r["date_utc"]))

    with (out / "STABLECOIN_DEPLOYMENT_PROXY_HISTORY.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    manifest_fields = [
        "entity",
        "metric",
        "url",
        "raw_filename",
        "bytes",
        "sha256",
        "fetch_status",
        "source_status",
        "notes",
    ]
    with (out / "STABLECOIN_RAW_SOURCE_MANIFEST.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=manifest_fields)
        writer.writeheader()
        writer.writerows(manifest)

    with (out / "STABLECOIN_COVERAGE_AUDIT.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        fields = list(coverage[0].keys()) if coverage else [
            "entity",
            "supply_dates_all",
            "dex_dates_all",
            "normalized_rows_2024_current",
            "matched_rows",
            "first_normalized_date",
            "last_normalized_date",
            "supply_parse_status",
            "dex_parse_status",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(coverage)

    checksum_lines = []
    for path in sorted(raw_dir.glob("*.json")):
        checksum_lines.append(f"{sha256_bytes(path.read_bytes())}  {path.name}")
    (out / "RAW_CHECKSUMS.sha256").write_text(
        "\n".join(checksum_lines) + ("\n" if checksum_lines else ""),
        encoding="utf-8",
    )

    raw_passes = sum(1 for r in manifest if r["fetch_status"] == "PASS")
    raw_failures = 12 - raw_passes
    parse_failures = [
        c["entity"]
        for c in coverage
        if c["supply_parse_status"] != "PASS"
        or c["dex_parse_status"] != "PASS"
    ]
    entity_row_failures = [
        c["entity"] for c in coverage if c["normalized_rows_2024_current"] == 0
    ]
    final_status = (
        "PASS"
        if raw_passes == 12
        and not parse_failures
        and not entity_row_failures
        and len(all_rows) > 0
        else "FAIL"
    )

    report = [
        "# Stablecoin History Validation",
        "",
        f"- Verified: {verified_at_z}",
        f"- Latest complete UTC date allowed: {latest_complete}",
        f"- Normalized rows: {len(all_rows)}",
        f"- Raw endpoint passes: {raw_passes}/12",
        f"- Final status: {final_status}",
        "- Proxy: STABLECOIN_DEPLOYMENT_PROXY",
        "- Supply is not velocity.",
        "- DEX volume / supply is an activity proxy only.",
        "- TVL substitution: NO",
        "- Interpolation: NO",
        "",
        "## Coverage",
        "",
    ]
    for c in coverage:
        report.append(
            "- {entity}: supply_dates={supply_dates_all}, "
            "dex_dates={dex_dates_all}, normalized={normalized_rows_2024_current}, "
            "matched={matched_rows}, first={first_normalized_date}, "
            "last={last_normalized_date}, supply_parse={supply_parse_status}, "
            "dex_parse={dex_parse_status}".format(**c)
        )
    if parse_failures:
        report += ["", f"- Parse failures: {', '.join(parse_failures)}"]
    if entity_row_failures:
        report += ["", f"- Empty normalized entities: {', '.join(entity_row_failures)}"]

    (out / "STABLECOIN_HISTORY_VALIDATION.md").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )

    machine_report = {
        "verified_at": verified_at_z,
        "latest_complete_utc_date": latest_complete.isoformat(),
        "raw_endpoint_passes": raw_passes,
        "raw_endpoint_failures": raw_failures,
        "normalized_rows": len(all_rows),
        "parse_failures": parse_failures,
        "empty_normalized_entities": entity_row_failures,
        "final_status": final_status,
    }
    (out / "STABLECOIN_HISTORY_VALIDATION.json").write_text(
        json.dumps(machine_report, indent=2) + "\n", encoding="utf-8"
    )

    raw_zip = out / "DEFILLAMA_STABLECOIN_AND_DEX_HISTORY_6_ENTITIES.zip"
    with zipfile.ZipFile(raw_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(raw_dir.glob("*.json")):
            z.write(path, arcname=path.name)

    print(json.dumps(machine_report, indent=2))
    return 0 if final_status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
