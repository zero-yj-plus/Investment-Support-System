"""Deterministic decision controls.

The package deliberately contains no model or network clients.  Decision
controls consume an already-materialised snapshot and return an auditable
result that can be persisted alongside a recommendation event.
"""

from .risk_gate import (
    RULE_VERSION,
    CandidateAction,
    EventContext,
    HoldingSnapshot,
    LiquidityContext,
    MarketContext,
    PortfolioContext,
    RiskGateDecision,
    RiskGateInput,
    evaluate,
    evaluate_risk_gate,
)

__all__ = [
    "RULE_VERSION",
    "CandidateAction",
    "EventContext",
    "HoldingSnapshot",
    "LiquidityContext",
    "MarketContext",
    "PortfolioContext",
    "RiskGateDecision",
    "RiskGateInput",
    "evaluate",
    "evaluate_risk_gate",
]
