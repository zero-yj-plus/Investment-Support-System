from __future__ import annotations

from pathlib import Path

import pytest

from hermes_trader.analysis import engine as engine_module
from hermes_trader.analysis.advice_proposal import proposal_ledger_for
from hermes_trader.analysis.engine import (
    Engine,
    _sanitize_blocked_report_advice_lines,
    _sanitize_blocked_report_price_lines,
    _sanitize_unbound_report_price_lines,
    validate_report_advice_governance,
)
from hermes_trader.knowledge.store import Store


def _snapshot(level: int) -> dict:
    return {
        "command": {"risk_level": level, "rule_version": "test-risk-v1"},
        "as_of": "2026-07-13 14:52:00",
    }


def test_report_governance_blocks_direct_order_language_at_any_level() -> None:
    result = validate_report_advice_governance("建议立即买入 000001", _snapshot(1))

    assert result["ok"] is False
    assert "direct_order_language" in result["errors"]
    assert result["blocked_line_numbers"] == [1]


def test_report_governance_blocks_any_positive_entry_at_risk_five() -> None:
    result = validate_report_advice_governance("000001 观察后可介入", _snapshot(5))

    assert result["ok"] is False
    assert "risk_level_5_entry_proposal" in result["errors"]


def test_report_governance_allows_negated_or_proposal_language() -> None:
    defensive = validate_report_advice_governance("风险高，不买入，不加仓。", _snapshot(5))
    proposal = validate_report_advice_governance(
        "000001 候选观察，待触发条件满足后再走风险闸。", _snapshot(2),
    )

    assert defensive["ok"] is True
    assert proposal["ok"] is True


def test_blocked_price_line_is_removed_before_revalidation() -> None:
    content = "# 收盘复盘\n- 回踩 10.20 元后观察\n- 保持低仓位"
    sanitized, meta = _sanitize_unbound_report_price_lines(
        content,
        {
            "ok": False,
            "claims": [{
                "line": 2,
                "status": "blocked",
                "reason": "price_claim_not_bound_to_stock",
                "value": 10.20,
            }],
        },
    )

    assert "10.20" not in sanitized
    assert "暂不给点位" in sanitized
    assert "未绑定到唯一股票" in sanitized
    assert "保持低仓位" in sanitized
    assert meta == {
        "applied": True,
        "reason": "blocked_stock_price_lines_removed",
        "line_count": 1,
        "lines": [2],
        "original_blocked_count": 1,
        "blocked_reasons": ["price_claim_not_bound_to_stock"],
        "replacements": [{
            "line": 2,
            "format": "text_line",
            "code": None,
            "name": None,
            "blocked_reasons": ["price_claim_not_bound_to_stock"],
        }],
        "blocked_claims": [{
            "line": 2,
            "reason": "price_claim_not_bound_to_stock",
            "value": 10.20,
        }],
    }


@pytest.mark.parametrize(
    "reason",
    [
        "stock_not_in_injected_context",
        "price_claim_bound_to_multiple_stocks",
        "stock_has_no_price_evidence",
        "price_outside_stock_evidence_range",
    ],
)
def test_price_sanitizer_removes_all_locally_blocked_claim_failures(reason: str) -> None:
    content = "- 000001 平安银行回踩 99.99 元后买入"

    sanitized, meta = _sanitize_blocked_report_price_lines(
        content,
        {"claims": [{"line": 1, "status": "blocked", "reason": reason}]},
    )

    assert "99.99" not in sanitized
    assert "暂不给点位" in sanitized
    assert meta is not None
    assert meta["blocked_reasons"] == [reason]


def test_price_sanitizer_preserves_markdown_table_shape_and_stock_identity() -> None:
    content = (
        "| 代码 | 名称 | 所属方向 | 入选逻辑 | 今日动作 |\n"
        "|---|---|---|---|---|\n"
        "| 600396 | 华电辽能 | 电力 | 对比华银电力 | 回踩16.16元，跌破15.5元放弃 |\n"
        "| 301526 | 国际复材 | 新材料 | 成交活跃 | 只观察 |"
    )
    validation = {
        "claims": [{
            "line": 3,
            "status": "blocked",
            "reason": "price_claim_bound_to_multiple_stocks",
            "value": 16.16,
            "candidate_codes": ["600396", "600744"],
            "candidate_names": ["华电辽能", "华银电力"],
        }],
    }

    sanitized, meta = _sanitize_blocked_report_price_lines(content, validation)
    lines = sanitized.splitlines()

    assert lines[2].startswith("| 600396 | 华电辽能 |")
    assert lines[2].endswith("|")
    assert lines[3].startswith("| 301526 | 国际复材 |")
    assert "16.16" not in sanitized
    assert "15.5" not in sanitized
    assert "同一行提及多只股票" in lines[2]
    assert meta is not None
    assert meta["replacements"][0] == {
        "line": 3,
        "format": "markdown_table_row",
        "code": "600396",
        "name": "华电辽能",
        "blocked_reasons": ["price_claim_bound_to_multiple_stocks"],
    }
    assert meta["blocked_claims"][0]["candidate_codes"] == ["600396", "600744"]


def test_morning_prompt_forbids_multiple_stock_identities_in_one_table_row() -> None:
    prompt = engine_module.prompts._morning()

    assert "一行只写一只股票" in prompt
    assert "禁止提及其他股票代码或名称" in prompt
    assert "仍须输出完整五列表格行" in prompt


def test_advice_sanitizer_removes_only_known_text_level_violations() -> None:
    content = "# 盘前\n- 建议立即买入 000001\n- 保持观察"
    governance = validate_report_advice_governance(content, _snapshot(2))

    sanitized, meta = _sanitize_blocked_report_advice_lines(content, governance)

    assert "立即买入" not in sanitized
    assert "未授权的新增风险动作" in sanitized
    assert "保持观察" in sanitized
    assert meta == {
        "applied": True,
        "reason": "blocked_advice_lines_removed",
        "line_count": 1,
        "lines": [2],
        "original_errors": ["direct_order_language"],
    }
    assert validate_report_advice_governance(sanitized, _snapshot(2))["ok"] is True


def test_advice_sanitizer_cannot_bypass_missing_risk_snapshot() -> None:
    content = "建议立即买入 000001"
    governance = validate_report_advice_governance(content, None)

    sanitized, meta = _sanitize_blocked_report_advice_lines(content, governance)

    assert sanitized == content
    assert meta is None


class _Config:
    client_id = "report-governance-test"

    @staticmethod
    def section(_name: str) -> dict:
        return {}


class _Hermes:
    def __init__(self, content: str):
        self.content = content

    async def chat_stream(self, *_args, **_kwargs):
        yield self.content


class _LengthThenStopHermes:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat_stream(self, *_args, **kwargs):
        self.calls.append(kwargs)
        meta = kwargs["meta"]
        if len(self.calls) == 1:
            meta["finish_reason"] = "length"
            yield "这是一份必须丢弃的截断稿"
            raise RuntimeError("Hermes Agent 运行未完成（length）")
        meta["finish_reason"] = "stop"
        yield "# 完整日报\n\n风险闸未额外阻断，仅保留观察。"


async def _collect(stream) -> list[dict]:
    return [event async for event in stream]


@pytest.mark.asyncio
async def test_report_length_falls_back_once_and_discards_truncated_draft(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    hermes = _LengthThenStopHermes()
    engine = Engine(_Config(), store, hermes)
    monkeypatch.setattr(
        engine, "_context_for_report",
        lambda _rtype: _async_value({"今日日期": "2026-07-13"}),
    )
    monkeypatch.setattr(engine_module, "is_trade_day", lambda: False)
    monkeypatch.setattr(
        engine_module,
        "validate_report_finalization",
        lambda *_args, **_kwargs: {"ok": True, "status": "passed"},
    )

    events = await _collect(engine.generate_report_stream("morning"))
    report = store.get_report("morning", "2026-07-13")

    assert [call["thinking_mode"] for call in hermes.calls] == ["enabled", "disabled"]
    assert report is not None
    assert "完整日报" in report["content_md"]
    assert "必须丢弃的截断稿" not in report["content_md"]
    assert events[-1]["done"] is True


@pytest.mark.asyncio
async def test_report_degrades_direct_order_text_before_release(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    engine = Engine(_Config(), store, _Hermes("建议立即买入 000001"))
    monkeypatch.setattr(
        engine, "_context_for_report",
        lambda _rtype: _async_value({"今日日期": "2026-07-13"}),
    )
    monkeypatch.setattr(engine_module, "is_trade_day", lambda: False)
    monkeypatch.setattr(
        engine_module, "validate_report_finalization",
        lambda *_args, **_kwargs: {"ok": True, "status": "passed"},
    )

    events = await _collect(engine.generate_report_stream("morning"))
    report = store.get_report("morning", "2026-07-13")

    assert events[-1]["done"] is True
    assert report is not None
    assert "立即买入" not in report["content_md"]
    assert "未授权的新增风险动作" in report["content_md"]
    assert report["advice_governance"]["sanitation"]["line_count"] == 1
    assert report["claim_validation"]["post_governance_revalidated"] is True
    assert all("立即买入" not in event.get("delta", "") for event in events)


@pytest.mark.asyncio
async def test_validated_report_records_proposals_before_releasing_content(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    content = "趋势观察候选 000001，待触发后再走风险闸。"
    engine = Engine(_Config(), store, _Hermes(content))
    monkeypatch.setattr(
        engine, "_context_for_report",
        lambda _rtype: _async_value({"今日日期": "2026-07-13"}),
    )
    monkeypatch.setattr(engine_module, "is_trade_day", lambda: False)
    monkeypatch.setattr(
        engine_module, "validate_report_finalization",
        lambda *_args, **_kwargs: {"ok": True, "status": "passed"},
    )

    events = await _collect(engine.generate_report_stream("trend"))
    report = store.get_report("trend", "2026-07-13")

    assert report is not None
    assert report["advice_governance"]["proposal_count"] == 1
    assert report["advice_governance"]["proposal_ids"]
    proposals = proposal_ledger_for(store).events()
    assert len(proposals) == 1
    assert proposals[0]["source_artifact"]["content_hash"] == report["content_hash"]
    deltas = [event for event in events if event.get("delta")]
    assert deltas and all(event.get("validated") is True for event in deltas)
    assert events[-1]["done"] is True


@pytest.mark.asyncio
async def test_report_degrades_only_unbound_price_line_and_saves_audited_result(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    content = "# 收盘复盘\n- 回踩 10.20 元后观察\n- 风险偏高，保持低仓位。"
    engine = Engine(_Config(), store, _Hermes(content))
    monkeypatch.setattr(
        engine, "_context_for_report",
        lambda _rtype: _async_value({"今日日期": "2026-07-13"}),
    )
    monkeypatch.setattr(engine_module, "is_trade_day", lambda: False)

    events = await _collect(engine.generate_report_stream("close"))
    report = store.get_report("close", "2026-07-13")

    assert events[-1]["done"] is True
    assert report is not None
    assert "10.20" not in report["content_md"]
    assert "暂不给点位" in report["content_md"]
    assert "未绑定到唯一股票" in report["content_md"]
    assert report["claim_validation"]["status"] == "passed"
    assert report["claim_validation"]["sanitation"]["line_count"] == 1


@pytest.mark.asyncio
async def test_report_degrades_out_of_range_price_line_and_saves_audited_result(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    content = "# 早盘内参\n- 国瓷材料 300285：回踩 150 元后观察\n- 保持低仓位。"
    context = {
        "今日日期": "2026-07-13",
        "用户持仓": [{
            "code": "300285",
            "name": "国瓷材料",
            "实时行情": {
                "code": "300285",
                "name": "国瓷材料",
                "price": 80.80,
                "high": 81.09,
                "low": 74.00,
                "prev_close": 74.60,
            },
        }],
    }
    engine = Engine(_Config(), store, _Hermes(content))
    monkeypatch.setattr(
        engine, "_context_for_report", lambda _rtype: _async_value(context),
    )
    monkeypatch.setattr(engine_module, "is_trade_day", lambda: False)

    events = await _collect(engine.generate_report_stream("close"))
    report = store.get_report("close", "2026-07-13")

    assert events[-1]["done"] is True
    assert report is not None
    assert "150" not in report["content_md"]
    assert "暂不给点位" in report["content_md"]
    assert "超出该股真实行情校验区间" in report["content_md"]
    sanitation = report["claim_validation"]["sanitation"]
    assert sanitation["blocked_reasons"] == ["price_outside_stock_evidence_range"]


async def _async_value(value):
    return value
