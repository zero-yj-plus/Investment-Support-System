"""Pure, deterministic pre-trade risk gate.

The gate is intentionally a small policy engine rather than an analysis
model.  It accepts an immutable, normalised snapshot and can only return one
of three decisions:

``allow``
    The proposed action and size already comply with every hard rule.
``reduce``
    The action remains possible, but the gate lowered confidence or position.
``veto``
    The proposed risk-increasing action must not be executed.

No LLM, clock, filesystem or network is consulted.  Identical normalised
inputs therefore produce the same decision and the same SHA-256 snapshot
hash.  The hash is suitable for linking the decision to a recommendation
event, but is not a signature.

Percent fields use percentage points (``8`` means 8%, not 0.08) unless a
field name explicitly says ``rate`` or ``mdd``; those compatibility aliases
accept a 0..1 ratio.  Confidence is normalised to 0..1.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


RULE_VERSION = "risk-gate.v2"

# These are policy constants, not predictions.  Changing one requires a rule
# version bump so historical decisions remain reproducible.
CANDIDATE_POSITION_CAP_PCT = {1: 10.0, 2: 8.0, 3: 5.0, 4: 2.0, 5: 0.0}
TOTAL_POSITION_CAP_PCT = {1: 70.0, 2: 50.0, 3: 40.0, 4: 20.0, 5: 10.0}
# Backward-compatible name used by older callers.  It remains the per-candidate
# target cap; portfolio exposure is governed separately by TOTAL_POSITION_CAP_PCT.
MARKET_POSITION_CAP_PCT = CANDIDATE_POSITION_CAP_PCT
SINGLE_NAME_CAP_PCT = 10.0
SECTOR_CAP_PCT = 30.0
HIGH_CORRELATION = 0.80
HIGH_CONFIDENCE = 0.70
MAX_CONFIDENCE_WITH_MISSING_EVIDENCE = 0.70
MAX_LIQUIDITY_PARTICIPATION_PCT = 10.0
DRAWDOWN_REDUCE_PCT = 8.0
DRAWDOWN_SEVERE_PCT = 15.0


_ACTION_ALIASES = {
    "buy": "buy", "open": "buy", "candidate": "buy", "long": "buy",
    "买入": "buy", "建仓": "buy", "候选": "buy",
    "add": "add", "increase": "add", "overweight": "add",
    "加仓": "add", "增持": "add",
    "hold": "hold", "持有": "hold", "持仓": "hold",
    "watch": "watch", "observe": "watch", "观察": "watch", "观望": "watch", "等待": "watch",
    "reduce": "reduce", "underweight": "reduce", "减仓": "reduce",
    "sell": "sell", "卖出": "sell",
    "exit": "exit", "clear": "exit", "退出": "exit", "清仓": "exit", "止损": "exit",
    "avoid": "avoid", "回避": "avoid", "不买": "avoid",
}
_ENTRY_ACTIONS = frozenset({"buy", "add"})
_DEFENSIVE_ACTIONS = frozenset({"reduce", "sell", "exit", "avoid"})
_NON_TRADING_ACTIONS = frozenset({"hold", "watch"})


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _first_with_key(mapping: Mapping[str, Any], *keys: str) -> tuple[Any, str | None]:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key], key
    return None, None


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return 0.0 if number == 0 else number


def _percentage(value: Any, *, ratio_alias: bool = False) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if ratio_alias and abs(number) <= 1:
        number *= 100
    return number


def _nonnegative_percentage(value: Any, *, ratio_alias: bool = False) -> float | None:
    number = _percentage(value, ratio_alias=ratio_alias)
    return number if number is not None and number >= 0 else None


def _correlation(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    number = abs(number)
    if 1 < number <= 100:
        number /= 100
    return number if 0 <= number <= 1 else None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (1, "1"):
        return True
    if value in (0, "0"):
        return False
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "y", "ok", "pass", "是", "有", "充足", "正常"}:
            return True
        if lowered in {"false", "no", "n", "fail", "否", "无", "不足", "异常"}:
            return False
    return None


def _strings(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        values: Sequence[Any] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        values = (value,)
    result: list[str] = []
    for raw in values:
        text = str(raw or "").strip()[:160]
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _normalise_action(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _ACTION_ALIASES.get(text, "unknown")


def _confidence(value: Any, scale: Any = None) -> float | None:
    number = _number(value)
    if number is None:
        return None
    scale_text = str(scale or "").strip().lower()
    if scale_text in {"0_to_100", "0-100", "percent", "pct", "%"} or number > 1:
        number /= 100
    return number if 0 <= number <= 1 else None


@dataclass(frozen=True, slots=True)
class CandidateAction:
    action: str
    symbol: str | None = None
    sector: str | None = None
    confidence: float | None = None
    position_cap_pct: float | None = None
    position_supplied: bool = False
    position_valid: bool = True
    confidence_supplied: bool = False
    confidence_valid: bool = True
    evidence_ids: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    critical_evidence_complete: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CandidateAction":
        raw_action = _first(value, "action", "direction", "rec_type")
        raw_position, position_key = _first_with_key(
            value, "position_cap_pct", "position_pct", "position_cap", "position",
        )
        position = _percentage(raw_position)
        raw_confidence, confidence_key = _first_with_key(value, "confidence", "score_confidence")
        confidence = _confidence(raw_confidence, value.get("confidence_scale"))
        evidence_ids = _strings(_first(value, "evidence_ids", "evidence_id"))
        missing = _strings(_first(value, "missing_evidence", "missing"))
        explicit_complete = _optional_bool(_first(
            value, "critical_evidence_complete", "evidence_complete",
        ))
        evidence_complete = (
            explicit_complete
            if explicit_complete is not None
            else bool(evidence_ids and not missing)
        )
        symbol = str(_first(value, "symbol", "code") or "").strip()[:32] or None
        sector = str(_first(value, "sector", "industry") or "").strip()[:80] or None
        return cls(
            action=_normalise_action(raw_action),
            symbol=symbol,
            sector=sector,
            confidence=confidence,
            position_cap_pct=position,
            position_supplied=position_key is not None,
            position_valid=position_key is None or position is not None,
            confidence_supplied=confidence_key is not None,
            confidence_valid=confidence_key is None or confidence is not None,
            evidence_ids=evidence_ids,
            missing_evidence=missing,
            critical_evidence_complete=bool(evidence_complete),
        )


@dataclass(frozen=True, slots=True)
class MarketContext:
    risk_level: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MarketContext":
        raw = _first(value, "risk_level", "level", "market_risk_level")
        try:
            level = int(raw)
        except (TypeError, ValueError):
            level = None
        return cls(level if level in MARKET_POSITION_CAP_PCT else None)


@dataclass(frozen=True, slots=True)
class HoldingSnapshot:
    symbol: str | None = None
    sector: str | None = None
    weight_pct: float | None = None
    market_value: float | None = None
    correlation_to_candidate: float | None = None


def _holding_rows(value: Any) -> tuple[HoldingSnapshot, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    parsed: list[dict[str, Any]] = []
    for raw in value:
        row = _mapping(raw)
        if not row:
            continue
        symbol = str(_first(row, "symbol", "code") or "").strip()[:32] or None
        sector = str(_first(row, "sector", "industry") or "").strip()[:80] or None
        weight = _nonnegative_percentage(_first(
            row, "weight_pct", "position_pct", "share_pct",
        ))
        market_value = _number(_first(row, "market_value", "value"))
        if market_value is None:
            cost = _number(row.get("cost"))
            shares = _number(row.get("shares"))
            if cost is not None and shares is not None and cost > 0 and shares > 0:
                market_value = cost * shares
        parsed.append({
            "symbol": symbol,
            "sector": sector,
            "weight": weight,
            "market_value": market_value if market_value is not None and market_value > 0 else None,
            "correlation": _correlation(_first(
                row, "correlation_to_candidate", "correlation", "corr",
            )),
        })

    # cost*shares 只是市值，没有账户 NAV/现金时不得将「已投资部分」
    # 归一化为 100%；否则单持仓永远被误判为满仓。缺权重保持 None，
    # 由下游入场闸 fail closed。

    return tuple(HoldingSnapshot(
        symbol=row["symbol"],
        sector=row["sector"],
        weight_pct=row["weight"],
        market_value=row["market_value"],
        correlation_to_candidate=row["correlation"],
    ) for row in parsed)


@dataclass(frozen=True, slots=True)
class PortfolioContext:
    holdings: tuple[HoldingSnapshot, ...] = ()
    target_position_pct: float | None = None
    sector_position_pct: float | None = None
    projected_target_pct: float | None = None
    projected_sector_pct: float | None = None
    max_correlation: float | None = None
    drawdown_pct: float | None = None
    # Appended after the v1 fields to preserve positional-constructor
    # compatibility for callers that instantiate PortfolioContext directly.
    current_total_position_pct: float | None = None
    projected_total_position_pct: float | None = None

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        candidate: CandidateAction,
    ) -> "PortfolioContext":
        holdings = _holding_rows(value.get("holdings"))
        target = _nonnegative_percentage(_first(
            value, "target_position_pct", "current_target_position_pct", "single_name_pct",
        ))
        sector = _nonnegative_percentage(_first(
            value, "sector_position_pct", "current_sector_pct", "sector_pct",
            "industry_position_pct", "industry_share_pct",
        ))

        # Accept the cockpit's structured concentration field when it refers to
        # the candidate sector.  Do not apply a portfolio's largest unrelated
        # industry to every candidate.
        concentration = _mapping(value.get("concentration"))
        concentration_name = str(_first(concentration, "industry", "sector") or "").strip()
        if (
            sector is None
            and candidate.sector
            and concentration_name == candidate.sector
        ):
            sector = _nonnegative_percentage(_first(concentration, "share_pct", "sector_pct"))

        if target is None and candidate.symbol:
            matches = [holding for holding in holdings if holding.symbol == candidate.symbol]
            target = (
                sum(float(holding.weight_pct) for holding in matches)
                if matches and all(holding.weight_pct is not None for holding in matches)
                else (0.0 if not matches else None)
            )
        if sector is None and candidate.sector:
            matches = [holding for holding in holdings if holding.sector == candidate.sector]
            sector = (
                sum(float(holding.weight_pct) for holding in matches)
                if matches and all(holding.weight_pct is not None for holding in matches)
                else (0.0 if not matches else None)
            )

        current_total = _nonnegative_percentage(_first(
            value,
            "current_total_position_pct",
            "total_position_pct",
            "portfolio_position_pct",
            "invested_pct",
        ))
        if current_total is None:
            current_total = (
                sum(float(holding.weight_pct) for holding in holdings)
                if holdings and all(holding.weight_pct is not None for holding in holdings)
                else (0.0 if not holdings else None)
            )

        max_correlation = _correlation(_first(
            value, "max_correlation", "max_pair_correlation", "correlation",
        ))
        if max_correlation is None:
            correlations = [
                item.correlation_to_candidate
                for item in holdings
                if item.correlation_to_candidate is not None
            ]
            max_correlation = max(correlations, default=None)

        raw_drawdown, drawdown_key = _first_with_key(
            value, "drawdown_pct", "portfolio_drawdown_pct", "current_drawdown_pct",
            "max_drawdown", "mdd",
        )
        drawdown = _percentage(
            raw_drawdown,
            ratio_alias=drawdown_key in {"max_drawdown", "mdd"},
        )
        if drawdown is not None:
            drawdown = abs(drawdown)
        return cls(
            holdings=holdings,
            target_position_pct=target,
            sector_position_pct=sector,
            projected_target_pct=_nonnegative_percentage(_first(
                value, "projected_target_pct", "post_trade_single_pct",
                "projected_single_name_pct",
            )),
            projected_sector_pct=_nonnegative_percentage(_first(
                value, "projected_sector_pct", "post_trade_sector_pct",
                "projected_industry_pct",
            )),
            current_total_position_pct=current_total,
            projected_total_position_pct=_nonnegative_percentage(_first(
                value,
                "projected_total_position_pct",
                "post_trade_total_position_pct",
                "projected_portfolio_position_pct",
            )),
            max_correlation=max_correlation,
            drawdown_pct=drawdown,
        )


@dataclass(frozen=True, slots=True)
class LiquidityContext:
    adequate: bool | None = None
    status: str | None = None
    participation_pct: float | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LiquidityContext":
        status = str(_first(value, "status", "level") or "").strip().lower() or None
        adequate = _optional_bool(_first(value, "adequate", "sufficient", "is_liquid"))
        if adequate is None and status:
            if status in {"ok", "adequate", "sufficient", "liquid", "normal", "充足", "正常"}:
                adequate = True
            elif status in {"low", "insufficient", "illiquid", "blocked", "不足", "枯竭"}:
                adequate = False
        raw_participation, key = _first_with_key(
            value, "participation_pct", "order_participation_pct", "participation_rate",
        )
        participation = _percentage(raw_participation, ratio_alias=key == "participation_rate")
        if participation is not None and participation < 0:
            adequate = False
            participation = None
        if participation is None:
            order_value = _number(_first(value, "order_value_cny", "proposed_order_value"))
            daily_value = _number(_first(
                value, "avg_daily_turnover_cny", "average_daily_amount", "adv_cny",
            ))
            if order_value is not None and daily_value is not None and order_value >= 0 and daily_value > 0:
                participation = order_value / daily_value * 100
        return cls(adequate=adequate, status=status, participation_pct=participation)


@dataclass(frozen=True, slots=True)
class EventContext:
    major_event: bool | None = None
    severity: str | None = None
    unresolved: bool | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventContext":
        severity = str(_first(value, "severity", "level") or "").strip().lower() or None
        major = _optional_bool(_first(value, "major_event", "major", "is_major", "material"))
        if major is None and severity:
            if severity in {"critical", "high", "severe", "major", "重大", "严重", "高"}:
                major = True
            elif severity in {"none", "low", "normal", "minor", "无", "低", "正常"}:
                major = False
        return cls(
            major_event=major,
            severity=severity,
            unresolved=_optional_bool(_first(value, "unresolved", "pending")),
        )


@dataclass(frozen=True, slots=True)
class RiskGateInput:
    candidate: CandidateAction
    market: MarketContext
    portfolio: PortfolioContext
    liquidity: LiquidityContext
    event: EventContext

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RiskGateInput":
        if not isinstance(value, Mapping):
            raise TypeError("risk gate input must be a mapping")
        candidate_map = _mapping(value.get("candidate")) or value
        candidate = CandidateAction.from_mapping(candidate_map)

        market_map = _mapping(value.get("market"))
        if not market_map:
            risk_decision = _mapping(value.get("risk_decision"))
            market_map = risk_decision or value

        portfolio_map = _mapping(value.get("portfolio"))
        if not portfolio_map:
            portfolio_map = _mapping(value.get("holdings_context")) or value

        return cls(
            candidate=candidate,
            market=MarketContext.from_mapping(market_map),
            portfolio=PortfolioContext.from_mapping(portfolio_map, candidate),
            liquidity=LiquidityContext.from_mapping(_mapping(value.get("liquidity"))),
            event=EventContext.from_mapping(_mapping(value.get("event"))),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate": {
                "action": self.candidate.action,
                "symbol": self.candidate.symbol,
                "sector": self.candidate.sector,
                "confidence": self.candidate.confidence,
                "position_cap_pct": self.candidate.position_cap_pct,
                "position_supplied": self.candidate.position_supplied,
                "position_valid": self.candidate.position_valid,
                "confidence_supplied": self.candidate.confidence_supplied,
                "confidence_valid": self.candidate.confidence_valid,
                "evidence_ids": list(self.candidate.evidence_ids),
                "missing_evidence": list(self.candidate.missing_evidence),
                "critical_evidence_complete": self.candidate.critical_evidence_complete,
            },
            "market": {"risk_level": self.market.risk_level},
            "portfolio": {
                "holdings": [
                    {
                        "symbol": row.symbol,
                        "sector": row.sector,
                        "weight_pct": row.weight_pct,
                        "market_value": row.market_value,
                        "correlation_to_candidate": row.correlation_to_candidate,
                    }
                    for row in self.portfolio.holdings
                ],
                "target_position_pct": self.portfolio.target_position_pct,
                "sector_position_pct": self.portfolio.sector_position_pct,
                "projected_target_pct": self.portfolio.projected_target_pct,
                "projected_sector_pct": self.portfolio.projected_sector_pct,
                "current_total_position_pct": self.portfolio.current_total_position_pct,
                "projected_total_position_pct": self.portfolio.projected_total_position_pct,
                "max_correlation": self.portfolio.max_correlation,
                "drawdown_pct": self.portfolio.drawdown_pct,
            },
            "liquidity": {
                "adequate": self.liquidity.adequate,
                "status": self.liquidity.status,
                "participation_pct": self.liquidity.participation_pct,
            },
            "event": {
                "major_event": self.event.major_event,
                "severity": self.event.severity,
                "unresolved": self.event.unresolved,
            },
        }


@dataclass(frozen=True, slots=True)
class RiskGateDecision:
    decision: str
    final_action: str
    position_cap_pct: float
    effective_confidence: float | None
    reason_codes: tuple[str, ...]
    reasons: tuple[str, ...]
    rule_version: str
    input_snapshot_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "final_action": self.final_action,
            "position_cap_pct": self.position_cap_pct,
            "effective_confidence": self.effective_confidence,
            "reason_codes": list(self.reason_codes),
            "reasons": list(self.reasons),
            "rule_version": self.rule_version,
            "input_snapshot_hash": self.input_snapshot_hash,
        }


def _snapshot_hash(snapshot: RiskGateInput) -> str:
    payload = json.dumps(
        snapshot.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evaluate(value: RiskGateInput | Mapping[str, Any]) -> RiskGateDecision:
    """Evaluate a snapshot without mutating it or consulting external state."""
    snapshot = value if isinstance(value, RiskGateInput) else RiskGateInput.from_mapping(value)
    candidate = snapshot.candidate
    snapshot_hash = _snapshot_hash(snapshot)

    # A defensive action never needs permission from a gate whose purpose is
    # to prevent taking more risk.  Blocking an exit because liquidity or
    # event evidence is missing would invert the safety property.
    if candidate.action in _DEFENSIVE_ACTIONS | _NON_TRADING_ACTIONS:
        return RiskGateDecision(
            decision="allow",
            final_action=candidate.action,
            position_cap_pct=0.0,
            effective_confidence=candidate.confidence,
            reason_codes=(),
            reasons=(),
            rule_version=RULE_VERSION,
            input_snapshot_hash=snapshot_hash,
        )

    decision_rank = 0  # allow=0, reduce=1, veto=2
    reason_codes: list[str] = []
    reasons: list[str] = []

    def trigger(rank: int, code: str, message: str) -> None:
        nonlocal decision_rank
        decision_rank = max(decision_rank, rank)
        if code not in reason_codes:
            reason_codes.append(code)
            reasons.append(message)

    if candidate.action not in _ENTRY_ACTIONS:
        trigger(2, "action_invalid", "候选动作缺失或不在允许的动作集合内")

    if not candidate.position_valid:
        trigger(2, "position_invalid", "仓位建议不是有限的有效百分比")
    if not candidate.confidence_valid:
        trigger(1, "confidence_invalid", "置信度不是可识别的 0–1 或 0–100 数值")

    risk_level = snapshot.market.risk_level
    if risk_level is None:
        trigger(2, "market_risk_missing", "市场风险等级缺失或不在 1–5 级范围")
        candidate_cap = 0.0
        total_position_cap = 0.0
    else:
        candidate_cap = CANDIDATE_POSITION_CAP_PCT[risk_level]
        total_position_cap = TOTAL_POSITION_CAP_PCT[risk_level]
        if risk_level == 5:
            trigger(2, "market_risk_level_5", "市场风险为 5 级，禁止买入或加仓")

    liquidity = snapshot.liquidity
    participation_too_high = (
        liquidity.participation_pct is not None
        and liquidity.participation_pct > MAX_LIQUIDITY_PARTICIPATION_PCT
    )
    if liquidity.adequate is False or participation_too_high:
        observed = (
            f"，预计参与率 {liquidity.participation_pct:.2f}%"
            if liquidity.participation_pct is not None else ""
        )
        trigger(2, "liquidity_insufficient", f"流动性不足{observed}，禁止新增风险暴露")
    elif liquidity.adequate is None:
        trigger(2, "liquidity_check_missing", "缺少服务端流动性核验，禁止新增风险暴露")

    event = snapshot.event
    if event.major_event is True:
        trigger(2, "major_event", "存在重大事件风险，禁止买入或加仓")
    elif event.major_event is None:
        trigger(2, "event_check_missing", "缺少服务端重大事件核验，禁止新增风险暴露")

    if not candidate.sector:
        trigger(2, "sector_classification_missing", "缺少服务端行业归属，无法核验行业集中度")

    proposed = candidate.position_cap_pct
    if proposed is None:
        position_cap = 0.0
        if candidate.position_valid:
            trigger(2, "position_cap_missing", "未提供明确目标仓位，不得自动补成规则上限")
    else:
        position_cap = max(0.0, proposed)
        if proposed <= 0:
            trigger(2, "position_not_positive", "买入或加仓动作必须有正的仓位上限")

    if position_cap > candidate_cap:
        position_cap = candidate_cap
        trigger(1, "market_position_cap", f"市场风险 {risk_level} 级将单股目标仓位降至 {candidate_cap:.1f}%")

    if position_cap > SINGLE_NAME_CAP_PCT:
        position_cap = SINGLE_NAME_CAP_PCT
        trigger(1, "single_name_cap", f"单股仓位上限为 {SINGLE_NAME_CAP_PCT:.1f}%")

    portfolio = snapshot.portfolio
    if portfolio.holdings and any(not holding.sector for holding in portfolio.holdings):
        trigger(2, "portfolio_sector_missing", "现有持仓存在未知行业，无法排除行业集中风险")
    if portfolio.holdings and (
        portfolio.target_position_pct is None
        or portfolio.sector_position_pct is None
        or portfolio.current_total_position_pct is None
    ):
        trigger(2, "portfolio_weight_missing", "持仓缺少账户 NAV/仓位权重，无法核验集中度")
    current_target = max(0.0, portfolio.target_position_pct or 0.0)
    current_sector = max(0.0, portfolio.sector_position_pct or 0.0)
    current_total = portfolio.current_total_position_pct

    if current_total is None:
        trigger(2, "portfolio_total_position_missing", "缺少组合当前总仓位，无法核验总风险暴露")
    elif current_total >= total_position_cap:
        trigger(
            2,
            "total_position_limit_reached",
            f"当前组合总仓位 {current_total:.1f}% 已达到或超过风险 {risk_level} 级上限 {total_position_cap:.1f}%",
        )

    if candidate.action == "add" and current_target >= SINGLE_NAME_CAP_PCT:
        trigger(2, "single_name_limit_reached", "目标个股当前仓位已达到单股上限，不得继续加仓")

    if portfolio.projected_target_pct is not None and portfolio.projected_target_pct > SINGLE_NAME_CAP_PCT:
        excess = portfolio.projected_target_pct - SINGLE_NAME_CAP_PCT
        position_cap = max(0.0, position_cap - excess)
        trigger(1, "projected_single_name_cap", "预计交易后单股集中度超过上限，已收缩仓位")

    # position_cap_pct means the target total position, not an order delta.
    # Only the increment above the current target consumes sector headroom.
    sector_headroom = max(0.0, SECTOR_CAP_PCT - current_sector)
    sector_safe_target = current_target + sector_headroom
    if position_cap > sector_safe_target:
        position_cap = sector_safe_target
        trigger(1, "sector_concentration_cap", f"行业集中度上限为 {SECTOR_CAP_PCT:.1f}%，已按剩余空间缩减仓位")

    if portfolio.projected_sector_pct is not None and portfolio.projected_sector_pct > SECTOR_CAP_PCT:
        excess = portfolio.projected_sector_pct - SECTOR_CAP_PCT
        position_cap = max(0.0, position_cap - excess)
        trigger(1, "projected_sector_cap", "预计交易后行业集中度超过上限，已收缩仓位")

    # If a cap leaves no room to increase the current target, an add/buy
    # instruction is not executable and must be vetoed rather than described
    # misleadingly as a reduced buy.
    if position_cap <= current_target and candidate.action == "add":
        trigger(2, "no_concentration_headroom", "单股或行业集中度已无可加仓空间")
    if position_cap <= 0 and candidate.action == "buy":
        trigger(2, "no_position_headroom", "风险规则下已无可新建仓位")

    if (
        portfolio.max_correlation is not None
        and portfolio.max_correlation >= HIGH_CORRELATION
        and position_cap > 0
    ):
        position_cap *= 0.5
        trigger(1, "high_correlation", f"与现有持仓最高相关性达 {portfolio.max_correlation:.2f}，仓位减半")

    if portfolio.drawdown_pct is not None and portfolio.drawdown_pct >= DRAWDOWN_REDUCE_PCT:
        if portfolio.drawdown_pct >= DRAWDOWN_SEVERE_PCT:
            position_cap = min(position_cap, 1.0)
            trigger(1, "portfolio_drawdown_severe", f"组合回撤 {portfolio.drawdown_pct:.2f}%，新仓上限降至 1.0%")
        else:
            position_cap *= 0.5
            trigger(1, "portfolio_drawdown", f"组合回撤 {portfolio.drawdown_pct:.2f}%，仓位减半")

    if current_total is not None:
        computed_projected_total = current_total + max(0.0, position_cap - current_target)
        projected_total = max(
            computed_projected_total,
            portfolio.projected_total_position_pct or 0.0,
        )
        if projected_total >= total_position_cap:
            trigger(
                2,
                "projected_total_position_limit",
                f"预计交易后组合总仓位 {projected_total:.1f}% 将达到或超过风险 {risk_level} 级上限 {total_position_cap:.1f}%",
            )

    missing_evidence = list(candidate.missing_evidence)
    if not candidate.critical_evidence_complete:
        missing_evidence.append("candidate_critical_evidence")
    if liquidity.adequate is None and liquidity.participation_pct is None:
        missing_evidence.append("liquidity")
    if event.major_event is None:
        missing_evidence.append("major_event_check")
    missing_evidence = sorted(set(missing_evidence))

    if (
        not candidate.critical_evidence_complete
        or candidate.missing_evidence
    ):
        trigger(
            2,
            "candidate_evidence_missing",
            "候选标的关键证据缺失或不完整，禁止买入或加仓",
        )

    effective_confidence = candidate.confidence
    if (
        effective_confidence is not None
        and effective_confidence > HIGH_CONFIDENCE
        and missing_evidence
    ):
        effective_confidence = MAX_CONFIDENCE_WITH_MISSING_EVIDENCE
        position_cap = min(position_cap, CANDIDATE_POSITION_CAP_PCT[3])
        trigger(
            1,
            "high_confidence_without_evidence",
            "关键证据不完整，不允许保留高置信度：" + "、".join(missing_evidence),
        )

    if decision_rank >= 2:
        decision = "veto"
        final_action = "avoid"
        position_cap = 0.0
    elif decision_rank == 1:
        decision = "reduce"
        final_action = candidate.action
    else:
        decision = "allow"
        final_action = candidate.action

    return RiskGateDecision(
        decision=decision,
        final_action=final_action,
        position_cap_pct=round(max(0.0, position_cap), 4),
        effective_confidence=(
            round(effective_confidence, 6) if effective_confidence is not None else None
        ),
        reason_codes=tuple(reason_codes),
        reasons=tuple(reasons),
        rule_version=RULE_VERSION,
        input_snapshot_hash=snapshot_hash,
    )


def evaluate_risk_gate(value: RiskGateInput | Mapping[str, Any]) -> dict[str, Any]:
    """Dictionary-returning compatibility wrapper for API/event integration."""
    return evaluate(value).as_dict()


__all__ = [
    "RULE_VERSION",
    "CANDIDATE_POSITION_CAP_PCT",
    "MARKET_POSITION_CAP_PCT",
    "TOTAL_POSITION_CAP_PCT",
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
