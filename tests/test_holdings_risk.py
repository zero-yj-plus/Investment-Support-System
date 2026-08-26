from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_trader.analysis import holdings_risk
from hermes_trader.api import routes
from hermes_trader.knowledge.store import Store


class Request:
    def __init__(self, state, body: dict | None = None):
        self.app = SimpleNamespace(state=state)
        self._body = body or {}

    async def json(self):
        return self._body


def test_shadow_service_never_contains_formal_notification_side_effect() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "hermes_trader"
        / "analysis"
        / "holdings_risk.py"
    ).read_text(encoding="utf-8")

    assert "await self.notifier.push_text" not in source
    assert holdings_risk.RULE_VERSION == "holding-risk-v1-shadow-r2"
    assert "未读取真实持仓、行情或风险状态" in holdings_risk.push_test_message()


@pytest.mark.asyncio
async def test_holdings_risk_get_returns_shadow_rows_without_runtime_state(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_holding_risk_state("000001", {
        "code": "000001",
        "state": "yellow",
        "action": "暂停加仓，等待确认",
        "runtime_state": {"algorithm_stop": 9.2},
    })

    class Service:
        def snapshots(self):
            return {
                "000001": {
                    "code": "000001",
                    "state": "yellow",
                    "action": "暂停加仓，等待确认",
                }
            }

        def market_context(self):
            return {"benchmark": "沪深300"}

    result = await routes.get_holdings_risk(Request(SimpleNamespace(
        store=store,
        holding_risk=Service(),
    )))

    assert result["ok"] is True
    assert result["shadow_mode"] is True
    assert result["push_enabled"] is False
    assert result["rule_version"] == "holding-risk-v1-shadow-r2"
    assert result["market_context"]["benchmark"] == "沪深300"
    assert result["rows"]["000001"]["action"] == "暂停加仓，等待确认"
    assert "runtime_state" not in result["rows"]["000001"]


@pytest.mark.asyncio
async def test_holdings_risk_refresh_endpoint_preserves_shadow_contract() -> None:
    class Service:
        async def refresh_all(self, force: bool = False):
            assert force is True
            return {
                "ok": True,
                "shadow_mode": True,
                "push_enabled": False,
                "rule_version": "holding-risk-v1-shadow-r2",
                "rows": {},
            }

    result = await routes.refresh_holdings_risk(Request(SimpleNamespace(
        holding_risk=Service(),
    )))

    assert result == {
        "ok": True,
        "shadow_mode": True,
        "push_enabled": False,
        "rule_version": "holding-risk-v1-shadow-r2",
        "rows": {},
    }
