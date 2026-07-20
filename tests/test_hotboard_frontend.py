from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend" / "js" / "views" / "hotboard.js"
APP = ROOT / "frontend" / "js" / "app.js"


def test_hotboard_keeps_theme_fields_separate_from_main_limitup_contract() -> None:
    source = VIEW.read_text(encoding="utf-8")

    assert "已验证公开数据链路" in source
    assert "api.get('/market/limitup/themes'" in source
    for label in ["今日热门题材", "几天几板", "板型", "封板率", "首封", "封单额"]:
        assert label in source
    assert "不与驾驶舱主涨停池" in source
    assert "#/research?" in source


def test_hotboard_is_lazy_loaded_as_a_visible_today_module() -> None:
    source = APP.read_text(encoding="utf-8")

    assert "hotboard: lazyView(() => import('./views/hotboard.js'), '涨停题材')" in source
    assert "import * as hotboard" not in source
