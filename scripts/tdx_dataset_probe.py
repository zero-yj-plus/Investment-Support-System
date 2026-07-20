#!/usr/bin/env python3
"""Read-only, credential-safe probe for the ten TDX datasets used in review.

The probe prints only counts, dates, field coverage and consistency checks.  It
never prints the configured API base URL, key, or complete upstream rows.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_trader.config import Config
from hermes_trader.data import official_universe
from hermes_trader.data import tdx_api_source as tdx


DATASETS = (
    "snapshot",
    "more_info",
    "instruments_a",
    "trading_calendar",
    "relation",
    "sector_members",
    "gpjy",
    "gp_one",
    "holders_top10",
    "news_sentiment",
)


def _body(row: dict) -> dict:
    value = row.get("payload")
    return value if isinstance(value, dict) else {}


def _first_number(value: Any) -> float | None:
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _date_bounds(rows: list[dict], key: str) -> dict[str, str | None]:
    values = sorted(str(row.get(key) or "").strip() for row in rows if row.get(key))
    return {"earliest": values[0] if values else None, "latest": values[-1] if values else None}


def _field_coverage(rows: list[dict], *, limit: int = 24) -> dict[str, dict[str, int | float]]:
    counts: dict[str, int] = {}
    for row in rows:
        for key, value in _body(row).items():
            if _present(value):
                counts[str(key)] = counts.get(str(key), 0) + 1
    total = len(rows)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return {
        key: {"nonempty": count, "ratio": round(count / total, 4) if total else 0}
        for key, count in ordered
    }


def _summary(rows: list[dict] | None) -> dict:
    if rows is None:
        return {"status": "error", "rows": 0}
    entities = {str(row.get("entity_key") or "") for row in rows if row.get("entity_key")}
    return {
        "status": "ok" if rows else "empty",
        "rows": len(rows),
        "entities": len(entities),
        "as_of": _date_bounds(rows, "as_of"),
        "series": _date_bounds(rows, "series_key"),
        "payload_fields": _field_coverage(rows),
    }


def _sample_entities(instrument_rows: list[dict]) -> list[str]:
    entities = [str(row.get("entity_key") or "").upper() for row in instrument_rows]
    checks: tuple[Callable[[str], bool], ...] = (
        lambda value: value.startswith("600") and value.endswith(".SH"),
        lambda value: value.startswith("000") and value.endswith(".SZ"),
        lambda value: value.startswith("300") and value.endswith(".SZ"),
        lambda value: value.startswith("688") and value.endswith(".SH"),
        lambda value: value.endswith(".BJ"),
    )
    selected: list[str] = []
    for check in checks:
        found = next((entity for entity in entities if check(entity)), "")
        if found and found not in selected:
            selected.append(found)
    return selected


def _snapshot_checks(rows: list[dict]) -> dict:
    errors: list[float] = []
    for row in rows:
        body = _body(row)
        now = _first_number(body.get("Now"))
        volume_hands = _first_number(body.get("Volume"))
        amount_wan = _first_number(body.get("Amount"))
        if not now or not volume_hands or not amount_wan:
            continue
        implied_price = amount_wan * 10_000 / (volume_hands * 100)
        errors.append(abs(implied_price / now - 1) * 100)
    return {
        "unit_hypothesis": "Volume=hands, Amount=10k_CNY",
        "checked_rows": len(errors),
        "max_implied_price_error_pct": round(max(errors), 3) if errors else None,
        "result": "consistent" if errors and max(errors) <= 5 else "unverified",
    }


def _more_info_checks(rows: list[dict]) -> dict:
    zjl_values = [
        number
        for row in rows
        for number in (_first_number(_body(row).get("Zjl")), _first_number(_body(row).get("Zjl_HB")))
        if number is not None
    ]
    board_values = [
        number for row in rows
        if (number := _first_number(_body(row).get("fLianB"))) is not None
    ]
    non_integer = [number for number in board_values if abs(number - round(number)) > 1e-6]
    return {
        "moneyflow_nonzero": sum(1 for value in zjl_values if value != 0),
        "moneyflow_observed": len(zjl_values),
        "fLianB_observed": len(board_values),
        "fLianB_non_integer": len(non_integer),
        "warning": "Zjl/Zjl_HB unusable; fLianB is not verified as board count"
        if (zjl_values and not any(zjl_values)) or non_integer else None,
    }


def _gpjy_checks(rows: list[dict]) -> dict:
    compared = matched = 0
    max_error = 0.0
    for row in rows:
        body = _body(row)
        gp11 = _first_number(body.get("GP11"))
        gp12 = _first_number(body.get("GP12"))
        gp13 = _first_number(body.get("GP13"))
        if None in (gp11, gp12, gp13):
            continue
        compared += 1
        error = abs(float(gp13) - (float(gp11) - float(gp12)))
        max_error = max(max_error, error)
        matched += int(error <= 1e-6)
    return {
        "claimed_identity": "GP13 = GP11 - GP12",
        "compared_rows": compared,
        "matched_rows": matched,
        "max_absolute_error": round(max_error, 4) if compared else None,
        "result": "verified" if compared and matched == compared else "failed",
        "decision": "blocked_until_field_dictionary_and_units_are_verified",
    }


def _gp_one_checks(rows: list[dict]) -> dict:
    by_entity: dict[str, list[dict]] = {}
    for row in rows:
        by_entity.setdefault(str(row.get("entity_key") or ""), []).append(row)
    usable = 0
    for entity_rows in by_entity.values():
        values = {
            key: _first_number(_body(row).get(key))
            for row in entity_rows
            for key in ("GO3", "GO5", "GO6", "GO7", "GO8", "GO9", "GO10", "GO20", "GO21", "GO22")
            if key in _body(row)
        }
        usable += int(any(value not in (None, 0) for value in values.values()))
    return {
        "sample_entities": len(by_entity),
        "entities_with_nonzero_forecast": usable,
        "coverage_ratio": round(usable / len(by_entity), 4) if by_entity else 0,
        "decision": "optional_after_hours_enrichment_only",
    }


def _holder_checks(rows: list[dict]) -> dict:
    valid = 0
    reports: list[str] = []
    totals: list[float] = []
    for row in rows:
        holders = _body(row).get("holders")
        if not isinstance(holders, list) or not holders:
            continue
        valid += 1
        reports.append(str(row.get("series_key") or ""))
        totals.append(sum(_first_number(holder.get("cgbl")) or 0 for holder in holders if isinstance(holder, dict)))
    return {
        "valid_entities": valid,
        "latest_report": max(reports) if reports else None,
        "top10_total_pct_range": [round(min(totals), 2), round(max(totals), 2)] if totals else None,
    }


def _news_checks(rows: list[dict]) -> dict:
    summaries = [str(_body(row).get("Summary") or "").strip() for row in rows]
    nonempty = sum(bool(value) for value in summaries)
    return {
        "summary_nonempty": nonempty,
        "summary_coverage_ratio": round(nonempty / len(rows), 4) if rows else 0,
        "latest_event_time": _date_bounds(rows, "series_key")["latest"],
        "decision": "usable_after_dedupe_freshness_and_relevance_filters",
    }


def _official_compare(instrument_rows: list[dict], timeout: float) -> dict:
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda exchange: official_universe.fetch_exchange(exchange, timeout), ("sse", "szse", "bse")))
    official = {code for result in results for code in result.names}
    tdx_codes = {
        str(row.get("entity_key") or "").split(".", 1)[0]
        for row in instrument_rows if row.get("entity_key")
    }
    exchange_complete = {result.exchange: result.complete for result in results}
    if not official:
        return {
            "status": "unavailable",
            "tdx_count": len(tdx_codes),
            "exchange_complete": exchange_complete,
            "decision": "keep_existing_official_validator_and_retry_reconciliation",
        }
    return {
        "status": "complete" if all(exchange_complete.values()) else "partial",
        "tdx_count": len(tdx_codes),
        "official_count": len(official),
        "tdx_only_count": len(tdx_codes - official),
        "official_only_count": len(official - tdx_codes),
        "exchange_complete": exchange_complete,
        "decision": "do_not_replace_official_hard_validator_until_reconciled",
    }


def run(config_path: Path, *, compare_official: bool = False, official_timeout: float = 8.0) -> dict:
    Config(config_path)
    health = tdx.service_health.__wrapped__()
    instrument_rows = tdx._dataset_rows(
        "instruments_a", {"series": "latest"}, label="TDX probe instruments_a", max_rows=15_000
    )
    if instrument_rows is None:
        return {"probe_version": 1, "health": health, "error": "instruments_a_unavailable"}
    entities = _sample_entities(instrument_rows)
    entity_param = ",".join(entities)
    today = datetime.now().date()
    probes: dict[str, list[dict] | None] = {
        "instruments_a": instrument_rows,
        "snapshot": tdx._dataset_rows("snapshot", {"entities": entity_param}, label="TDX probe snapshot", max_rows=2_000),
        "more_info": tdx._dataset_rows("more_info", {"entities": entity_param}, label="TDX probe more_info", max_rows=2_000),
        "trading_calendar": tdx._dataset_rows(
            "trading_calendar",
            {
                "series_start": (today - timedelta(days=10)).strftime("%Y%m%d"),
                "series_end": (today + timedelta(days=30)).strftime("%Y%m%d"),
                "order": "asc",
            },
            label="TDX probe trading_calendar",
            max_rows=500,
        ),
        "relation": tdx._dataset_rows("relation", {"entities": entity_param}, label="TDX probe relation", max_rows=5_000),
        "gpjy": tdx._dataset_rows("gpjy", {"entities": entity_param}, label="TDX probe gpjy", max_rows=5_000),
        "gp_one": tdx._dataset_rows("gp_one", {"entities": entity_param}, label="TDX probe gp_one", max_rows=2_000),
        "holders_top10": tdx._dataset_rows("holders_top10", {"entities": entity_param}, label="TDX probe holders_top10", max_rows=2_000),
        "news_sentiment": tdx._dataset_rows("news_sentiment", label="TDX probe news_sentiment", max_rows=10_000),
    }
    relation_rows = probes["relation"] or []
    sector_code = next((str(_body(row).get("BlockCode") or "") for row in relation_rows if _body(row).get("BlockType") == "行业"), "881002.SH")
    probes["sector_members"] = tdx._dataset_rows(
        "sector_members", {"entities": sector_code}, label="TDX probe sector_members", max_rows=5_000
    )

    matrix = {dataset: _summary(probes.get(dataset)) for dataset in DATASETS}
    matrix["snapshot"]["validation"] = _snapshot_checks(probes["snapshot"] or [])
    matrix["more_info"]["validation"] = _more_info_checks(probes["more_info"] or [])
    matrix["gpjy"]["validation"] = _gpjy_checks(probes["gpjy"] or [])
    matrix["gp_one"]["validation"] = _gp_one_checks(probes["gp_one"] or [])
    matrix["holders_top10"]["validation"] = _holder_checks(probes["holders_top10"] or [])
    matrix["news_sentiment"]["validation"] = _news_checks(probes["news_sentiment"] or [])
    result = {
        "probe_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "credential_fields_emitted": False,
        "health": health,
        "sample_entities": entities,
        "datasets": matrix,
    }
    if compare_official:
        result["universe_reconciliation"] = _official_compare(instrument_rows, official_timeout)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--compare-official", action="store_true")
    parser.add_argument("--official-timeout", type=float, default=8.0)
    args = parser.parse_args()
    result = run(Path(args.config).resolve(), compare_official=args.compare_official, official_timeout=args.official_timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
