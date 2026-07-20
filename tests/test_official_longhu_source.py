from __future__ import annotations

from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest

from hermes_trader.data import official_longhu_source as longhu


BEIJING = ZoneInfo("Asia/Shanghai")
TRADE_DAY = "2026-07-13"


def _sse_row() -> dict:
    return {
        "secTxVolume": "79,663,158",
        "secCode": "603137",
        "refType": "1",
        "secType": "A",
        "tradeDate": "20260713",
        "abnormalEnd": "20260713",
        "abnormalStart": "20260709",
        "secTxAmount": "2176296806.00",
        "secAbbr": "恒尚节能",
        "branchNameB": "机构专用,国泰海通证券股份有限公司总部",
        "branchTxAmtB": "43706341.20,40048548.40",
        "branchNameS": "华泰证券股份有限公司深圳彩田路证券营业部,机构专用",
        "branchTxAmtS": "42854746.00,35014677.00",
    }


def _sse_payload(rows: list[dict] | None = None) -> dict:
    return {
        "isPagination": "false",
        "pageHelp": {"data": list(rows if rows is not None else [_sse_row()])},
    }


def _szse_row(code: str, reason_code: str, *, amount="11.19", volume="4,575.92") -> dict:
    return {
        "dqrq": TRADE_DAY,
        "zqdm": code,
        "zqjc": f"股票{code}",
        "cjje": amount,
        "cjsl": volume,
        "plyy": "日价格振幅达到16.93%",
        "bz": (
            "<a a-param='/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1842_detal"
            f"&DQRQ={TRADE_DAY}&ZQDM={code}&ZBDM={reason_code}'>查看详情</a>"
        ),
    }


def _szse_summary(
    rows: list[dict], *, page_no: int = 1, page_count: int = 1, record_count: int | None = None,
) -> list[dict]:
    return [{
        "metadata": {
            "catalogid": "1842_xxpl_after",
            "recordcount": len(rows) if record_count is None else record_count,
            "pagecount": page_count,
            "pageno": page_no,
            "pagesize": 10,
        },
        "data": rows,
        "error": None,
    }]


def _szse_detail() -> list[dict]:
    return [
        {
            "metadata": {
                "catalogid": "1842_detal",
                "tabkey": "tab1",
                "cols": {"dqrq": "公告日期"},
            },
            "data": [{"dqrq": TRADE_DAY}],
        },
        {
            "metadata": {
                "catalogid": "1842_detal",
                "tabkey": "tab2",
                "cols": {"mmlb": "买/卖", "zsmc": "会员营业部名称"},
            },
            "data": [
                {
                    "mmlb": "买1",
                    "zsmc": "深股通专用",
                    "mrje": "87,806,514",
                    "mcje": "72,230,977",
                },
                {
                    "mmlb": "卖1",
                    "zsmc": "机构专用",
                    "mrje": "41,835,046",
                    "mcje": "48,558,114",
                },
            ],
        },
    ]


class FakeTransport:
    def __init__(
        self,
        *,
        sse_error: bool = False,
        szse_error: bool = False,
        empty: bool = False,
        fail_szse_page: int | None = None,
        detail_error: bool = False,
        two_szse_pages: bool = False,
    ):
        self.sse_error = sse_error
        self.szse_error = szse_error
        self.empty = empty
        self.fail_szse_page = fail_szse_page
        self.detail_error = detail_error
        self.two_szse_pages = two_szse_pages
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs) -> bytes:
        self.calls.append((url, kwargs))
        if url == longhu.SSE_LIST_URL:
            if self.sse_error:
                raise longhu.DisclosureFetchError("sse unavailable")
            return json.dumps(_sse_payload([] if self.empty else None)).encode()
        if url != longhu.SZSE_REPORT_URL:
            raise AssertionError(url)
        params = kwargs.get("params") or {}
        catalog = params.get("CATALOGID")
        if catalog == "1842_detal":
            if self.detail_error:
                raise longhu.DisclosureFetchError("seat detail unavailable")
            return json.dumps(_szse_detail()).encode()
        if catalog != "1842_xxpl_after":
            raise AssertionError(params)
        if self.szse_error:
            raise longhu.DisclosureFetchError("szse unavailable")
        page = int(params.get("PAGENO") or 1)
        if self.fail_szse_page == page:
            raise longhu.DisclosureFetchError(f"page {page} unavailable")
        if self.empty:
            payload = _szse_summary([], page_count=0, record_count=0)
        elif self.two_szse_pages:
            row = _szse_row("000048" if page == 1 else "300001", "0902" if page == 1 else "0901")
            payload = _szse_summary([row], page_no=page, page_count=2, record_count=2)
        else:
            payload = _szse_summary([_szse_row("000048", "0902")])
        return json.dumps(payload).encode()


def _fetch(transport: FakeTransport, *, hour: int = 22, **kwargs):
    return longhu.fetch_official_longhu(
        TRADE_DAY,
        transport=transport,
        now_fn=lambda: datetime(2026, 7, 13, hour, 0, tzinfo=BEIJING),
        trade_day_fn=lambda _day: True,
        **kwargs,
    )


def test_sse_parser_normalizes_event_and_embedded_seats_without_inventing_reason() -> None:
    events, issues, meta = longhu.parse_sse_summary(_sse_payload(), TRADE_DAY)

    assert issues == []
    assert meta == {"raw_row_count": 1, "excluded_non_a_share": 0}
    assert len(events) == 1
    event = events[0]
    assert event["exchange"] == "sse"
    assert event["trade_date"] == TRADE_DAY
    assert event["code"] == "603137"
    assert event["reason_code"] == "1"
    assert event["reason_text"] is None
    assert event["turnover_amount"] == pytest.approx(2_176_296_806)
    assert event["traded_volume"] == 79_663_158
    assert event["currency"] == "CNY"
    assert event["seat_detail_state"] == "complete"
    assert [(seat["ranked_side"], seat["rank"]) for seat in event["seats"]] == [
        ("buy", 1), ("buy", 2), ("sell", 1), ("sell", 2),
    ]
    assert event["seats"][0]["is_institution"] is True
    # SSE only publishes the ranked-side amount in this response.  It is not
    # legitimate to fabricate the opposite side and a net amount.
    assert event["seats"][0]["sell_amount_yuan"] is None
    assert event["seats"][0]["net_amount_yuan"] is None


def test_szse_parsers_convert_report_units_once_and_keep_both_seat_amounts() -> None:
    events, issues, meta = longhu.parse_szse_summary(
        _szse_summary([_szse_row("000048", "0902")]),
        TRADE_DAY,
    )

    assert issues == []
    assert meta["record_count"] == 1
    event = events[0]
    assert event["turnover_amount"] == pytest.approx(1_119_000_000)
    assert event["turnover_amount_unit"] == "yuan"
    assert event["traded_volume"] == 45_759_200
    assert event["traded_volume_unit"] == "shares_or_units"
    assert event["reason_code"] == "0902"
    assert event["reason_text"] == "日价格振幅达到16.93%"

    seats, state, detail_issues = longhu.parse_szse_detail(_szse_detail(), event)
    assert state == "complete"
    assert detail_issues == []
    assert len(seats) == 2
    assert seats[0]["ranked_amount_yuan"] == 87_806_514
    assert seats[0]["net_amount_yuan"] == 15_575_537
    assert seats[1]["ranked_side"] == "sell"
    assert seats[1]["ranked_amount_yuan"] == 48_558_114
    assert seats[1]["net_amount_yuan"] == -6_723_068


def test_both_official_sources_and_all_szse_pages_produce_complete_contract() -> None:
    transport = FakeTransport(two_szse_pages=True)
    result = _fetch(transport)

    assert result["ok"] is True
    assert result["state"] == "complete"
    assert result["trade_date"] == TRADE_DAY
    assert result["event_count"] == 3
    assert result["seat_count"] == 8
    assert result["empty_confirmed"] is False
    assert result["completeness"] == {
        "expected_exchanges": ["sse", "szse"],
        "complete_exchanges": ["sse", "szse"],
        "live_exchanges": ["sse", "szse"],
    }
    assert result["content_hash"]
    assert result["evidence"]["schema_version"] == "data-evidence-v1"
    assert result["evidence"]["provider_family"] == "sse+szse"
    assert result["evidence"]["data_as_of"] == TRADE_DAY
    by_source = {source["provider_family"]: source for source in result["sources"]}
    assert by_source["szse"]["expected_records"] == 2
    assert by_source["szse"]["received_records"] == 2
    assert by_source["szse"]["pages_fetched"] == 2
    assert by_source["szse"]["detail_requests_completed"] == 2


def test_one_official_source_failure_is_degraded_after_settlement_cutoff() -> None:
    result = _fetch(FakeTransport(sse_error=True))

    assert result["ok"] is True
    assert result["state"] == "degraded"
    assert result["event_count"] == 1
    by_source = {source["provider_family"]: source for source in result["sources"]}
    assert by_source["sse"]["state"] == "missing"
    assert by_source["szse"]["state"] == "complete"
    assert "sse" in result["completeness"]["expected_exchanges"]
    assert result["missing"] == []


def test_both_official_source_failures_are_missing_not_a_successful_empty_list() -> None:
    result = _fetch(FakeTransport(sse_error=True, szse_error=True))

    assert result["ok"] is False
    assert result["state"] == "missing"
    assert result["events"] == []
    assert result["content_hash"] is None
    assert result["evidence"]["content_present"] is False
    assert result["missing"] == ["官方龙虎榜"]


def test_before_publication_window_is_not_due_and_makes_no_network_request() -> None:
    transport = FakeTransport(sse_error=True, szse_error=True)
    result = _fetch(transport, hour=14)

    assert result["ok"] is True
    assert result["state"] == "not_due"
    assert result["events"] == []
    assert result["missing"] == []
    assert transport.calls == []


def test_empty_current_day_responses_are_settling_before_internal_cutoff() -> None:
    result = _fetch(FakeTransport(empty=True), hour=16)

    assert result["ok"] is True
    assert result["state"] == "settling"
    assert result["event_count"] == 0
    assert result["empty_confirmed"] is False
    assert result["missing"] == []
    assert result["completeness"]["complete_exchanges"] == ["sse", "szse"]


def test_partial_current_day_response_is_settling_not_prematurely_degraded() -> None:
    result = _fetch(FakeTransport(sse_error=True), hour=16)

    assert result["ok"] is True
    assert result["state"] == "settling"
    assert result["event_count"] == 1
    assert result["completeness"]["complete_exchanges"] == ["szse"]


def test_empty_current_day_responses_are_confirmed_complete_only_after_cutoff() -> None:
    result = _fetch(FakeTransport(empty=True), hour=22)

    assert result["state"] == "complete"
    assert result["event_count"] == 0
    assert result["empty_confirmed"] is True
    assert result["content_hash"]


def test_failed_later_szse_page_keeps_first_page_but_degrades_completeness() -> None:
    result = _fetch(FakeTransport(two_szse_pages=True, fail_szse_page=2))

    assert result["state"] == "degraded"
    assert result["event_count"] == 2  # SSE + usable SZSE page 1
    szse = next(source for source in result["sources"] if source["provider_family"] == "szse")
    assert szse["state"] == "degraded"
    assert szse["pages_fetched"] == 1
    assert szse["expected_records"] == 2
    assert szse["received_records"] == 1
    assert any("pagination incomplete" in issue for issue in szse["issues"])


def test_failed_szse_seat_detail_keeps_event_summary_but_degrades_source() -> None:
    result = _fetch(FakeTransport(detail_error=True))

    assert result["state"] == "degraded"
    event = next(item for item in result["events"] if item["exchange"] == "szse")
    assert event["seat_detail_state"] == "unavailable"
    assert event["seats"] == []
    assert any("seat detail" in reason for reason in result["degraded_reasons"])


def test_szse_detail_safety_cap_is_explicit_degradation_and_bounds_requests() -> None:
    transport = FakeTransport(two_szse_pages=True)
    result = _fetch(transport, max_detail_requests=1)

    assert result["state"] == "degraded"
    szse = next(source for source in result["sources"] if source["provider_family"] == "szse")
    assert szse["detail_requests_expected"] == 2
    assert szse["detail_requests_completed"] == 1
    assert any("safety cap reached" in issue for issue in szse["issues"])
    detail_calls = [
        call for call in transport.calls
        if (call[1].get("params") or {}).get("CATALOGID") == "1842_detal"
    ]
    assert len(detail_calls) == 1


def test_non_trading_day_is_not_due_without_network() -> None:
    transport = FakeTransport()
    result = longhu.fetch_official_longhu(
        "2026-07-12",
        transport=transport,
        now_fn=lambda: datetime(2026, 7, 13, 22, 0, tzinfo=BEIJING),
        trade_day_fn=lambda _day: False,
    )

    assert result["state"] == "not_due"
    assert "非交易日" in result["availability_reason"]
    assert transport.calls == []


def test_customer_scoped_snapshot_keeps_last_good_on_live_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(longhu, "_DATA_ROOT", tmp_path / "official-longhu")
    complete = _fetch(FakeTransport())
    missing = _fetch(FakeTransport(sse_error=True, szse_error=True))
    not_due = {**complete, "state": "not_due", "events": []}

    saved = longhu.save_snapshot(complete)
    rejected = longhu.save_snapshot(missing)
    not_due_rejected = longhu.save_snapshot(not_due)
    restored = longhu.load_snapshot(TRADE_DAY)

    assert saved is not None and saved.exists()
    assert saved.stat().st_mode & 0o777 == 0o600
    assert rejected is None
    assert not_due_rejected is None
    assert restored is not None
    assert restored["content_hash"] == complete["content_hash"]
    assert restored["state"] == "complete"


@pytest.mark.parametrize("value", ["202607", "2026/07/13", "not-a-date"])
def test_invalid_trade_date_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="trade_date"):
        longhu.fetch_official_longhu(value)
