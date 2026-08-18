from __future__ import annotations

from io import BytesIO
import json
import time
from urllib.parse import urlparse

from openpyxl import Workbook

from hermes_trader.data import free_source, official_universe


def _tdx_names(*, include_bse: bool = True) -> dict[str, str]:
    names = {
        **{str(600000 + index): f"沪{index}" for index in range(1600)},
        **{str(index + 1).zfill(6): f"深{index}" for index in range(2100)},
    }
    if include_bse:
        names.update({str(920000 + index): f"北{index}" for index in range(100)})
    names["688563"] = "航材股份"
    return names


def test_tdx_is_the_only_complete_authority_and_persists_v3(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    tdx_names = _tdx_names()
    monkeypatch.setattr(
        free_source, "_bounded_tdx_api_universe", lambda _timeout: tdx_names
    )
    monkeypatch.setattr(
        official_universe,
        "fetch_exchange",
        lambda *_args: (_ for _ in ()).throw(AssertionError("主名录不得请求交易所全表")),
    )

    names = free_source.stock_universe.__wrapped__()

    assert names == tdx_names
    status = free_source.stock_universe_status()
    assert status["source"] == "TDX 数据 API 股票列表"
    assert status["authority"] == "tdx_instruments_a"
    assert status["complete"] is True
    assert status["missing_exchanges"] == []
    assert free_source.cached_stock_identity("688563") == {
        "code": "688563",
        "known": True,
        "name": "航材股份",
        "universe_complete": True,
        "universe_as_of": status["as_of"],
    }
    payload = json.loads(free_source._universe_cache_path().read_text(encoding="utf-8"))
    assert payload["schema"] == "a-share-universe-v3"
    assert payload["authority"] == "tdx_instruments_a"
    assert payload["names"]["688563"] == "航材股份"


def test_tdx_missing_exchange_is_saved_but_never_marked_complete(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    tdx_names = _tdx_names(include_bse=False)
    monkeypatch.setattr(
        free_source, "_bounded_tdx_api_universe", lambda _timeout: tdx_names
    )

    assert free_source.stock_universe.__wrapped__() == tdx_names
    status = free_source.stock_universe_status()
    assert status["complete"] is False
    assert status["refresh_required"] is True
    assert status["missing_exchanges"] == ["bse"]


def test_legacy_or_official_complete_cache_is_downgraded_without_deletion(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    names = _tdx_names()
    path = free_source._universe_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "a-share-universe-v2",
        "saved_at": "2026-08-18T10:00:00+08:00",
        "saved_at_epoch": time.time(),
        "as_of": "2026-08-18",
        "source": "沪深京交易所官网公开列表",
        "complete": True,
        "refresh_required": False,
        "missing_exchanges": [],
        "names": names,
    }, ensure_ascii=False), encoding="utf-8")

    assert free_source._load_universe_cache() == names
    status = free_source.stock_universe_status()
    assert status["complete"] is False
    assert status["refresh_required"] is True
    assert status["degradation_reason"] == "legacy_or_non_tdx_authority"
    assert path.exists()


def test_fresh_tdx_v3_last_good_is_complete_without_network(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    names = _tdx_names()
    free_source._save_universe_cache(names, {
        "source": "TDX 数据 API 股票列表",
        "authority": "tdx_instruments_a",
        "coverage": {"sse": 1601, "szse": 2100, "bse": 100, "total": len(names)},
        "complete": True,
        "refresh_required": False,
        "missing_exchanges": [],
    })
    free_source._set_universe_status({"source": "unavailable", "count": 0})

    status = free_source.stock_universe_status()

    assert status["source"] == "TDX 数据 API 股票列表"
    assert status["authority"] == "tdx_instruments_a"
    assert status["complete"] is True
    assert status["refresh_required"] is False


def test_saving_tdx_refresh_invalidates_warmed_identity_universe(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    old_names = _tdx_names()
    old_names.pop("688563")
    old_names["688562"] = "旧快照样本"
    metadata = {
        "source": "TDX 数据 API 股票列表",
        "authority": "tdx_instruments_a",
        "complete": True,
        "refresh_required": False,
        "missing_exchanges": [],
    }
    free_source._save_universe_cache(old_names, metadata)
    assert "688563" not in free_source.cached_stock_universe()

    refreshed_names = {**old_names, "688563": "航材股份"}
    free_source._save_universe_cache(refreshed_names, metadata)

    assert free_source.cached_stock_universe()["688563"] == "航材股份"


def test_tdx_last_good_is_marked_incomplete_when_stale_then_hard_expires(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    names = _tdx_names()
    free_source._save_universe_cache(names, {
        "source": "TDX 数据 API 股票列表",
        "authority": "tdx_instruments_a",
        "coverage": {"total": len(names)},
        "as_of": "2026-05-01",
        "complete": True,
    })
    path = free_source._universe_cache_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["saved_at_epoch"] = time.time() - free_source._UNIVERSE_CACHE_FRESH_AGE - 1
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert free_source._load_universe_cache() == names
    status = free_source.stock_universe_status()
    assert status["stale"] is True
    assert status["complete"] is False
    assert status["refresh_required"] is True

    payload["saved_at_epoch"] = time.time() - free_source._UNIVERSE_CACHE_MAX_STALE_AGE - 1
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert free_source._load_universe_cache() is None


def test_tdx_failure_never_promotes_official_bootstrap_to_complete(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(free_source, "_bounded_tdx_api_universe", lambda _timeout: None)

    names = free_source.stock_universe.__wrapped__()

    assert names is not None
    status = free_source.stock_universe_status()
    assert status["cache"] == "bundled-bootstrap"
    assert status["complete"] is False
    assert status["refresh_required"] is True


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
