from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FEATURES = ROOT / "frontend/js/views/features.js"


def _function_block(source: str, name: str, next_name: str | None) -> str:
    start = source.index(f"async function {name}()")
    end = source.index(f"async function {next_name}()", start) if next_name else len(source)
    return source[start:end]


def test_feature_modules_separate_transport_failure_from_empty_samples() -> None:
    source = FEATURES.read_text(encoding="utf-8")

    assert "async function fetchFeature(path)" in source
    assert "transportFailed: true" in source
    assert "后端请求未成功，请检查服务连接后重试" in source
    assert "本模块超过交互时间预算，其他模块仍可使用" in source
    assert "await loadMoneyEffect();" in source
    assert "Promise.allSettled([loadCrowdedness(), loadMainline(), loadLeaders()])" in source
    assert "if (!d)" not in source
    assert "点击顶栏刷新重试" not in source

    cases = (
        ("loadMoneyEffect", "loadCrowdedness", "/features/money-effect", "赚钱效应", "赚钱效应本次未计算"),
        ("loadCrowdedness", "loadMainline", "/features/crowdedness", "交易拥挤度", "交易拥挤度本次未计算"),
        ("loadMainline", "loadLeaders", "/features/mainline", "主线数据", "今日榜单本次未形成"),
        ("loadLeaders", None, "/features/leaders", "龙头数据", "龙头梯队本次未形成"),
    )
    for name, next_name, path, failure_label, empty_label in cases:
        block = _function_block(source, name, next_name)
        assert f"await fetchFeature('{path}')" in block
        failure = f"renderFeatureTransportFailure(body, '{failure_label}', error)"
        assert failure in block
        assert empty_label in block
        assert block.index(failure) < block.index(empty_label)
