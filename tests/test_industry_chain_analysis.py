import asyncio

from hermes_trader.analysis import industry_chain
from hermes_trader.analysis.engine import Engine
from hermes_trader.api.routes import _governed_chain_chat_stream


def _sample_analysis() -> dict:
    catalog = {
        "881001": {
            "code": "881001",
            "name": "机器人",
            "kind": "industry",
            "data_date": "2026-07-17",
        },
        "880123": {
            "code": "880123",
            "name": "机器人概念",
            "kind": "concept",
            "data_date": "2026-07-17",
        },
        "881999": {
            "code": "881999",
            "name": "食品饮料",
            "kind": "industry",
            "data_date": "2026-07-17",
        },
    }
    membership = {
        "by_sector": {
            "881001": ("000001", "000002"),
            "880123": ("000002", "000003"),
        },
        "member_count": 4,
        "data_date": "2026-07-17",
    }
    quotes = {
        "000001": {
            "code": "000001",
            "name": "甲公司",
            "price": 10,
            "change_pct": 1.2,
            "amount": 300_000_000,
            "pe": 20,
            "pb": 2,
            "total_mv": 10_000_000_000,
            "source": "TDX 数据 API",
            "as_of": "2026-07-17 10:00:00",
        },
        "000002": {
            "code": "000002",
            "name": "乙公司",
            "price": 8,
            "change_pct": -0.5,
            "amount": 100_000_000,
            "source": "腾讯财经",
            "as_of": "2026-07-17 10:00:01",
        },
        "000003": {
            "code": "000003",
            "name": "丙公司",
            "price": 6,
            "change_pct": 0.1,
            "amount": 20_000_000,
            "source": "腾讯财经",
            "as_of": "2026-07-17 10:00:01",
        },
        # 即使行情批次混入非成员，也不得进入产业公司映射。
        "999999": {
            "code": "999999",
            "name": "非成员",
            "price": 99,
            "amount": 9_999_999_999,
        },
    }
    return industry_chain.build_analysis(
        "机器人",
        catalog=catalog,
        membership=membership,
        quotes=quotes,
        sector_market={
            "ts": "2026-07-17 10:00:02",
            "sectors": [
                {"name": "机器人", "kind": "industry", "change_pct": 1.5},
            ],
        },
        generated_at="2026-07-17 10:00:03",
    )


def test_chain_analysis_only_maps_real_tdx_members_and_score_is_recomputable() -> None:
    analysis = _sample_analysis()

    assert analysis["schema_version"] == "industry-chain-analysis.v1"
    assert analysis["coverage"]["tdx_member_count"] == 3
    assert {row["code"] for row in analysis["company_mapping"]} == {
        "000001", "000002", "000003",
    }
    assert "999999" not in {row["code"] for row in analysis["company_mapping"]}
    assert all(row["verification"] == "tdx_sector_member" for row in analysis["company_mapping"])
    assert all((row["chain_role"] or {}).get("status") == "unverified" for row in analysis["company_mapping"])

    for row in analysis["company_mapping"]:
        assert row["data_researchability_score"] == round(
            sum(row["data_researchability_components"].values()), 2,
        )
    assert analysis["company_mapping"][0]["code"] == "000001"
    assert "research_priority_score" not in analysis["company_mapping"][0]
    assert "research_priorities" not in analysis
    assert analysis["research_trade_separation"]["trade_timing"] == "not_evaluated"


def test_chain_markdown_has_fixed_eight_sections_and_truthful_header_footer() -> None:
    rendered = industry_chain.render_markdown(_sample_analysis())

    expected = [
        "### 1. 需求本质",
        "### 2. 产业地图",
        "### 3. 价值捕获",
        "### 4. 卡点",
        "### 5. 国产替代",
        "### 6. 公司映射",
        "### 7. 兑现与估值",
        "### 8. 研究优先级与失效条件",
    ]
    positions = [rendered.index(title) for title in expected]
    assert positions == sorted(positions)
    assert "**产业结论：" in rendered
    assert "**当前交易时点：未评估" in rendered
    assert "> 数据时点：" in rendered
    assert "> 来源覆盖：" in rendered
    assert "> 页首缺口：" in rendered
    assert "**页末证据与缺口复核**" in rendered
    assert "不是产业研究优先级、投资排名或买入排名" in rendered
    assert "数据可研分" in rendered


def test_unmatched_chain_keeps_framework_and_refuses_company_ranking() -> None:
    analysis = industry_chain.build_analysis(
        "不存在的产业",
        catalog={},
        membership={},
        quotes={"000001": {"price": 10, "amount": 1_000_000}},
        generated_at="2026-07-17 10:00:00",
    )

    assert analysis["company_mapping"] == []
    assert analysis["evidence_verification_queue"] == []
    assert analysis["research_trade_separation"]["trade_timing"] == "not_evaluated"
    assert any(gap["dimension"] == "TDX 板块匹配" for gap in analysis["data_gaps"])


def test_public_board_normalization_uses_real_constituents_and_names() -> None:
    matched = industry_chain._public_board_matches(
        "光模块",
        [
            {"板块代码": "BK1234", "板块名称": "CPO概念", "_kind": "concept"},
            {"板块代码": "BK5678", "板块名称": "食品饮料", "_kind": "industry"},
        ],
    )
    codes, names = industry_chain._public_member_rows([
        {"代码": "300001", "名称": "甲光电"},
        {"代码": "000002", "名称": "乙通信"},
        {"代码": "invalid", "名称": "无效记录"},
    ])

    assert [row["code"] for row in matched] == ["BK1234"]
    assert codes == ["300001", "000002"]
    assert names == {"300001": "甲光电", "000002": "乙通信"}


def test_public_board_adapter_calls_keyless_akshare_endpoints(monkeypatch) -> None:
    class FakeAk:
        @staticmethod
        def stock_board_concept_name_em():
            return [{"板块代码": "BK1234", "板块名称": "CPO概念"}]

        @staticmethod
        def stock_board_industry_name_em():
            return []

        @staticmethod
        def stock_board_concept_cons_em(*, symbol):
            assert symbol == "CPO概念"
            return [{"代码": "300001", "名称": "甲光电"}]

    def fake_safe(function, *args, **kwargs):
        kwargs.pop("label", None)
        kwargs.pop("timeout", None)
        return function(*args, **kwargs)

    monkeypatch.setattr(industry_chain, "get_ak", lambda: FakeAk())
    monkeypatch.setattr(industry_chain, "safe", fake_safe)

    result = industry_chain._public_board_membership.__wrapped__("光模块")

    assert result is not None
    assert result["verification"] == "public_board_member"
    assert result["by_sector"] == {"BK1234": ("300001",)}
    assert result["names"] == {"300001": "甲光电"}


def test_public_membership_and_official_clue_work_without_realtime_quotes() -> None:
    analysis = industry_chain.build_analysis(
        "光模块",
        catalog={
            "BK1234": {
                "code": "BK1234",
                "name": "CPO概念",
                "kind": "concept",
                "data_date": "2026-07-28",
            },
        },
        membership={
            "by_sector": {"BK1234": ("300001",)},
            "names": {"300001": "甲光电"},
            "data_date": "2026-07-28",
            "source": "东方财富公开板块（AKShare）",
            "catalog_source": "东方财富公开行业/概念目录（AKShare）",
            "verification": "public_board_member",
        },
        quotes={},
        company_evidence={
            "300001": {
                "status": "available",
                "data_as_of": "2026-07-27",
                "matched_terms": ["光模块"],
                "formal_disclosures": [
                    {"title": "关于光模块项目进展的公告", "evidence_level": "tier_1"},
                ],
                "financial_snapshot": {"end_date": "20260331", "roe": 5.0},
            },
        },
        evidence_refresh={"status": "available", "cached_count": 1, "limit": 5},
        generated_at="2026-07-28 10:00:00",
    )

    company = analysis["company_mapping"][0]
    assert company["name"] == "甲光电"
    assert company["verification"] == "public_board_member"
    assert company["quote"]["price"] is None
    assert company["chain_role"]["status"] == "evidence_clue"
    assert company["chain_role"]["value"] is None
    assert company["realization"]["status"] == "financial_context_only"
    assert analysis["research_trade_separation"]["trade_timing"] == "not_evaluated"
    assert analysis["coverage"]["company_evidence_available_count"] == 1


def test_collect_analysis_falls_back_when_tdx_members_are_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        industry_chain.tdx_api,
        "sector_catalog",
        lambda: {
            "880001": {
                "code": "880001",
                "name": "CPO概念",
                "kind": "concept",
                "data_date": "2026-07-28",
            },
        },
    )
    monkeypatch.setattr(
        industry_chain.tdx_api,
        "sector_members_for_codes",
        lambda _codes: {"by_sector": {"880001": ()}},
    )
    monkeypatch.setattr(
        industry_chain.market,
        "market_sectors",
        lambda: {"sectors": []},
    )
    monkeypatch.setattr(
        industry_chain.stock,
        "quick_quotes",
        lambda _codes: {},
    )
    monkeypatch.setattr(
        industry_chain,
        "_public_board_membership",
        lambda _industry: {
            "state": "complete",
            "matched_sectors": [{
                "code": "BK1234",
                "name": "CPO概念",
                "kind": "concept",
                "data_date": "2026-07-28",
            }],
            "by_sector": {"BK1234": ("300001",)},
            "names": {"300001": "甲光电"},
            "data_date": "2026-07-28",
            "source": "东方财富公开板块（AKShare）",
            "catalog_source": "东方财富公开行业/概念目录（AKShare）",
            "verification": "public_board_member",
        },
    )

    analysis = asyncio.run(industry_chain.collect_analysis("光模块"))

    assert analysis["coverage"]["verified_member_count"] == 1
    assert analysis["coverage"]["membership_verification"] == "public_board_member"
    assert analysis["company_mapping"][0]["name"] == "甲光电"
    assert analysis["company_evidence_refresh"]["status"] == "disabled"


def test_governed_chain_stream_accepts_v2_and_model_only_adds_explanation() -> None:
    analysis = _sample_analysis()

    class FakeEngine:
        def __init__(self):
            self.explanation_calls = 0

        async def industry_chain_analysis(self, industry: str) -> dict:
            assert industry == "机器人"
            return analysis

        async def governed_chain_explanation_stream(self, message, frozen, *, history=None):
            self.explanation_calls += 1
            assert frozen["company_mapping"] == analysis["company_mapping"]
            assert message == "重点看卡点"
            assert history == [{"role": "user", "content": "上一问"}]
            yield "\n".join([
                "### 1. 需求本质",
                "产业常识·待公告核验：需求来自自动化升级。",
                "### 2. 产业地图",
                "产业常识·待公告核验：分为零部件、整机和应用。",
                "### 3. 价值捕获",
                "产业常识·待公告核验：取决于议价与工艺。",
                "### 4. 卡点",
                "产业常识·待公告核验：认证与良率需核验。",
                "### 5. 国产替代",
                "产业常识·待公告核验：替代空间需公告核验。",
                "### 6. 公司映射",
                "只使用冻结候选池。",
                "### 7. 兑现与估值",
                "产业常识·待公告核验：先核收入再谈估值。",
                "### 8. 研究优先级与失效条件",
                "产业常识·待公告核验：按产业关键性建议核验。",
                "数据截至/来源覆盖/证据缺口：见冻结底座。",
                "产业结论：待核验；当前交易时点：未评估。",
            ])

    class FakeState:
        engine = FakeEngine()

    async def collect():
        return [
            chunk
            async for chunk in _governed_chain_chat_stream(
                FakeState(),
                "机器人",
                "重点看卡点",
                history=[{"role": "user", "content": "上一问"}],
                model_available=True,
                research_contract="chain-research-v2",
            )
        ]

    chunks = asyncio.run(collect())
    assert analysis["request_contract"] == "chain-research-v2"
    assert "### 冻结证据底座" in chunks[0]
    assert "八段定性研究（公开产业常识推演）" in "".join(chunks)
    assert "### 1. 需求本质" in "".join(chunks)
    assert FakeState.engine.explanation_calls == 1


def _valid_model_research() -> str:
    return "\n".join([
        "### 1. 需求本质",
        "[public_knowledge / 待公告核验] 需求来自自动化升级。",
        "### 2. 产业地图",
        "[public_knowledge / 待公告核验] 分为零部件、整机和应用。",
        "### 3. 价值捕获",
        "[public_knowledge / 待公告核验] 取决于议价与工艺。",
        "### 4. 卡点",
        "[public_knowledge / 待公告核验] 认证与良率需核验。",
        "### 5. 国产替代",
        "[public_knowledge / 待公告核验] 替代进度需公告核验。",
        "### 6. 公司映射",
        "000001 仅为冻结候选；业务角色待公告核验。",
        "### 7. 兑现与估值",
        "[public_knowledge / 待公告核验] 先核收入再谈估值。",
        "### 8. 研究优先级与失效条件",
        "[public_knowledge / 待公告核验] 按产业关键性建议核验，不按成交额排序。",
        "数据截至/来源覆盖/证据缺口：见冻结底座。",
        "产业结论：待核验；当前交易时点：未评估。",
    ])


def test_model_receives_code_sorted_pool_without_data_researchability_rank() -> None:
    analysis = _sample_analysis()

    class FakeStore:
        @staticmethod
        def knowledge_digest():
            return ""

    class FakeHermes:
        messages = None

        async def chat_stream(self, messages, **_kwargs):
            self.messages = messages
            yield _valid_model_research()

    engine = Engine.__new__(Engine)
    engine.store = FakeStore()
    engine.hermes = FakeHermes()

    async def collect():
        return [
            chunk
            async for chunk in engine.governed_chain_explanation_stream(
                "完整研究",
                analysis,
            )
        ]

    chunks = asyncio.run(collect())
    assert chunks == [_valid_model_research()]
    system = engine.hermes.messages[0]["content"]
    assert "candidate_pool_order" in system
    assert "data_researchability_score" not in system
    assert system.index("000001") < system.index("000002")


def test_model_explanation_fails_closed_on_candidate_pool_escape() -> None:
    analysis = _sample_analysis()

    class FakeStore:
        @staticmethod
        def knowledge_digest():
            return ""

    class FakeHermes:
        async def chat_stream(self, _messages, **_kwargs):
            yield _valid_model_research().replace("000001", "600999")

    engine = Engine.__new__(Engine)
    engine.store = FakeStore()
    engine.hermes = FakeHermes()

    async def collect():
        return [
            chunk
            async for chunk in engine.governed_chain_explanation_stream(
                "完整研究",
                analysis,
            )
        ]

    assert asyncio.run(collect()) == []
