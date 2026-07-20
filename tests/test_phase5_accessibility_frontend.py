from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "frontend" / "js" / "views"
RISK_PALETTE = ("#1d4ed8", "#0f766e", "#a16207", "#c2410c", "#be123c")


def _source(name: str) -> str:
    return (VIEWS / name).read_text(encoding="utf-8")


def _palette(source: str, variable: str) -> tuple[str, ...]:
    match = re.search(rf"const {variable} = \{{([^}}]+)\}};", source)
    assert match, f"missing {variable}"
    return tuple(re.findall(r"#[0-9a-fA-F]{6}", match.group(1)))


def _relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _white_contrast(color: str) -> float:
    return 1.05 / (_relative_luminance(color) + 0.05)


def test_risk_palette_is_consistent_and_wcag_aa_across_decision_pages() -> None:
    cockpit = _palette(_source("cockpit.js"), "RISK_COLORS")
    morning = _palette(_source("morning.js"), "LV_COLOR")
    tail = _palette(_source("tail.js"), "LV_COLOR")

    assert cockpit == morning == tail == RISK_PALETTE
    assert all(_white_contrast(color) >= 4.5 for color in RISK_PALETTE)


def test_tail_grade_badges_also_have_readable_white_text() -> None:
    colors = _palette(_source("tail.js"), "GRADE_COLOR")

    assert len(colors) == 5
    assert all(_white_contrast(color) >= 4.5 for color in colors)


def test_cockpit_emotion_phase_text_uses_readable_deep_colors() -> None:
    colors = _palette(_source("cockpit.js"), "PHASE_COLORS")

    assert len(colors) == 6
    assert all(_white_contrast(color) >= 4.5 for color in colors)


def test_terminal_empty_states_do_not_keep_loading_minimum_heights() -> None:
    contracts = {
        "reports.js": ("360px", 'id="report-main"'),
        "archive.js": ("400px", 'id="ar-detail"'),
        "forecast.js": ("420px", 'id="fc-main"'),
    }

    for filename, (height, host_id) in contracts.items():
        source = _source(filename)
        assert f'style="min-height:{height}"' not in source
        assert f"style.minHeight = '{height}'" in source
        assert "style.removeProperty('min-height')" in source
        assert host_id in source
