#!/usr/bin/env python3
"""Fetch point-in-time CoinMarketCap historical snapshots and build a frozen-universe breadth dataset.

This is a research extractor only. It never writes to the canonical framework repository.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://coinmarketcap.com/historical/{date}/"
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
]

EXCLUDED_SYMBOLS = {
    # BTC/ETH and close wrappers/derivatives
    "BTC", "ETH", "WBTC", "BTCB", "RENBTC", "HBTC", "TBTC", "WETH", "STETH", "WSTETH", "RETH", "CBETH", "ANKRETH", "SFRXETH", "FRXETH",
    # USD and fiat stablecoins / cash-like assets
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "PAX", "GUSD", "USDD", "FRAX", "LUSD", "DOLA", "MIM", "USTC", "UST", "USDN", "FEI", "FDUSD", "PYUSD", "USDE", "USDS", "SUSD", "CRVUSD", "GHO", "CUSD", "CEUR", "EURT", "EURC", "EURS", "XSGD", "XIDR", "BIDR", "IDRT", "VAI", "OUSD", "HUSD", "USDK", "USDX", "USDR", "USDB", "USDBR", "USDL", "USD0", "RLUSD", "USDA", "DJED", "MAI", "MUSD", "ALUSD", "TOR", "USN", "USN2", "USX", "USK", "USC", "USDJ", "USDD",
    # Commodity-backed fixed-value proxies
    "PAXG", "XAUT", "DGX", "GOLD",
}

EXCLUDED_NAME_TERMS = (
    "wrapped bitcoin", "wrapped ethereum", "staked ether", "staked ethereum", "liquid staked ether",
    "usd coin", "tether", "stablecoin", "binance usd", "dai", "trueusd", "pax dollar", "gemini dollar",
    "frax", "liquity usd", "paypal usd", "first digital usd", "ethena usde", "pax gold", "tether gold",
)

@dataclass
class AssetRow:
    snapshot_date: str
    raw_rank: int
    slug: str
    name: str
    symbol: str
    market_cap_usd: float | None
    price_usd: float | None
    pct_1h: float | None
    pct_24h: float | None
    pct_7d: float | None
    source_url: str
    source_sha256: str
    parser: str
    excluded: bool
    exclusion_reason: str
    eligible_rank: int | None = None


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "N/A", "null", "None"}:
        return None
    text = text.replace("$", "").replace("%", "").replace("<", "")
    multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
    m = re.fullmatch(r"([-+]?\d*\.?\d+)([KMBT])?", text, re.I)
    if not m:
        try:
            return float(text)
        except ValueError:
            return None
    out = float(m.group(1))
    if m.group(2):
        out *= multipliers[m.group(2).upper()]
    return out


def recursive_dicts(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from recursive_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from recursive_dicts(value)


def first_present(d: dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def extract_quote(d: dict[str, Any]) -> dict[str, Any]:
    for key in ("quote", "quotes"):
        q = d.get(key)
        if isinstance(q, dict):
            for sub in ("USD", "2781", 2781):
                if sub in q and isinstance(q[sub], dict):
                    return q[sub]
            if any(k in q for k in ("price", "marketCap", "market_cap", "percentChange7d", "percent_change_7d")):
                return q
    return d


def classify_exclusion(symbol: str, name: str) -> tuple[bool, str]:
    s = symbol.upper().strip()
    n = name.lower().strip()
    if s in {"BTC", "ETH"}:
        return True, "BTC_ETH_EXCLUDED"
    if s in EXCLUDED_SYMBOLS:
        return True, "STABLE_WRAPPED_OR_FIXED_NAV_EXCLUDED"
    if any(term in n for term in EXCLUDED_NAME_TERMS):
        return True, "NAME_BASED_STABLE_WRAPPED_EXCLUSION"
    return False, ""


def parse_next_data(html: str, snapshot_date: str, source_url: str, source_sha: str) -> list[AssetRow]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []
    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return []

    candidates: dict[tuple[int, str], AssetRow] = {}
    for d in recursive_dicts(data):
        rank = first_present(d, ("cmcRank", "cmc_rank", "rank", "marketCapRank", "market_cap_rank"))
        symbol = first_present(d, ("symbol", "ticker"))
        name = first_present(d, ("name", "coinName", "coin_name"))
        if rank is None or not symbol or not name:
            continue
        try:
            rank_i = int(rank)
        except (TypeError, ValueError):
            continue
        if rank_i < 1 or rank_i > 5000:
            continue
        quote = extract_quote(d)
        slug = str(first_present(d, ("slug", "id", "coinId", "coin_id")) or f"{symbol.lower()}-{rank_i}")
        market_cap = parse_number(first_present(quote, ("marketCap", "market_cap", "market_cap_usd")))
        price = parse_number(first_present(quote, ("price", "price_usd")))
        p1h = parse_number(first_present(quote, ("percentChange1h", "percent_change_1h", "change1h")))
        p24h = parse_number(first_present(quote, ("percentChange24h", "percent_change_24h", "change24h")))
        p7d = parse_number(first_present(quote, ("percentChange7d", "percent_change_7d", "change7d")))
        # Some CMC objects keep quote fields at the parent level.
        market_cap = market_cap if market_cap is not None else parse_number(first_present(d, ("marketCap", "market_cap")))
        price = price if price is not None else parse_number(first_present(d, ("price",)))
        p1h = p1h if p1h is not None else parse_number(first_present(d, ("percentChange1h", "percent_change_1h")))
        p24h = p24h if p24h is not None else parse_number(first_present(d, ("percentChange24h", "percent_change_24h")))
        p7d = p7d if p7d is not None else parse_number(first_present(d, ("percentChange7d", "percent_change_7d")))
        excluded, reason = classify_exclusion(str(symbol), str(name))
        row = AssetRow(snapshot_date, rank_i, slug, str(name), str(symbol).upper(), market_cap, price, p1h, p24h, p7d,
                       source_url, source_sha, "NEXT_DATA", excluded, reason)
        key = (rank_i, row.symbol)
        prev = candidates.get(key)
        if prev is None or sum(v is not None for v in (market_cap, price, p7d)) > sum(v is not None for v in (prev.market_cap_usd, prev.price_usd, prev.pct_7d)):
            candidates[key] = row
    return sorted(candidates.values(), key=lambda x: x.raw_rank)


def parse_table(html: str, snapshot_date: str, source_url: str, source_sha: str) -> list[AssetRow]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[AssetRow] = []
    for tr in soup.select("table tbody tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 5:
            continue
        rank_match = re.search(r"\b(\d{1,4})\b", cells[0])
        if not rank_match:
            continue
        rank = int(rank_match.group(1))
        links = [a for a in tr.find_all("a", href=True) if "/currencies/" in a["href"]]
        slug = ""
        link_texts: list[str] = []
        for a in links:
            href = a["href"]
            m = re.search(r"/currencies/([^/]+)/", href)
            if m and not slug:
                slug = m.group(1)
            t = a.get_text(" ", strip=True)
            if t:
                link_texts.append(t)
        # Symbol is usually a short all-caps token in the first two cells/link texts.
        all_text = " | ".join(cells[:3] + link_texts)
        symbol_candidates = re.findall(r"\b[A-Z0-9]{2,12}\b", all_text)
        symbol = symbol_candidates[-1] if symbol_candidates else "UNKNOWN"
        name_candidates = [t for t in link_texts if t.upper() != symbol and not t.isdigit()]
        name = max(name_candidates, key=len) if name_candidates else cells[1]
        money = re.findall(r"\$[-+]?\d[\d,]*(?:\.\d+)?", " | ".join(cells))
        market_cap = parse_number(money[0]) if money else None
        price = parse_number(money[1]) if len(money) > 1 else None
        pct_tokens = re.findall(r"(?:<)?[-+]?\d*\.?\d+%", " | ".join(cells))
        pct_values = [parse_number(x) for x in pct_tokens]
        p1h = pct_values[-3] if len(pct_values) >= 3 else None
        p24h = pct_values[-2] if len(pct_values) >= 2 else None
        p7d = pct_values[-1] if len(pct_values) >= 1 else None
        excluded, reason = classify_exclusion(symbol, name)
        out.append(AssetRow(snapshot_date, rank, slug or f"{symbol.lower()}-{rank}", name, symbol, market_cap, price,
                            p1h, p24h, p7d, source_url, source_sha, "HTML_TABLE", excluded, reason))
    # De-duplicate by rank/symbol.
    dedup = {(r.raw_rank, r.symbol): r for r in out}
    return sorted(dedup.values(), key=lambda x: x.raw_rank)


def fetch_snapshot(session: requests.Session, date: dt.date, raw_dir: Path, retries: int = 4) -> tuple[list[AssetRow], dict[str, Any]]:
    ds = date.strftime("%Y%m%d")
    url = BASE_URL.format(date=ds)
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            }, timeout=45)
            raw = resp.content
            sha = hashlib.sha256(raw).hexdigest()
            audit = {
                "snapshot_date": date.isoformat(), "url": url, "http_status": resp.status_code,
                "bytes": len(raw), "sha256": sha, "attempt": attempt,
            }
            if resp.status_code != 200 or len(raw) < 10000:
                last_error = f"HTTP_{resp.status_code}_BYTES_{len(raw)}"
                audit["error"] = last_error
                time.sleep(1.5 * attempt)
                continue
            html = resp.text
            rows = parse_next_data(html, date.isoformat(), url, sha)
            if len(rows) < 100:
                table_rows = parse_table(html, date.isoformat(), url, sha)
                if len(table_rows) > len(rows):
                    rows = table_rows
            raw_path = raw_dir / f"CMC_HISTORICAL_{ds}.html.gz"
            with gzip.open(raw_path, "wb", compresslevel=6) as fh:
                fh.write(raw)
            audit["parser"] = rows[0].parser if rows else "NONE"
            audit["parsed_rows"] = len(rows)
            audit["eligible_rows_before_cap"] = sum(not r.excluded for r in rows)
            return rows, audit
        except Exception as exc:  # noqa: BLE001 - audit all extractor failures
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * attempt)
    return [], {"snapshot_date": date.isoformat(), "url": url, "error": last_error, "parsed_rows": 0}


def date_range_weekly(start: dt.date, end: dt.date) -> list[dt.date]:
    # Historical snapshot pages are Sunday snapshots. Move start forward to the first Sunday.
    cur = start + dt.timedelta(days=(6 - start.weekday()) % 7)
    dates: list[dt.date] = []
    while cur <= end:
        dates.append(cur)
        cur += dt.timedelta(days=7)
    return dates


def safe_mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def safe_median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ys[lo]
    return ys[lo] * (hi - pos) + ys[hi] * (pos - lo)


def compute_breadth(all_rows: list[AssetRow], top_n: int = 100) -> tuple[list[AssetRow], dict[str, Any]]:
    eligible = [r for r in sorted(all_rows, key=lambda x: x.raw_rank) if not r.excluded]
    selected = eligible[:top_n]
    for i, r in enumerate(selected, start=1):
        r.eligible_rank = i
    r7 = [r.pct_7d for r in selected if r.pct_7d is not None]
    caps = [r.market_cap_usd for r in selected if r.market_cap_usd is not None and r.market_cap_usd > 0]
    top20 = [r.pct_7d for r in selected[:20] if r.pct_7d is not None]
    bottom80 = [r.pct_7d for r in selected[20:] if r.pct_7d is not None]
    total_cap = sum(caps) if caps else None
    top10_cap = sum(r.market_cap_usd or 0 for r in selected[:10])
    metrics = {
        "snapshot_date": selected[0].snapshot_date if selected else (all_rows[0].snapshot_date if all_rows else None),
        "raw_rows_parsed": len(all_rows),
        "eligible_rows_available": len(eligible),
        "selected_n": len(selected),
        "return_7d_coverage": len(r7),
        "positive_7d_pct": 100.0 * sum(x > 0 for x in r7) / len(r7) if r7 else None,
        "equal_weight_mean_7d_pct": safe_mean(r7),
        "median_7d_pct": safe_median(r7),
        "p25_7d_pct": quantile(r7, 0.25),
        "p75_7d_pct": quantile(r7, 0.75),
        "dispersion_iqr_7d_pct": (quantile(r7, 0.75) - quantile(r7, 0.25)) if r7 else None,
        "top20_positive_7d_pct": 100.0 * sum(x > 0 for x in top20) / len(top20) if top20 else None,
        "bottom80_positive_7d_pct": 100.0 * sum(x > 0 for x in bottom80) / len(bottom80) if bottom80 else None,
        "breadth_depth_gap_pp": ((100.0 * sum(x > 0 for x in bottom80) / len(bottom80)) - (100.0 * sum(x > 0 for x in top20) / len(top20))) if top20 and bottom80 else None,
        "top10_market_cap_share_pct": (100.0 * top10_cap / total_cap) if total_cap else None,
        "source_url": selected[0].source_url if selected else "",
        "source_sha256": selected[0].source_sha256 if selected else "",
        "parser": selected[0].parser if selected else "NONE",
    }
    return selected, metrics


def add_trailing_and_forward_metrics(universe_rows: list[AssetRow], breadth_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, list[AssetRow]] = {}
    for r in universe_rows:
        by_date.setdefault(r.snapshot_date, []).append(r)
    dates = sorted(by_date)
    price_maps = {d: {r.slug: r.price_usd for r in rows if r.price_usd is not None} for d, rows in by_date.items()}
    breadth_map = {r["snapshot_date"]: r for r in breadth_rows}
    for i, d in enumerate(dates):
        members = by_date[d]
        for weeks, prefix in ((4, "trailing_28d"),):
            if i - weeks >= 0:
                old = price_maps[dates[i - weeks]]
                vals = [100.0 * (r.price_usd / old[r.slug] - 1.0) for r in members if r.price_usd and r.slug in old and old[r.slug]]
                row = breadth_map[d]
                row[f"{prefix}_coverage"] = len(vals)
                row[f"positive_{prefix}_pct"] = 100.0 * sum(x > 0 for x in vals) / len(vals) if vals else None
                row[f"median_{prefix}_pct"] = safe_median(vals)
                row[f"mean_{prefix}_pct"] = safe_mean(vals)
        for weeks, horizon in ((1, 7), (2, 14), (4, 28)):
            if i + weeks < len(dates):
                fut = price_maps[dates[i + weeks]]
                vals = [100.0 * (fut[r.slug] / r.price_usd - 1.0) for r in members if r.price_usd and r.slug in fut and fut[r.slug]]
                row = breadth_map[d]
                row[f"fwd_{horizon}d_coverage"] = len(vals)
                row[f"fwd_{horizon}d_positive_pct"] = 100.0 * sum(x > 0 for x in vals) / len(vals) if vals else None
                row[f"fwd_{horizon}d_median_pct"] = safe_median(vals)
                row[f"fwd_{horizon}d_mean_pct"] = safe_mean(vals)
    return breadth_rows


def current_constituent_bias(universe_rows: list[AssetRow], breadth_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, list[AssetRow]] = {}
    for r in universe_rows:
        by_date.setdefault(r.snapshot_date, []).append(r)
    dates = sorted(by_date)
    if not dates:
        return []
    latest_members = {r.slug for r in by_date[dates[-1]]}
    truth = {r["snapshot_date"]: r for r in breadth_rows}
    out: list[dict[str, Any]] = []
    for d in dates:
        current_backfill = [r for r in by_date[d] if r.slug in latest_members and r.pct_7d is not None]
        vals = [r.pct_7d for r in current_backfill if r.pct_7d is not None]
        frozen = truth[d]
        naive_positive = 100.0 * sum(x > 0 for x in vals) / len(vals) if vals else None
        naive_median = safe_median(vals)
        out.append({
            "snapshot_date": d,
            "latest_constituent_set_date": dates[-1],
            "naive_current_constituent_coverage": len(vals),
            "frozen_positive_7d_pct": frozen.get("positive_7d_pct"),
            "naive_positive_7d_pct": naive_positive,
            "positive_bias_pp": (naive_positive - frozen["positive_7d_pct"]) if naive_positive is not None and frozen.get("positive_7d_pct") is not None else None,
            "frozen_median_7d_pct": frozen.get("median_7d_pct"),
            "naive_median_7d_pct": naive_median,
            "median_bias_pp": (naive_median - frozen["median_7d_pct"]) if naive_median is not None and frozen.get("median_7d_pct") is not None else None,
        })
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for k in row:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-07-05")
    ap.add_argument("--output", default="breadth_output")
    ap.add_argument("--top-n", type=int, default=100)
    ap.add_argument("--min-pass-pct", type=float, default=95.0)
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    out = Path(args.output)
    raw_dir = out / "raw_html_gz"
    raw_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    dates = date_range_weekly(start, end)
    universe: list[AssetRow] = []
    breadth: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []

    for idx, date in enumerate(dates, 1):
        rows, audit = fetch_snapshot(session, date, raw_dir)
        selected, metrics = compute_breadth(rows, args.top_n) if rows else ([], {"snapshot_date": date.isoformat(), "selected_n": 0})
        universe.extend(selected)
        breadth.append(metrics)
        audit["selected_n"] = len(selected)
        audit["status"] = "PASS" if len(selected) == args.top_n and metrics.get("return_7d_coverage", 0) >= int(args.top_n * 0.9) else "FAIL"
        audits.append(audit)
        print(f"[{idx}/{len(dates)}] {date}: parsed={audit.get('parsed_rows',0)} selected={len(selected)} status={audit['status']}", flush=True)
        time.sleep(0.35)

    breadth = add_trailing_and_forward_metrics(universe, breadth)
    bias = current_constituent_bias(universe, breadth)

    write_csv(out / "CMC_FROZEN_UNIVERSE_WEEKLY.csv", [asdict(r) for r in universe])
    write_csv(out / "CMC_BREADTH_WEEKLY.csv", breadth)
    write_csv(out / "CMC_CURRENT_CONSTITUENT_BACKFILL_BIAS.csv", bias)
    write_csv(out / "CMC_BREADTH_SOURCE_AUDIT.csv", audits)

    passed = sum(a.get("status") == "PASS" for a in audits)
    pass_pct = 100.0 * passed / len(audits) if audits else 0.0
    validation = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_provider": "CoinMarketCap historical snapshots",
        "source_convention": "CMC_HISTORICAL_WEEKLY_FROZEN_UNIVERSE",
        "period_requested": f"{start}/{end}",
        "snapshots_expected": len(dates),
        "snapshots_passed": passed,
        "snapshot_pass_pct": pass_pct,
        "universe_rows": len(universe),
        "top_n": args.top_n,
        "interpolation": False,
        "current_constituent_backfill_used_for_truth": False,
        "backfill_bias_test_included": True,
        "status": "PASS" if pass_pct >= args.min_pass_pct else "PARTIAL_OR_FAIL",
        "failure_dates": [a.get("snapshot_date") for a in audits if a.get("status") != "PASS"],
        "limitations": [
            "CoinMarketCap historical snapshots are weekly, not daily.",
            "The stable/wrapped exclusion list is deterministic but not a full economic taxonomy.",
            "Trailing and forward 28d returns use four-week snapshot spacing, not calendar-month daily closes.",
            "No historical 30-day moving-average breadth is claimed.",
        ],
    }
    (out / "CMC_BREADTH_VALIDATION.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    (out / "README.md").write_text(
        "# CMC Frozen-Universe Breadth Truth Layer\n\n"
        "Research-only extraction of weekly point-in-time CoinMarketCap historical snapshots. "
        "BTC, ETH, stablecoins, wrapped BTC/ETH, liquid-staked ETH and fixed-NAV commodity tokens are excluded mechanically. "
        "The top 100 remaining assets are frozen for each snapshot. No current-constituent history is used as truth.\n\n"
        f"Validation status: **{validation['status']}** ({passed}/{len(dates)} snapshots).\n",
        encoding="utf-8",
    )

    # Build package hashes.
    manifest: list[dict[str, Any]] = []
    for p in sorted(x for x in out.rglob("*") if x.is_file()):
        manifest.append({"path": str(p.relative_to(out)), "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    write_csv(out / "PACKAGE_MANIFEST.csv", manifest)

    print(json.dumps(validation, indent=2))
    return 0 if validation["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
