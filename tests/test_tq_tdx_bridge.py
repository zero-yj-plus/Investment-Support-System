from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tq_tdx_bridge.py"
SPEC = importlib.util.spec_from_file_location("tq_tdx_bridge", SCRIPT)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class FakeTQ:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, method: str, params: dict | None = None):
        params = params or {}
        self.calls.append((method, params))
        if method == "get_market_snapshot":
            code = params["stock_code"]
            return {
                "Now": "10.50",
                "LastClose": "10.00",
                "Open": "10.10",
                "High": "10.80",
                "Low": "9.90",
                "Amount": "12345678",
                "Volume": "123400",
                "BuyPrice": ["10.49", "10.48"],
                "BuyVol": ["1000", "2000"],
                "SellPrice": ["10.51", "10.52"],
                "SellVol": ["3000", "4000"],
            }
        if method == "get_stock_list":
            return [
                {"Code": "000001.SZ", "Name": "平安银行"},
                {"Code": "600000.SH", "Name": "浦发银行"},
                {"Code": "830001.BJ", "Name": "北交样本"},
            ]
        if method == "get_sector_list":
            return [{"Code": "881001", "Name": "银行"}, {"Code": "880001", "Name": "融资融券"}]
        if method == "get_stock_list_in_sector":
            return [{"Code": "000001.SZ", "Name": "平安银行"}]
        if method == "get_relation":
            return [{"BlockCode": "881001", "BlockName": "银行", "BlockType": "行业"}]
        if method == "get_stock_info":
            return {"J_jyl": "12.5", "J_mgsy": "1.2"}
        if method == "get_pricevol":
            return {
                code: {"Now": "10.5", "LastClose": "10", "Volume": "123400"}
                for code in params["stock_list"]
            }
        raise AssertionError(method)


def app() -> bridge.BridgeApp:
    return bridge.BridgeApp(FakeTQ(), "secret")


def auth() -> dict[str, str]:
    return {"x-api-key": "secret"}


def test_authentication_and_read_only_gate() -> None:
    service = app()
    status, _ = service.dispatch("GET", "/v1/realtime/status", {})
    assert status == 401
    status, payload = service.dispatch("POST", "/v1/realtime/status", auth())
    assert status == 405
    assert payload["error"] == "read_only"


def test_quote_contract_and_units() -> None:
    service = app()
    status, payload = service.dispatch("GET", "/v1/realtime/quotes?codes=000001.SZ", auth())
    assert status == 200
    quote = payload["quotes"][0]
    assert quote["code"] == "000001.SZ"
    assert quote["amount"] == 1234.5678  # TQ 元 -> TDX 契约万元
    assert quote["volume"] == 1234.0  # TQ 股 -> TDX 契约手
    assert quote["buyv"] == [10.0, 20.0]
    assert quote["sellv"] == [30.0, 40.0]


def test_stock_universe_and_cursor_pagination() -> None:
    service = app()
    status, first = service.dispatch("GET", "/v1/data/instruments_a?limit=2", auth())
    assert status == 200
    assert len(first["rows"]) == 2
    assert first["has_more"] is True
    assert first["next_cursor"] == "2"
    assert first["rows"][0]["payload"]["Name"] == "平安银行"
    status, second = service.dispatch("GET", "/v1/data/instruments_a?limit=2&cursor=2", auth())
    assert status == 200
    assert second["rows"][0]["entity_key"] == "830001.BJ"
    assert second["has_more"] is False


def test_sectors_members_and_relations() -> None:
    service = app()
    status, sectors = service.dispatch("GET", "/v1/data/sectors", auth())
    assert status == 200
    assert sectors["rows"][0]["entity_key"] == "881001.SH"

    status, members = service.dispatch("GET", "/v1/data/sector_members?entities=881001.SH", auth())
    assert status == 200
    assert members["rows"][0]["payload"]["Code"] == "000001.SZ"

    status, relations = service.dispatch("GET", "/v1/data/relation?entities=000001.SZ", auth())
    assert status == 200
    row = relations["rows"][0]
    assert row["payload"]["BlockType"] == "行业"
    assert row["payload"]["BlockCode"] == "881001.SH"


def test_stock_info_and_pricevol() -> None:
    service = app()
    status, info = service.dispatch("GET", "/v1/data/stock_info?entities=000001.SZ", auth())
    assert status == 200
    assert info["rows"][0]["payload"]["J_jyl"] == "12.5"

    status, pricevol = service.dispatch("GET", "/v1/data/pricevol", auth())
    assert status == 200
    assert pricevol["rows"][0]["payload"]["Volume"] == 1234.0


def test_unsupported_capabilities_are_explicit() -> None:
    service = app()
    for path in (
        "/v1/funds/sectors/rank",
        "/v1/data/news_sentiment",
        "/v1/data/gp_one",
        "/v1/data/holders_top10",
    ):
        status, payload = service.dispatch("GET", path, auth())
        assert status == 501
        assert payload["error"] == "unsupported_by_tq_bridge"
        assert payload["missing"] is True


def test_tq_client_rejects_non_loopback_and_non_readonly_method() -> None:
    try:
        bridge.TQClient("http://192.168.1.8:17709")
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("non-loopback TQ URL should be rejected")

    client = bridge.TQClient("http://127.0.0.1:17709")
    try:
        client.call("order_stock", {})
    except bridge.BridgeError as exc:
        assert exc.status == 403
        assert exc.code == "read_only_method_denied"
    else:
        raise AssertionError("trading method should be rejected")
