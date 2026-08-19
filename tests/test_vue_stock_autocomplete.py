from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(relative: str) -> str:
    return (VUE / relative).read_text(encoding="utf-8")


def test_shared_stock_autocomplete_is_connected_to_all_confirmed_single_stock_entries() -> None:
    targets = (
        "src/components/StockAskConversation.vue",
        "src/components/DiagnosisSearch.vue",
        "src/components/CompanyResearchForm.vue",
        "src/components/HoldingEditor.vue",
        "src/components/WatchlistEditor.vue",
        "src/components/SimulationActionDialog.vue",
        "src/components/TimingStockLookup.vue",
        "src/components/TechnicalScreenerGovernance.vue",
    )
    for target in targets:
        source = _read(target)
        assert "import StockAutocomplete from './StockAutocomplete.vue'" in source
        assert "<StockAutocomplete" in source

    assert "StockAutocomplete" not in _read("src/components/EtfWatchPage.vue")
    assert "StockAutocomplete" not in _read("src/components/ForecastMiningTable.vue")


def test_stock_autocomplete_requires_explicit_candidate_selection_but_keeps_six_digit_submit() -> None:
    source = _read("src/components/StockAutocomplete.vue")

    assert "activeIndex.value = -1" in source
    assert "activeIndex.value >= 0" in source
    assert "if (!/^\\d{6}$/.test(query.value))" in source
    assert "中文或拼音输入必须先选择候选股票" in source
    assert "model.value = committedCode" in source
    assert "emit('selected', item)" in source
    assert "results.slice(0, 8)" in source


def test_stock_autocomplete_has_combobox_keyboard_ime_and_lifecycle_contracts() -> None:
    source = _read("src/components/StockAutocomplete.vue")

    for contract in (
        'role="combobox"',
        'aria-autocomplete="list"',
        'role="listbox"',
        'role="option"',
        ':aria-activedescendant=',
        "event.key === 'ArrowDown'",
        "event.key === 'ArrowUp'",
        "event.key === 'Escape'",
        '@compositionstart="onCompositionStart"',
        '@compositionend="onCompositionEnd"',
        "new AbortController()",
        "setTimeout(() => void search(term), 160)",
        "onBeforeUnmount",
        "controller?.abort()",
        "aria-live=\"polite\"",
    ):
        assert contract in source
    assert 'inputmode="search"' in source
    assert "min-height: 44px" in source
    assert "z-index: 40" in source
    assert "@media (max-width: 560px)" in source
    assert "v-html" not in source


def test_stock_autocomplete_uses_only_cache_search_and_surfaces_degraded_truth() -> None:
    source = _read("src/components/StockAutocomplete.vue")

    assert "`/stocks/search?q=${encodeURIComponent(term)}&limit=8`" in source
    for unrelated in ("/quote/", "/stock/", "/diagnose", "/market/"):
        assert unrelated not in source
    assert "待刷新；候选仅供选择，提交仍会真实核验" in source
    assert "本地股票名录暂不可用；仍可输入完整六位代码继续核验" in source
    assert "<strong>{{ item.name }}</strong><span class=\"mono\">{{ item.code }}</span>" in source


def test_stock_autocomplete_has_focused_mobile_and_desktop_audit() -> None:
    package = _read("package.json")
    audit = _read("scripts/audit-stock-autocomplete.mjs")

    assert '"audit:stock-autocomplete": "node scripts/audit-stock-autocomplete.mjs"' in package
    for contract in (
        "phone-375",
        "wide-1440",
        "enterDoesNotAutoPickFirst",
        "keyboardSelectionWritesCode",
        "staleBoundaryVisible",
        "touchTargets",
        "noHorizontalOverflow",
        "window.axe.run",
        "reducedMotion: 'reduce'",
    ):
        assert contract in audit
