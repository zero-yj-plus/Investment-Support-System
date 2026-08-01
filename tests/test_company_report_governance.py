from hermes_trader.analysis.engine import (
    _company_report_evidence_appendix,
    _sanitize_company_report,
)


def test_closed_market_company_report_rebuilds_trade_section_and_removes_inference():
    raw = """# 贵州茅台研究

五、估值评析
当前估值处于历史相对低位。
现金流充裕（具体CF数据未接入）。
股份回购是公司认为股价被低估的信号，对股价有托底作用。
历史上MA60多次成为反弹终止点。
机构目标价较现价有37.7%的上行空间。

八、交易节奏（前台只认价格+动作）
试仓条件：1204.11 元附近介入。
止损位：1200 元。

九、风险提示
数据有缺口。
"""
    context = {
        "确定性技术分析(唯一价位依据)": {
            "levels": {
                "support": 1204.11,
                "support_basis": "MA20",
                "resistance": 1265.39,
                "resistance_basis": "MA60",
            }
        }
    }

    result = _sanitize_company_report(raw, context, market_closed=True)

    assert "试仓条件" not in result
    assert "## 八、交易节奏（确定性复核）" in result
    assert "1200 元" not in result
    assert "上方观察位：1265.39 元（MA60）" in result
    assert "下方观察位：1204.11 元（MA20）" in result
    assert "无法判断历史分位" in result
    assert "现金流明细未接入" in result
    assert "不推断管理层估值判断或股价影响" in result
    assert "无法判断该位置以往表现" in result
    assert "不输出二次推导数字" in result


def test_company_report_appendix_is_structured_and_preserves_missing_semantics():
    context = {
        "个股代码": "000001",
        "个股数据": {
            "quote": {
                "code": "000001",
                "name": "平安银行",
                "price": 12.34,
                "change_pct": 1.2,
                "amount": 609_000_000,
                "total_mv": 220_000_000_000,
                "pe": 6.8,
                "pb": None,
                "source": "腾讯财经",
                "as_of": "2026-07-31 14:30:00",
            },
            "board": {"industry": "银行"},
        },
        "基本面财务(近一期·TDX主/新浪备)": {
            "end_date": "20260331",
            "roe": 3.2,
            "grossprofit_margin": None,
            "or_yoy": 4.5,
            "netprofit_yoy": 2.1,
            "debt_to_assets": 91.0,
            "source": "TDX",
        },
        "确定性技术分析(唯一价位依据)": {
            "dimensions": [{
                "id": "trend", "label": "趋势结构", "status": "complete",
                "score": 2, "summary": "现价站上MA20", "source": "前复权日K",
            }],
            "levels": {"support": 11.8, "support_basis": "MA20"},
        },
        "官方公告证据(近90日·不参与日内评分)": {
            "state": "complete",
            "verification_state": "cross_confirmed",
            "data_as_of": "2026-07-30",
            "items": [{
                "announcement_date": "2026-07-28",
                "title": "2026年半年度业绩快报",
                "verification": "cross_confirmed",
            }],
        },
        "交易所互动问答(近90日·Tier3·不参与日内评分)": {
            "state": "complete_empty", "data_as_of": "2026-07-30", "items": [],
        },
    }

    rendered = _company_report_evidence_appendix(context)

    assert "## 附录：结构化公司数据与证据" in rendered
    assert "6.09亿元" in rendered
    assert "2200.00亿元" in rendered
    assert "|6.80|—|" in rendered
    assert "趋势结构" in rendered
    assert "2026年半年度业绩快报" in rendered
    assert "不补成 0" in rendered
