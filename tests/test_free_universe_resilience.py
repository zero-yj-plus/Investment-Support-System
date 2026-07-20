from __future__ import annotations

from io import BytesIO
import json
import time
from urllib.parse import urlparse

from openpyxl import Workbook

from hermes_trader.data import free_source, official_universe


def _part(exchange: str, names: dict[str, str], *, complete: bool = True, error=None):
    return official_universe.ExchangeUniverse(
        exchange=exchange,
        names=names,
        source_url=official_universe.OFFICIAL_ENDPOINTS[exchange],
        fetched_at="2026-07-12T10:00:00+08:00",
        elapsed_ms=10,
        complete=complete,
        error=error,
    )


def test_single_exchange_failure_still_builds_and_persists_denominator(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(free_source, "_bounded_tdx_api_universe", lambda _timeout: None)
    parts = {
        "sse": _part("sse", {str(600000 + i): f"沪{i}" for i in range(1600)}),
        "szse": _part("szse", {str(i + 1).zfill(6): f"深{i}" for i in range(1600)}),
        "bse": _part("bse", {}, complete=False, error="timeout"),
    }
    monkeypatch.setattr(
        official_universe, "fetch_exchange", lambda exchange, _timeout: parts[exchange]
    )

    names = free_source.stock_universe.__wrapped__()

    assert names is not None and len(names) == 3200
    status = free_source.stock_universe_status()
    assert status["source"] == "沪深京交易所官网公开列表"
    assert status["refresh_required"] is True
    # 达标后可立即返回；故障分支若已结束则记录错误，否则不等它。
    assert status["coverage"].get("bse", {}).get("error") in (None, "timeout")
    payload = json.loads(free_source._universe_cache_path().read_text(encoding="utf-8"))
    assert payload["schema"] == "a-share-universe-v2"
    assert len(payload["names"]) == 3200
    assert payload["refresh_required"] is True
    assert "bse" in payload["missing_exchanges"]

    # 重启后不能因快照年龄很新，就丢掉「缺北交所」状态。
    free_source._set_universe_status({"source": "unavailable"})
    assert free_source._load_universe_cache() == names
    reloaded = free_source.stock_universe_status()
    assert reloaded["refresh_required"] is True
    assert reloaded["complete"] is False
    assert "bse" in reloaded["missing_exchanges"]


def test_last_good_is_marked_stale_then_rejected_after_hard_expiry(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    names = {str(600000 + i): f"样本{i}" for i in range(3000)}
    free_source._save_universe_cache(names, {
        "source": "交易所官网公开列表",
        "sources": [official_universe.SSE_DOWNLOAD_URL],
        "coverage": {"total": 3000},
        "as_of": "2026-05-01",
    })
    path = free_source._universe_cache_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["saved_at_epoch"] = time.time() - free_source._UNIVERSE_CACHE_FRESH_AGE - 1
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert free_source._load_universe_cache() == names
    status = free_source.stock_universe_status()
    assert status["stale"] is True
    assert status["refresh_required"] is True
    assert status["as_of"] == "2026-05-01"

    payload["saved_at_epoch"] = time.time() - free_source._UNIVERSE_CACHE_MAX_STALE_AGE - 1
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert free_source._load_universe_cache() is None


def test_status_lazily_reads_fresh_customer_last_good_without_network(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    names = {str(600000 + i): f"样本{i}" for i in range(3000)}
    free_source._save_universe_cache(names, {
        "source": "沪深京交易所官网公开列表",
        "coverage": {"total": 3000},
        "complete": True,
        "refresh_required": False,
        "missing_exchanges": [],
    })
    free_source._set_universe_status({
        "source": "unavailable", "count": 0, "stale": True, "refresh_required": True,
    })

    status = free_source.stock_universe_status()

    assert status["source"] == "沪深京交易所官网公开列表"
    assert status["count"] == 3000
    assert status["complete"] is True
    assert status["stale"] is False
    assert status["refresh_required"] is False


def test_coverage_below_3000_never_becomes_last_good(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(free_source, "_bounded_tdx_api_universe", lambda _timeout: None)
    parts = {
        "sse": _part("sse", {str(600000 + i): f"沪{i}" for i in range(1499)}),
        "szse": _part("szse", {str(i + 1).zfill(6): f"深{i}" for i in range(1500)}),
        "bse": _part("bse", {}, complete=False),
    }
    monkeypatch.setattr(
        official_universe, "fetch_exchange", lambda exchange, _timeout: parts[exchange]
    )
    monkeypatch.setattr(free_source, "_load_bootstrap_universe", lambda: None)

    assert free_source.stock_universe.__wrapped__() is None
    assert not free_source._universe_cache_path().exists()


def test_tdx_api_total_count_does_not_hide_missing_exchange(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    tdx_names = {
        **{str(600000 + i): f"沪{i}" for i in range(1600)},
        **{str(i + 1).zfill(6): f"深{i}" for i in range(1600)},
    }
    monkeypatch.setattr(free_source, "_bounded_tdx_api_universe", lambda _timeout: tdx_names)
    monkeypatch.setattr(
        official_universe,
        "fetch_exchange",
        lambda exchange, _timeout: _part(exchange, {}, complete=False, error="offline"),
    )

    names = free_source.stock_universe.__wrapped__()

    assert names == tdx_names
    status = free_source.stock_universe_status()
    assert status["complete"] is False
    assert status["refresh_required"] is True
    assert "bse" in status["missing_exchanges"]


def test_partial_official_candidate_waits_briefly_for_complete_tdx_api(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(free_source, "_UNIVERSE_TDX_GRACE", 0.20)
    official_parts = {
        "sse": _part("sse", {str(600000 + i): f"沪{i}" for i in range(1600)}),
        "szse": _part("szse", {str(i + 1).zfill(6): f"深{i}" for i in range(1600)}),
    }

    def official(exchange: str, _timeout: float):
        if exchange == "bse":
            time.sleep(0.50)
            return _part("bse", {}, complete=False, error="slow")
        return official_parts[exchange]

    complete_tdx = {
        **{str(600000 + i): f"沪{i}" for i in range(1600)},
        **{str(i + 1).zfill(6): f"深{i}" for i in range(2100)},
        **{str(920000 + i): f"北{i}" for i in range(100)},
    }

    def tdx(_timeout: float):
        time.sleep(0.05)
        return complete_tdx

    monkeypatch.setattr(official_universe, "fetch_exchange", official)
    monkeypatch.setattr(free_source, "_bounded_tdx_api_universe", tdx)

    names = free_source.stock_universe.__wrapped__()

    assert names == complete_tdx
    status = free_source.stock_universe_status()
    assert status["source"] == "沪深京交易所官网 + TDX 数据 API 交叉并集"
    assert status["complete"] is True
    assert status["missing_exchanges"] == []


def test_partial_official_waits_for_bse_when_tdx_api_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(free_source, "_UNIVERSE_RACE_TIMEOUT", 1.5)
    monkeypatch.setattr(free_source, "_bounded_tdx_api_universe", lambda _timeout: None)
    parts = {
        "sse": _part("sse", {str(600000 + i): f"沪{i}" for i in range(1600)}),
        "szse": _part("szse", {str(i + 1).zfill(6): f"深{i}" for i in range(2100)}),
        "bse": _part("bse", {str(920000 + i): f"北{i}" for i in range(100)}),
    }

    def official(exchange: str, _timeout: float):
        if exchange == "bse":
            time.sleep(0.15)
        return parts[exchange]

    monkeypatch.setattr(official_universe, "fetch_exchange", official)

    names = free_source.stock_universe.__wrapped__()

    assert names is not None and len(names) == 3800
    status = free_source.stock_universe_status()
    assert status["complete"] is True
    assert status["missing_exchanges"] == []


def test_complete_official_and_tdx_are_merged_before_canonical_persist(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(free_source, "_UNIVERSE_TDX_GRACE", 0.20)
    parts = {
        "sse": _part("sse", {str(600000 + i): f"沪{i}" for i in range(1600)}),
        "szse": _part("szse", {str(i + 1).zfill(6): f"深{i}" for i in range(2100)}),
        "bse": _part("bse", {str(920000 + i): f"北{i}" for i in range(100)}),
    }
    official_names = {code: name for part in parts.values() for code, name in part.names.items()}
    tdx_names = {
        **official_names,
        "603998": "新增活跃股",
        "603999": "样本退",
    }
    monkeypatch.setattr(
        official_universe, "fetch_exchange", lambda exchange, _timeout: parts[exchange]
    )
    monkeypatch.setattr(free_source, "_bounded_tdx_api_universe", lambda _timeout: tdx_names)

    names = free_source.stock_universe.__wrapped__()

    assert names is not None
    assert "603998" in names
    assert "603999" not in names
    assert len(names) == len(official_names) + 1
    status = free_source.stock_universe_status()
    assert status["source"] == "沪深京交易所官网 + TDX 数据 API 交叉并集"
    assert status["coverage"]["tdx_only"] == 1
    assert status["complete"] is True


def test_bundled_official_bootstrap_is_explicitly_stale(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))

    names = free_source._load_bootstrap_universe()

    assert names is not None and len(names) == 4904
    status = free_source.stock_universe_status()
    assert status["cache"] == "bundled-bootstrap"
    assert status["stale"] is True
    assert status["refresh_required"] is True
    assert status["coverage"] == {
        "sse": 2009, "szse": 2895, "bse": 0, "total": 4904, "missing": ["bse"]
    }


def test_official_parsers_accept_exchange_native_formats() -> None:
    sse = official_universe._parse_sse_text(
        "公司代码\t公司简称\t代码\t简称\n600000\t浦发银行\t600000\t浦发银行\n".encode("gb18030")
    )
    assert sse == {"600000": "浦发银行"}

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["板块", "A股代码", "A股简称"])
    sheet.append(["主板", 1, "平安银行"])
    buf = BytesIO()
    workbook.save(buf)
    assert official_universe._parse_szse_xlsx(buf.getvalue()) == {"000001": "平安银行"}

    row = [""] * 48
    row[38], row[40] = "920001", "北证样本"
    bse, pages = official_universe._decode_bse(
        json.dumps([{"totalPages": 1, "content": [row]}], ensure_ascii=False).encode()
    )
    assert pages == 1
    assert bse == {"920001": "北证样本"}


def test_official_universe_endpoints_are_free_and_keyless() -> None:
    allowed_hosts = {"query.sse.com.cn", "www.szse.cn", "www.bse.cn"}
    assert {urlparse(url).hostname for url in official_universe.OFFICIAL_ENDPOINTS.values()} == allowed_hosts
    for url in official_universe.OFFICIAL_ENDPOINTS.values():
        lower = url.lower()
        assert "access_token" not in lower
        assert "api_key" not in lower
        assert "tushare" not in lower


def test_official_download_enforces_total_wall_clock_and_closes(monkeypatch) -> None:
    class SlowResponse:
        headers = {"Content-Length": "40960"}

        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def read(self, _size):
            time.sleep(0.012)
            return b"x" * 1024

    response = SlowResponse()

    class SlowOpener:
        def open(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(official_universe, "_OPENER", SlowOpener())
    started = time.monotonic()
    result = official_universe._request_bytes(
        "https://www.szse.cn/slow",
        label="深交所慢下载测试",
        timeout=0.20,
        referer="https://www.szse.cn/",
    )

    assert result is None
    assert time.monotonic() - started < 0.35
    assert response.closed is True
