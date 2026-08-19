"""Cache-only A-share symbol search with local pinyin derivatives.

The canonical identity remains ``free_source.cached_stock_universe``.  This
module only derives searchable keys from that snapshot; selecting a candidate
does not prove that a quote is currently available and must not bypass the
existing quote/identity checks on business endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata

from . import free_source

try:  # Deployment requirements install this; the fallback keeps code/name usable.
    from pypinyin import Style, lazy_pinyin

    _PINYIN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised through the explicit state contract
    Style = None
    lazy_pinyin = None
    _PINYIN_AVAILABLE = False


_TEMPORARY_NAME_PREFIX_RE = re.compile(
    r"^(?:S\*ST|\*ST|SST|ST|XD|XR|DR|N|C)",
    re.IGNORECASE,
)
_MAX_QUERY_LENGTH = 64
_MAX_RESULTS = 8


@dataclass(frozen=True, slots=True)
class _SearchEntry:
    code: str
    name: str
    normalized_names: tuple[str, ...]
    full_pinyin: tuple[str, ...]
    initials: tuple[str, ...]


def _search_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in text if char.isalnum())


def _base_name(name: str) -> str:
    current = str(name or "").strip()
    while current:
        stripped = _TEMPORARY_NAME_PREFIX_RE.sub("", current, count=1).strip()
        if stripped == current:
            break
        current = stripped
    return current


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _pinyin_keys(name: str) -> tuple[str, str]:
    if not _PINYIN_AVAILABLE or lazy_pinyin is None or Style is None:
        return "", ""
    full = "".join(
        lazy_pinyin(name, style=Style.NORMAL, errors="ignore", strict=False)
    ).casefold()
    first = "".join(
        lazy_pinyin(name, style=Style.FIRST_LETTER, errors="ignore", strict=False)
    ).casefold()
    return _search_key(full), _search_key(first)


@lru_cache(maxsize=4)
def _build_index(items: tuple[tuple[str, str], ...], pinyin_available: bool) -> tuple[_SearchEntry, ...]:
    entries: list[_SearchEntry] = []
    for raw_code, raw_name in items:
        code = str(raw_code or "").strip().zfill(6)
        name = str(raw_name or "").strip()
        if not re.fullmatch(r"\d{6}", code) or not name:
            continue
        base_name = _base_name(name) or name
        name_variants = _unique([_search_key(name), _search_key(base_name)])
        full_values: list[str] = []
        initial_values: list[str] = []
        if pinyin_available:
            for variant in dict.fromkeys((name, base_name)):
                full, first = _pinyin_keys(variant)
                full_values.append(full)
                initial_values.append(first)
        entries.append(_SearchEntry(
            code=code,
            name=name,
            normalized_names=name_variants,
            full_pinyin=_unique(full_values),
            initials=_unique(initial_values),
        ))
    return tuple(entries)


def _match_rank(entry: _SearchEntry, query: str) -> int | None:
    if query == entry.code:
        return 0
    if query in entry.normalized_names:
        return 1
    if entry.code.startswith(query):
        return 2
    if any(value.startswith(query) for value in entry.normalized_names):
        return 3
    if any(query in value for value in entry.normalized_names):
        return 4
    if any(value.startswith(query) for value in entry.full_pinyin):
        return 5
    if any(query in value for value in entry.full_pinyin):
        return 6
    if query in entry.initials:
        return 7
    # Users commonly omit a geographic/company prefix (for example ``gzmt``
    # -> ``mt``), so an initials suffix is more useful than a generic prefix.
    if any(value.endswith(query) for value in entry.initials):
        return 8
    if any(value.startswith(query) for value in entry.initials):
        return 9
    if any(query in value for value in entry.initials):
        return 10
    return None


def search_stock_catalog(query: str, *, limit: int = _MAX_RESULTS) -> dict:
    """Search the local canonical snapshot without refreshing any provider."""
    raw_query = str(query or "").strip()[:_MAX_QUERY_LENGTH]
    normalized_query = _search_key(raw_query)
    requested_limit = min(_MAX_RESULTS, max(1, int(limit or _MAX_RESULTS)))
    names = free_source.cached_stock_universe()
    status = free_source.stock_universe_status()
    items = tuple(sorted(
        (str(code), str(name))
        for code, name in (names or {}).items()
        if str(code).strip() and str(name).strip()
    ))
    index = _build_index(items, _PINYIN_AVAILABLE)

    ranked: list[tuple[int, str, _SearchEntry]] = []
    if normalized_query:
        for entry in index:
            rank = _match_rank(entry, normalized_query)
            if rank is None:
                continue
            ranked.append((rank, entry.code, entry))
    ranked.sort(key=lambda row: row[:2])
    results = [
        {"code": row[2].code, "name": row[2].name}
        for row in ranked[:requested_limit]
    ]

    authoritative = free_source.stock_universe_is_authoritative(status)
    stale = bool(status.get("stale"))
    refresh_required = bool(status.get("refresh_required"))
    missing: list[str] = []
    if not index:
        missing.append("本地全 A 股股票名录不可用")
    elif stale:
        missing.append("股票名录为陈旧快照，候选仅供选择")
    elif not authoritative or refresh_required:
        missing.append("股票名录尚未达到权威完整状态，候选仅供选择")
    if not _PINYIN_AVAILABLE:
        missing.append("拼音转换库未安装，仅支持代码和中文名称")

    state = "unavailable" if not index else "ready" if authoritative else "degraded"
    return {
        "ok": True,
        "state": state,
        "query": raw_query,
        "results": results,
        "pinyin_available": _PINYIN_AVAILABLE,
        "universe": {
            "count": len(index),
            "complete": authoritative,
            "stale": stale,
            "refresh_required": refresh_required,
            "as_of": status.get("as_of"),
            "source": status.get("source"),
            "authority": status.get("authority"),
        },
        "missing": missing,
    }
