from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_trader.analysis.engine import Engine


@pytest.mark.asyncio
async def test_company_context_includes_bounded_official_disclosure_metadata(
    monkeypatch, tmp_path,
) -> None:
    from hermes_trader.analysis import engine as engine_mod
    from hermes_trader.data import (
        free_source, mainforce_source, market, official_disclosure,
        official_interaction, stock,
    )

    monkeypatch.setattr(
        stock,
        "stock_detail",
        lambda code: {
            "code": code,
            "minute": [],
            "daily": [],
            "board": {"industry": "银行"},
            "quote": {},
        },
    )
    monkeypatch.setattr(market, "market_sectors", lambda: {"sectors": []})
    monkeypatch.setattr(market, "market_flow", lambda: {"ok": True})
    monkeypatch.setattr(market, "market_overview", lambda: {"ok": True})
    monkeypatch.setattr(mainforce_source, "longhu", lambda: {"data_date": "2026-07-13", "list": [], "inst": []})
    monkeypatch.setattr(free_source, "fina_indicator", lambda code: {"code": code, "roe": 8.2})
    monkeypatch.setattr(
        engine_mod.tdx_api_source,
        "after_hours_research",
        lambda code: {"ok": True, "code": code},
    )
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda store: {"senti": 50})
    seen = {}

    def fake_disclosures(code, start, end, limit, verify_pdf, hash_limit, cache_path):
        seen.update({
            "code": code,
            "start": start,
            "end": end,
            "limit": limit,
            "verify_pdf": verify_pdf,
            "hash_limit": hash_limit,
            "cache_path": cache_path,
        })
        return {
            "state": "complete",
            "verification_state": "cross_confirmed",
            "data_as_of": end,
            "fetched_at": "2026-07-13T18:00:00Z",
            "degraded_reasons": [],
            "items": [{
                "announcement_date": "2026-07-10",
                "published_at": "2026-07-10",
                "title": "测试公告",
                "original_url": "https://disc.static.szse.cn/example.pdf",
                "source_confirmations": ["cninfo", "szse"],
                "pdf_bytes": b"must-not-enter-model-context",
                "raw_payload": {"secret": "must-not-enter-model-context"},
            }],
        }

    monkeypatch.setattr(official_disclosure, "cached_official_disclosures", fake_disclosures)
    interaction_seen = {}

    def fake_interactions(code, start, end, limit, data_dir):
        interaction_seen.update(code=code, start=start, end=end, limit=limit, data_dir=data_dir)
        return {
            "state": "complete",
            "data_as_of": end,
            "fetched_at": "2026-07-13T18:01:00Z",
            "is_formal_disclosure": False,
            "degraded_reasons": [],
            "items": [{
                "question": "订单进度如何？",
                "question_pub_date": "2026-07-10T10:00:00+08:00",
                "answered": True,
                "answer": "按计划推进。",
                "answer_pub_date": "2026-07-11T10:00:00+08:00",
                "raw_payload": "must-not-enter-model-context",
            }],
        }

    monkeypatch.setattr(official_interaction, "cached_official_interactions", fake_interactions)
    engine = Engine(SimpleNamespace(data_dir=tmp_path), object(), object())

    context = await engine.chat_context("company", code="000001")

    evidence = context["官方公告证据(近90日·不参与日内评分)"]
    assert evidence["state"] == "complete"
    assert evidence["verification_state"] == "cross_confirmed"
    assert evidence["items"] == [{
        "announcement_date": "2026-07-10",
        "published_at": "2026-07-10",
        "title": "测试公告",
        "original_url": "https://disc.static.szse.cn/example.pdf",
        "source_confirmations": ["cninfo", "szse"],
        "verification": "cross_confirmed",
    }]
    assert seen["limit"] == 12
    assert seen["verify_pdf"] is False
    assert seen["hash_limit"] == 1
    cache_path = Path(seen["cache_path"])
    assert cache_path.parts[-2:] == ("official-disclosures", "000001.json")
    interactions = context["交易所互动问答(近90日·Tier3·不参与日内评分)"]
    assert interactions["is_formal_disclosure"] is False
    assert interactions["items"] == [{
        "question": "订单进度如何？",
        "question_pub_date": "2026-07-10T10:00:00+08:00",
        "answered": True,
        "answer": "按计划推进。",
        "answer_pub_date": "2026-07-11T10:00:00+08:00",
        "source_type": "管理层回复",
    }]
    assert interaction_seen["code"] == "000001"
    assert interaction_seen["limit"] == 6
    assert interaction_seen["data_dir"] == str(tmp_path)
    assert "must-not-enter-model-context" not in repr(context)


@pytest.mark.asyncio
async def test_company_context_degrades_unsupported_exchange_without_failing_report(
    monkeypatch, tmp_path,
) -> None:
    from hermes_trader.analysis import engine as engine_mod
    from hermes_trader.data import (
        free_source, mainforce_source, market, official_disclosure,
        official_interaction, stock,
    )

    monkeypatch.setattr(
        stock,
        "stock_detail",
        lambda code: {"code": code, "minute": [], "daily": [], "board": {}, "quote": {}},
    )
    monkeypatch.setattr(market, "market_sectors", lambda: {"sectors": []})
    monkeypatch.setattr(market, "market_flow", lambda: {"ok": True})
    monkeypatch.setattr(market, "market_overview", lambda: {"ok": True})
    monkeypatch.setattr(mainforce_source, "longhu", lambda: {"data_date": None, "list": [], "inst": []})
    monkeypatch.setattr(free_source, "fina_indicator", lambda _code: {})
    monkeypatch.setattr(engine_mod.tdx_api_source, "after_hours_research", lambda code: {"code": code})
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda _store: {"senti": 50})
    monkeypatch.setattr(
        official_disclosure,
        "cached_official_disclosures",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported exchange must not call SSE/SZSE adapter")
        ),
    )
    monkeypatch.setattr(
        official_interaction,
        "cached_official_interactions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported exchange must not call interaction adapter")
        ),
    )
    engine = Engine(SimpleNamespace(data_dir=tmp_path), object(), object())

    context = await engine.chat_context("company", code="920001")

    evidence = context["官方公告证据(近90日·不参与日内评分)"]
    assert context["个股代码"] == "920001"
    assert evidence["state"] == "not_supported"
    assert evidence["items"] == []
    interaction = context["交易所互动问答(近90日·Tier3·不参与日内评分)"]
    assert interaction["state"] == "not_supported"
    assert interaction["items"] == []
