#!/usr/bin/env python3
"""Compatibility patch for CMC breadth extraction.

CoinMarketCap embeds the 200-row historical table inside `props.initialState`
as a JSON string inside `__NEXT_DATA__`. The original extractor parsed normal
JSON objects but did not decode this nested JSON string. This wrapper patches
that parser while preserving the original validation and output contract.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

BASE = Path(__file__).with_name("fetch_cmc_frozen_breadth.py")
spec = importlib.util.spec_from_file_location("cmc_breadth_base", BASE)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load base extractor: {BASE}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def patched_parse_next_data(html: str, snapshot_date: str, source_url: str, source_sha: str):
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []
    try:
        next_data: dict[str, Any] = json.loads(script.string)
    except json.JSONDecodeError:
        return []

    roots: list[Any] = [next_data]
    initial_state = next_data.get("props", {}).get("initialState")
    if isinstance(initial_state, str):
        try:
            roots.insert(0, json.loads(initial_state))
        except json.JSONDecodeError:
            pass
    elif isinstance(initial_state, (dict, list)):
        roots.insert(0, initial_state)

    candidates = {}
    for root in roots:
        for d in mod.recursive_dicts(root):
            rank = mod.first_present(d, ("cmcRank", "cmc_rank", "rank", "marketCapRank", "market_cap_rank"))
            symbol = mod.first_present(d, ("symbol", "ticker"))
            name = mod.first_present(d, ("name", "coinName", "coin_name"))
            if rank is None or not symbol or not name:
                continue
            try:
                rank_i = int(rank)
            except (TypeError, ValueError):
                continue
            if rank_i < 1 or rank_i > 5000:
                continue
            quote = mod.extract_quote(d)
            slug = str(mod.first_present(d, ("slug", "id", "coinId", "coin_id")) or f"{str(symbol).lower()}-{rank_i}")
            market_cap = mod.parse_number(mod.first_present(quote, ("marketCap", "market_cap", "market_cap_usd")))
            price = mod.parse_number(mod.first_present(quote, ("price", "price_usd")))
            p1h = mod.parse_number(mod.first_present(quote, ("percentChange1h", "percent_change_1h", "change1h")))
            p24h = mod.parse_number(mod.first_present(quote, ("percentChange24h", "percent_change_24h", "change24h")))
            p7d = mod.parse_number(mod.first_present(quote, ("percentChange7d", "percent_change_7d", "change7d")))
            excluded, reason = mod.classify_exclusion(str(symbol), str(name))
            row = mod.AssetRow(
                snapshot_date, rank_i, slug, str(name), str(symbol).upper(),
                market_cap, price, p1h, p24h, p7d,
                source_url, source_sha, "NEXT_DATA_INITIAL_STATE", excluded, reason,
            )
            key = (rank_i, row.symbol)
            prev = candidates.get(key)
            if prev is None or sum(v is not None for v in (market_cap, price, p7d)) > sum(
                v is not None for v in (prev.market_cap_usd, prev.price_usd, prev.pct_7d)
            ):
                candidates[key] = row
    return sorted(candidates.values(), key=lambda x: x.raw_rank)


mod.parse_next_data = patched_parse_next_data

if __name__ == "__main__":
    raise SystemExit(mod.main())
