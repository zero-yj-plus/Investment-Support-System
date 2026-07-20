"""Shared execution contract for stock selection, diagnosis, and AI explanation."""
from __future__ import annotations

from typing import Iterable


VERSION = "stock-analysis-pipeline-v1"
PIPELINE = (
    "deterministic_candidate_selection",
    "governed_stock_diagnosis",
    "ai_explanation_only",
)


def build(
    *,
    entry: str,
    current_stage: str,
    analysis_executed: bool,
    deterministic_method: str,
    deterministic_rule_version: str,
    data_status: str,
    data_as_of: str | None = None,
    batch_id: str | None = None,
    expected: int | None = None,
    present: int | None = None,
    missing: Iterable[object] = (),
    ai_status: str = "not_requested",
    decision_authority: str = "deterministic_only",
) -> dict:
    """Build the same truth contract for every stock-analysis entry point."""
    missing_items = [
        str(item)[:240] for item in missing
        if str(item or "").strip()
    ][:20]
    completeness = None
    if expected is not None and expected > 0 and present is not None:
        completeness = round(max(0.0, min(1.0, present / expected)), 4)
    return {
        "version": VERSION,
        "entry": str(entry),
        "pipeline": list(PIPELINE),
        "current_stage": str(current_stage),
        "analysis_executed": bool(analysis_executed),
        "deterministic": {
            "status": "executed" if analysis_executed else "not_executed",
            "method": str(deterministic_method),
            "rule_version": str(deterministic_rule_version),
            "decision_authority": str(decision_authority),
        },
        "real_data": {
            "status": str(data_status),
            "as_of": data_as_of,
            "batch_id": batch_id,
            "expected": expected,
            "present": present,
            "completeness": completeness,
            "missing": missing_items,
        },
        "ai": {
            "role": "explanation_only",
            "status": str(ai_status),
            "may_change_deterministic_action": False,
            "may_invent_prices_or_missing_data": False,
        },
        "next_step": (
            "governed_stock_diagnosis"
            if current_stage == "deterministic_candidate_selection"
            else "ai_explanation_only"
        ),
    }
