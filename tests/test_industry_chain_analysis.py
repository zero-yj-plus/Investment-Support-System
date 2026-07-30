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
        "### 1. 核心需求与驱动力",
        "### 2. 上中下游全景",
        "### 3. 利润池与价值分配",
        "### 4. 核心卡点与竞争壁垒",
        "### 5. 国产替代路径",
        "### 6. A股映射思路",
        "### 7. 兑现节奏与观察指标",
        "### 8. 风险与失效条件",
    ]
    positions = [rendered.index(title) for title in expected]
    assert positions == sorted(positions)
    assert "## 产业结论" in rendered
    assert "|上游|" in rendered
    assert "|中游|" in rendered
    assert "|下游|" in rendered
    assert "公司业务、订单、财务和交易时点需另行研究" in rendered
    assert "数据可研分" not in rendered
    assert "页末证据与缺口复核" not in rendered


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
    assert analysis["company_mapping"][0]["quote"]["price"] is None
    assert analysis["data_as_of"]["sector_market"] is None
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
    rendered = "".join(chunks)
    assert chunks[0].startswith("# 产业链上下游研究")
    assert "### 1. 需求本质" in rendered
    assert "## 可核验证据附录" not in rendered
    assert "研究说明：本报告以产业结构、上下游传导和商业逻辑为主" in rendered
    assert FakeState.engine.explanation_calls == 1


def test_governed_chain_stream_still_analyzes_industry_with_an_empty_candidate_pool() -> None:
    analysis = industry_chain.build_analysis(
        "未匹配产业",
        catalog={},
        membership={},
        quotes={},
        generated_at="2026-07-30 10:00:00",
    )

    class FakeEngine:
        explanation_calls = 0

        async def industry_chain_analysis(self, _industry: str) -> dict:
            return analysis

        async def governed_chain_explanation_stream(self, *_args, **_kwargs):
            self.explanation_calls += 1
            yield "行业级分析正文；公司映射保持为空。"

    class FakeState:
        engine = FakeEngine()

    async def collect():
        return [
            chunk
            async for chunk in _governed_chain_chat_stream(
                FakeState(),
                "未匹配产业",
                "生成完整研究",
                model_available=True,
                research_contract="chain-research-v2",
            )
        ]

    rendered = "".join(asyncio.run(collect()))
    assert FakeState.engine.explanation_calls == 1
    assert "行业级分析正文" in rendered
    assert "## 可核验证据附录" not in rendered
    assert "产业结构、上下游传导和商业逻辑" in rendered


def _valid_model_research() -> str:
    return "\n".join([
        "## 产业结论",
        "产业需求、利润池与卡点应沿同一条传导链理解。",
        "### 1. 核心需求与驱动力",
        "需求来自自动化升级与效率改善。",
        "### 2. 上中下游全景",
        "|环节|主要参与者类型|核心任务|价值来源|向下游传导|",
        "|---|---|---|---|---|",
        "|上游|零部件|提供关键输入|稀缺性|决定整机性能|",
        "|中游|整机厂|系统集成|工艺能力|形成产品|",
        "|下游|应用方|场景落地|降本增效|形成订单|",
        "### 3. 利润池与价值分配",
        "利润倾向掌握核心工艺和议价权的环节。",
        "### 4. 核心卡点与竞争壁垒",
        "认证、良率与规模交付构成壁垒。",
        "### 5. 国产替代路径",
        "替代从非核心部件逐步走向核心环节。",
        "### 6. A股映射思路",
        "本段由服务端写入候选研究入口。",
        "### 7. 兑现节奏与观察指标",
        "依次观察产品、客户、交付和商业化。",
        "### 8. 风险与失效条件",
        "需求放缓、技术替代或竞争恶化会使逻辑失效。",
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
    assert len(chunks) == 1
    assert "## 产业结论" in chunks[0]
    assert "### 6. A股映射思路" in chunks[0]
    assert "甲公司（000001）" in chunks[0]
    assert "后续公司研究入口" in chunks[0]
    system = engine.hermes.messages[0]["content"]
    assert "candidate_pool_order" in system
    assert "data_researchability_score" not in system
    assert system.index("000001") < system.index("000002")


def test_model_company_section_is_replaced_by_frozen_candidate_pool() -> None:
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

    rendered = "".join(asyncio.run(collect()))
    assert "600999" not in rendered
    assert "甲公司（000001）" in rendered


def test_model_keeps_full_industry_research_when_candidate_pool_is_empty() -> None:
    analysis = industry_chain.build_analysis(
        "未匹配产业",
        catalog={},
        membership={},
        quotes={},
        generated_at="2026-07-30 10:00:00",
    )

    class FakeStore:
        @staticmethod
        def knowledge_digest():
            return ""

    class FakeHermes:
        async def chat_stream(self, _messages, **_kwargs):
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

    rendered = "".join(asyncio.run(collect()))
    assert "### 1. 核心需求与驱动力" in rendered
    assert "本轮没有可靠的本地候选名称，因此不列公司名单" in rendered
    assert "000001" not in rendered


def test_model_unverified_business_numbers_are_qualitized_without_visible_placeholders() -> None:
    analysis = _sample_analysis()
    model_text = _valid_model_research().replace(
        "利润倾向掌握核心工艺和议价权的环节。",
        "800G 是产品代际名称。\n毛利率可能达到50%-70%，认证需要12-18个月。",
    )

    class FakeStore:
        @staticmethod
        def knowledge_digest():
            return ""

    class FakeHermes:
        async def chat_stream(self, _messages, **_kwargs):
            yield model_text

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

    rendered = "".join(asyncio.run(collect()))
    assert "800G" in rendered
    assert "50%-70%" not in rendered
    assert "12-18个月" not in rendered
    assert "【具体比例待财报/公告核验】" not in rendered
    assert "【具体周期待公告/客户验证材料核验】" not in rendered
    assert "依据边界" not in rendered
    assert "毛利率水平受技术壁垒与竞争格局影响" in rendered
    assert "认证周期较长" in rendered
