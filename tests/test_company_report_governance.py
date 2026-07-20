from hermes_trader.analysis.engine import _sanitize_company_report


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
    assert "1200 元" not in result
    assert "上方观察位：1265.39 元（MA60）" in result
    assert "下方观察位：1204.11 元（MA20）" in result
    assert "无法判断历史分位" in result
    assert "现金流明细未接入" in result
    assert "不推断管理层估值判断或股价影响" in result
    assert "无法判断该位置以往表现" in result
    assert "不输出二次推导数字" in result
