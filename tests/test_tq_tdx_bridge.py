from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace


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
    try:
        status, payload = service.dispatch("GET", "/v1/realtime/quotes?codes=000001.SZ", auth())
        assert status == 200
        quote = payload["quotes"][0]
        assert quote["code"] == "000001.SZ"
        assert quote["amount"] == 1234.5678  # TQ 元 -> TDX 契约万元
        assert quote["volume"] == 1234.0  # TQ 股 -> TDX 契约手
        assert quote["buyv"] == [10.0, 20.0]
        assert quote["sellv"] == [30.0, 40.0]
    finally:
        service.close()


def test_wait_zero_returns_pending_without_waiting_for_slow_tq() -> None:
    release = threading.Event()

    class SlowTQ(FakeTQ):
        def call(self, method: str, params: dict | None = None):
            if method == "get_market_snapshot":
                release.wait(1.0)
            return super().call(method, params)

    service = bridge.BridgeApp(SlowTQ(), "secret")
    started = time.monotonic()
    try:
        status, payload = service.dispatch(
            "GET",
            "/v1/realtime/quotes?codes=000001.SZ&max_age=30&wait=0",
            auth(),
        )
        elapsed = time.monotonic() - started
        assert status == 200
        assert elapsed < 0.2
        assert payload["count"] == 0
        assert payload["quotes"] == [{"code": "000001.SZ", "pending": True}]

        release.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            _status, warmed = service.dispatch(
                "GET",
                "/v1/realtime/quotes?codes=000001.SZ&max_age=30&wait=0",
                auth(),
            )
            if warmed["count"] == 1:
                break
            time.sleep(0.01)
        assert warmed["count"] == 1
        assert warmed["quotes"][0]["now"] == 10.5
    finally:
        release.set()
        service.close()


def test_large_quote_batch_uses_tq_pricevol_once() -> None:
    service = app()
    codes = [f"{index:06d}.SZ" for index in range(65)]
    try:
        status, payload = service.dispatch(
            "GET",
            f"/v1/realtime/quotes?codes={','.join(codes)}&wait=2",
            auth(),
        )
        assert status == 200
        assert payload["count"] == 65
        assert len(payload["quotes"]) == 65
        assert service.client.calls == [("get_pricevol", {"stock_list": codes})]
    finally:
        service.close()


def test_waiting_request_reuses_existing_quote_refresh() -> None:
    release = threading.Event()

    class SlowTQ(FakeTQ):
        def call(self, method: str, params: dict | None = None):
            if method == "get_market_snapshot":
                release.wait(1.0)
            return super().call(method, params)

    client = SlowTQ()
    service = bridge.BridgeApp(client, "secret")
    try:
        _status, first = service.dispatch(
            "GET",
            "/v1/realtime/quotes?codes=000001.SZ&wait=0",
            auth(),
        )
        assert first["quotes"][0]["pending"] is True

        timer = threading.Timer(0.05, release.set)
        timer.start()
        status, second = service.dispatch(
            "GET",
            "/v1/realtime/quotes?codes=000001.SZ&wait=1",
            auth(),
        )
        timer.join()
        assert status == 200
        assert second["count"] == 1
        assert len(client.calls) == 1
    finally:
        release.set()
        service.close()


def test_handler_swallows_client_disconnect_during_body_write() -> None:
    class DisconnectedBody:
        @staticmethod
        def write(_raw: bytes) -> None:
            raise ConnectionAbortedError(10053, "client closed")

    handler = bridge.BridgeHandler.__new__(bridge.BridgeHandler)
    handler.server = SimpleNamespace(app=app())
    handler.command = "GET"
    handler.path = "/v1/realtime/status"
    handler.headers = {}
    handler.wfile = DisconnectedBody()
    handler.send_response = lambda _status: None
    handler.send_header = lambda _key, _value: None
    handler.end_headers = lambda: None
    try:
        handler._handle()
    finally:
        handler.server.app.close()


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
    assert members["rows"][0]["payload"]["Name"] == "平安银行"
    assert service.client.calls[-1] == (
        "get_stock_list_in_sector",
        {"block_code": "881001.SH", "block_type": 0, "list_type": 1},
    )

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
