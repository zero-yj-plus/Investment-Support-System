from __future__ import annotations

from hermes_trader.report_push_summary import (
    REPORT_PUSH_SUMMARY_MAX_BYTES,
    build_report_public_url,
    build_report_push_summary,
    normalize_public_base_url,
)


def test_morning_push_summary_keeps_decision_boundaries_not_full_evidence() -> None:
    summary = build_report_push_summary({
        "rtype": "morning",
        "title": "早盘内参",
        "content_hash": "abcdef1234567890",
        "content_md": """
# 早盘内参
## 一、今日行动（小白先看这段就够）
谨慎参与，总仓位不超过三成。
- 风险：高位退潮。
- 不做：连续异动高位票。
### 核心候选
- 000001 仅在放量确认后观察。
## 二、昨夜美股对 A 股影响
这是一大段只供完整报告查看的外盘证据。
## 七、持仓处理
现有持仓以观察和降低波动暴露为主。
## 十、验证条件
若量能继续收缩则再降一档。
## 十一、数据健康提示
北向口径缺失，相关判断降级。
""",
    })

    assert "谨慎参与，总仓位不超过三成" in summary
    assert "000001 仅在放量确认后观察" in summary
    assert "现有持仓以观察" in summary
    assert "若量能继续收缩" in summary
    assert "北向口径缺失" in summary
    assert "外盘证据" not in summary
    assert "内容版本 v1 · abcdef123456" in summary


def test_evening_push_summary_preserves_all_customer_decision_categories() -> None:
    content = """# 次日策略
## 一、明日基调
谨慎，优先防守。
## 二、明日风险等级预判
偏高：高位股存在补跌风险。
## 三、明日仓位上限
不超过三成。
## 四、明日重点方向
只观察低位放量方向。
## 五、不能碰方向
连续异动高位票。
## 六、确定性策略（最多 3 个）
候选 A 仅在放量确认后观察，跌破支撑则放弃。
## 七、高盈亏比策略（最多 3 个）
这段不是飞书摘要的优先内容。
## 十一、三种剧本（强于预期 / 符合预期 / 弱于预期）
- 强：到仓位上限，不追高。
- 中：按计划观察。
- 弱：停止新增风险。
"""
    summary = build_report_push_summary({"rtype": "evening", "content_md": content})

    for expected in (
        "谨慎，优先防守",
        "高位股存在补跌风险",
        "不超过三成",
        "只观察低位放量方向",
        "连续异动高位票",
        "跌破支撑则放弃",
        "停止新增风险",
    ):
        assert expected in summary
    assert "不是飞书摘要的优先内容" not in summary


def test_trend_summary_keeps_at_most_three_candidate_sections() -> None:
    content = """# 每日趋势票
## 1. 甲公司（600001）
- 量价：温和放量。
## 2. 乙公司（000002）
- 风险：仅观察。
## 3. 丙公司（300003）
- 证伪：跌破支撑。
## 4. 丁公司（600004）
- 不应进入客户摘要。
"""
    summary = build_report_push_summary({"rtype": "trend", "content_md": content})

    assert "甲公司" in summary and "乙公司" in summary and "丙公司" in summary
    assert "丁公司" not in summary


def test_unstructured_legacy_report_uses_bounded_excerpt_not_full_body() -> None:
    marker = "FULL_REPORT_TAIL_MUST_NOT_BE_PUSHED"
    summary = build_report_push_summary({
        "rtype": "morning",
        "content_md": "中" * 10000 + marker,
    })

    assert marker not in summary
    assert "报告关键节选" in summary
    assert "本节其余内容请在完整报告中查看" in summary
    assert len(summary.encode("utf-8")) <= REPORT_PUSH_SUMMARY_MAX_BYTES


def test_report_public_url_uses_explicit_base_and_canonical_route() -> None:
    assert normalize_public_base_url("http://111.231.173.220/") == "http://111.231.173.220"
    assert build_report_public_url(
        {"rtype": "morning"}, "http://111.231.173.220/",
    ) == "http://111.231.173.220/#/premarket?tab=report"
    assert build_report_public_url({"rtype": "midday"}, "http://111.231.173.220") == ""
    assert build_report_public_url({"rtype": "morning"}, "") == ""
