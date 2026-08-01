from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "frontend-vue" / "src" / "components"
STYLES = ROOT / "frontend-vue" / "src" / "styles.css"


def test_page_bottom_data_stamps_share_compact_visual_treatment() -> None:
    pages = (
        "CapitalTrackingPage.vue",
        "CompanyResearchPage.vue",
        "DiagnosisPage.vue",
        "ForecastMiningPage.vue",
        "HoldingsPage.vue",
        "HotboardPage.vue",
        "MainlineResearchPage.vue",
        "ScreenerPage.vue",
        "SentimentEventsPage.vue",
        "StockAskPage.vue",
        "TechnicalScreenerPage.vue",
        "TimingPage.vue",
        "WatchlistPage.vue",
    )

    for filename in pages:
        source = (COMPONENTS / filename).read_text(encoding="utf-8")
        assert "data-footnote-stamp" in source, filename

    styles = STYLES.read_text(encoding="utf-8")
    assert "#app .data-footnote-stamp" in styles
    assert "width: min(100%, 460px);" in styles
    assert "justify-self: end;" in styles
    assert "font-size: 12px;" in styles
    assert "#app .data-footnote-stamp .data-status" in styles
    assert "#app .data-footnote-stamp { width: 100%; justify-self: stretch; }" in styles
