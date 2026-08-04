"""消息面真实数据：财联社电报、东财全球快讯、隔夜美股。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import threading

from . import tencent_source as tc
from . import cls_news_source as cls_news
from . import gdelt_source as gdelt
from . import tdx_api_source as tdx_api
from .base import get_ak, now_cn, safe, to_float, ts_str, ttl_cache
from .evidence_contract import compute_content_hash, normalize_source_url

# 港股指数：腾讯符号 → 展示名（恒生/国企/恒生科技），用于腾讯优先取数。
_HK_INDEX_TC = (("hkHSI", "恒生指数"), ("hkHSCEI", "国企指数"), ("hkHSTECH", "恒生科技"))
# 美股知名科技股：腾讯符号 → 展示名。腾讯 us<TICKER> 海外 CDN 可达，补客户机东财死。
_US_TECH_TC = (
    ("usAAPL", "苹果"), ("usMSFT", "微软"), ("usNVDA", "英伟达"),
    ("usGOOGL", "谷歌-A"), ("usAMZN", "亚马逊"), ("usMETA", "Meta"),
    ("usTSLA", "特斯拉"),
)
_US_INDEX_TC = (("usDJI", "道琼斯"), ("usINX", "标普500"), ("usIXIC", "纳斯达克"))

# 资讯类源非关键，给较短超时——慢源(财联社/美股新浪/东财港股)在被封机器上原本各卡满
# 15s 且串行累加（最坏 ~100s），缩到 7s + 并行后整体上限 ~7-14s，不再拖死页面。
_NEWS_TIMEOUT = 7
# 国内快讯面向盘中事件流：前端 30 秒轮询，后端缓存必须同频，不能让页面
# 看似每 30 秒刷新、实际连续 3–5 分钟返回同一批旧数据。
NEWS_FEED_REFRESH_SECONDS = 30
_CLS_LAST_GOOD_PATH: str | None = None
_CN_LAST_GOOD_PATH: str | None = None
_A_SHARE_LAST_GOOD_PATH: str | None = None
_CN_LAST_GOOD_MAX_AGE = timedelta(hours=6)
_A_SHARE_LAST_GOOD_MAX_AGE = timedelta(hours=6)
_A_SHARE_SNAPSHOT_SCHEMA = "a-share-news-page-snapshot-v1"
_A_SHARE_REFRESH_LOCK = threading.Lock()
_A_SHARE_REFRESHING = False
log = logging.getLogger("hermes.news")


def init(data_dir: str | Path) -> None:
    """配置客户隔离的快讯 last-good 快照路径。"""
    global _CLS_LAST_GOOD_PATH, _CN_LAST_GOOD_PATH, _A_SHARE_LAST_GOOD_PATH  # noqa: PLW0603
    news_dir = Path(data_dir) / "news"
    _CLS_LAST_GOOD_PATH = str(news_dir / "cls-last-good.json")
    _CN_LAST_GOOD_PATH = str(news_dir / "cn-last-good.json")
    _A_SHARE_LAST_GOOD_PATH = str(news_dir / "a-share-page-last-good.json")


def _save_cn_last_good(payload: dict) -> None:
    """只保存非空聚合快讯；空响应绝不能覆盖最近成功数据。"""
    if not _CN_LAST_GOOD_PATH or not payload.get("items"):
        return
    path = Path(_CN_LAST_GOOD_PATH)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)
    finally:
        tmp.unlink(missing_ok=True)


def _load_cn_last_good() -> dict | None:
    if not _CN_LAST_GOOD_PATH:
        return None
    try:
        payload = json.loads(Path(_CN_LAST_GOOD_PATH).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            return None
        if not payload["items"]:
            return None
        stamp = datetime.fromisoformat(str(payload.get("fetched_at") or ""))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        age = now_cn().astimezone(stamp.tzinfo) - stamp
        if age < timedelta(0) or age > _CN_LAST_GOOD_MAX_AGE:
            return None
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _save_a_share_last_good(payload: dict, *, saved_at: str | None = None) -> str | None:
    """原子保存非空完整页面响应；失败或空结果不得覆盖最近成功快照。"""
    if not _A_SHARE_LAST_GOOD_PATH or payload.get("ok") is False or not payload.get("items"):
        return None
    stamp = saved_at or now_cn().isoformat(timespec="seconds")
    envelope = {
        "schema": _A_SHARE_SNAPSHOT_SCHEMA,
        "saved_at": stamp,
        "payload": payload,
    }
    path = Path(_A_SHARE_LAST_GOOD_PATH)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)
        return stamp
    finally:
        tmp.unlink(missing_ok=True)


def _load_a_share_last_good() -> tuple[dict, str, float] | None:
    """读取仍在六小时保护窗内的完整页面快照，并返回载荷、保存时点与年龄。"""
    if not _A_SHARE_LAST_GOOD_PATH:
        return None
    try:
        envelope = json.loads(Path(_A_SHARE_LAST_GOOD_PATH).read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or envelope.get("schema") != _A_SHARE_SNAPSHOT_SCHEMA:
            return None
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or payload.get("ok") is False:
            return None
        if not isinstance(payload.get("items"), list) or not payload["items"]:
            return None
        saved_at = str(envelope.get("saved_at") or "")
        stamp = datetime.fromisoformat(saved_at)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        age = (now_cn().astimezone(stamp.tzinfo) - stamp).total_seconds()
        if age < 0 or age > _A_SHARE_LAST_GOOD_MAX_AGE.total_seconds():
            return None
        return dict(payload), saved_at, age
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _with_a_share_snapshot(
    payload: dict, *, served_from_snapshot: bool, saved_at: str, age_seconds: float,
    refreshing: bool,
) -> dict:
    out = dict(payload)
    age = max(0, round(age_seconds))
    out["snapshot"] = {
        "served_from_snapshot": served_from_snapshot,
        "status": (
            "live" if not served_from_snapshot
            else ("fresh" if age <= NEWS_FEED_REFRESH_SECONDS else "stale")
        ),
        "saved_at": saved_at,
        "age_seconds": age,
        "refreshing": refreshing,
    }
    return out

# 新闻发布方、接入方式与证据层级是三个不同维度，不再用一个“来源”字段混在一起。
# tier_1 只表示客户授权数据通道；tier_2 表示公开采集端，不等于官方直连或独立信源。
_NEWS_SOURCE_META = {
    "tdx": {
        "source_id": "tdx_news_sentiment", "publisher_name": None,
        "collection_platform_name": "TDX 舆情电讯",
        "source_relation": "customer_authorized_feed", "access_path": "tdx_api",
        "evidence_level": "tier_1",
    },
    "em": {
        "source_id": "eastmoney_public_news", "publisher_name": None,
        "collection_platform_name": "东方财富快讯",
        "source_relation": "public_aggregator", "access_path": "akshare_adapter",
        "evidence_level": "tier_2",
    },
    "ths": {
        "source_id": "ths_public_news", "publisher_name": None,
        "collection_platform_name": "同花顺快讯",
        "source_relation": "public_aggregator", "access_path": "akshare_adapter",
        "evidence_level": "tier_2",
    },
    "futu": {
        "source_id": "futu_public_news", "publisher_name": None,
        "collection_platform_name": "富途快讯",
        "source_relation": "public_aggregator", "access_path": "akshare_adapter",
        "evidence_level": "tier_2",
    },
    "sina": {
        "source_id": "sina_public_news", "publisher_name": None,
        "collection_platform_name": "新浪财经快讯",
        "source_relation": "public_aggregator", "access_path": "akshare_adapter",
        "evidence_level": "tier_2",
    },
    "cls": {
        "source_id": "cls_original_telegraph", "publisher_name": "财联社",
        "collection_platform_name": "财联社官网电报",
        "source_relation": "original_publisher", "access_path": "official_website_internal_adapter",
        "evidence_level": "tier_2",
    },
}
_NEWS_EVIDENCE_RANK = {"tier_1": 1, "tier_2": 2, "tier_3": 3}


def _safe_source_url(value: object) -> str | None:
    try:
        return normalize_source_url(None if value is None else str(value))
    except ValueError:
        return None


def _parallel(thunks: list) -> list:
    """并发执行无参函数列表，按序返回结果；单个异常吞为 None（不影响其余）。"""
    with ThreadPoolExecutor(max_workers=min(8, len(thunks) or 1)) as ex:
        futs = [ex.submit(t) for t in thunks]
        out = []
        for f in futs:
            try:
                out.append(f.result())
            except Exception:
                out.append(None)
        return out


@ttl_cache(300)
def cn_news(limit: int = 40) -> dict:
    """国内财经快讯：东财公开 JSON 主源、财联社公开 JSON 备源，均不执行 V8。"""
    missing: list[str] = []
    out: dict = {"ok": True, "ts": ts_str(), "missing": missing, "items": []}
    ak = get_ak()
    if ak is None:
        missing.append("财经快讯（akshare 未安装）")
        return out
    df = safe(ak.stock_info_global_em, label="东财全球快讯", timeout=_NEWS_TIMEOUT)
    if df is not None and not df.empty:
        for _, r in df.head(limit).iterrows():
            out["items"].append(
                {
                    "source": "东方财富",
                    "title": str(r.get("标题", "") or "")[:120],
                    "content": str(r.get("摘要", "") or "")[:400],
                    "time": str(r.get("发布时间", "") or ""),
                }
            )
        return out
    # 兜底：当前 AkShare 版本的财联社接口为普通 JSON 请求；若未来实现重新引入 V8，
    # base.safe 的集中门禁会按函数名阻止并诚实返回空。
    df = safe(ak.stock_info_global_cls, symbol="全部", label="财联社电报", timeout=_NEWS_TIMEOUT)
    if df is not None and not df.empty:
        # akshare 内部按发布时间升序排序（oldest-first），直接 head(limit) 会取到最旧的 N 条。
        # 这里统一按 发布日期+发布时间 降序，再取最新 limit 条；列缺失时跳过排序，避免崩溃。
        sort_cols = [c for c in ("发布日期", "发布时间") if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols, ascending=False)
        for _, r in df.head(limit).iterrows():
            out["items"].append(
                {
                    "source": "财联社",
                    "title": str(r.get("标题", "") or "")[:120],
                    "content": str(r.get("内容", "") or "")[:400],
                    "time": f"{r.get('发布日期', '')} {r.get('发布时间', '')}".strip(),
                }
            )
        return out
    missing.append("财经快讯")
    return out


import re as _re  # noqa: E402


def _news_key(title: str, content: str) -> str:
    """归一去重键：标题(缺则正文)去空白标点、取前 16 字。同一事件多源措辞略异/东财【】包裹也能并到一起。
    16 字的中文标题前缀已足够特异（不同事件极难同前缀），又能容忍东财标题尾部拖正文的差异。"""
    s = (title or content or "")[:60]
    return _re.sub(r"[\s\W]+", "", s, flags=_re.UNICODE)[:16]


_NEWS_EVENT_ANCHORS = (
    "封锁", "制裁", "关税", "加息", "降息", "利率", "汇率", "收购", "并购", "重组",
    "停产", "复产", "涨价", "降价", "增持", "减持", "回购", "诉讼", "禁令", "补贴",
    "地震", "洪水", "山洪", "台风", "疫情", "火灾", "爆炸", "停火", "冲突",
)


def _news_norm(title: str, content: str) -> str:
    """新闻近似去重文本；只做格式归一，不改写原文。"""
    s = (title or content or "")[:120]
    s = _re.sub(r"^(?:市场消息|最新消息|快讯)[:：]?", "", s.strip())
    return _re.sub(r"[\s\W]+", "", s, flags=_re.UNICODE)


def _news_bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(max(0, len(text) - 1))}


def _news_times_close(left: str, right: str, *, minutes: int = 15) -> bool:
    """近似文本只在短时间窗内合并，避免把同一主题的后续进展吞掉。"""
    from datetime import datetime  # noqa: PLC0415

    try:
        a = datetime.fromisoformat(_news_time_key(left))
        b = datetime.fromisoformat(_news_time_key(right))
    except (TypeError, ValueError):
        return False
    return abs((a - b).total_seconds()) <= minutes * 60


def _same_news_event(left: dict, right: dict) -> bool:
    """保守合并多源近似标题：共享动作词、足够多双字片段且发布时间接近。"""
    if not _news_times_close(str(left.get("time") or ""), str(right.get("time") or "")):
        return False
    a = _news_norm(str(left.get("title") or ""), str(left.get("content") or ""))
    b = _news_norm(str(right.get("title") or ""), str(right.get("content") or ""))
    if min(len(a), len(b)) < 8:
        return False
    aa, bb = _news_bigrams(a), _news_bigrams(b)
    shared = aa & bb
    containment = len(shared) / max(1, min(len(aa), len(bb)))
    anchor_shared = any(word in a and word in b for word in _NEWS_EVENT_ANCHORS)
    return len(shared) >= 6 and (containment >= 0.55 or (anchor_shared and containment >= 0.40))


def _news_time_key(tm: str) -> str:
    """把各源不同格式的时间统一成可排序的 'YYYY-MM-DD HH:MM(:SS)'。只有时分秒的按今天补日期。"""
    tm = (tm or "").strip()
    m = _re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)", tm)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = _re.search(r"(\d{2}:\d{2}(?::\d{2})?)", tm)
    if m:
        return f"{now_cn().strftime('%Y-%m-%d')} {m.group(1)}"
    return tm


def _published_at(tm: str) -> str | None:
    """将供应方时间收敛为带时区的北京时间。

    旧客户端仍继续读 ``time``；新字段只在能解析时返回，不伪造时钟。
    """
    from datetime import datetime  # noqa: PLC0415

    normalized = _news_time_key(tm)
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now_cn().tzinfo)
    return parsed.isoformat(timespec="seconds")


def _has_explicit_news_date(tm: str) -> bool:
    return bool(_re.search(r"\d{4}-\d{2}-\d{2}", str(tm or "")))


def _source_url(row: object) -> str | None:
    """只保留无凭证的 HTTP(S) 原文地址；上游不给就明确为 None。"""
    getter = getattr(row, "get", None)
    if not callable(getter):
        return None
    for key in ("原文链接", "新闻链接", "链接", "网址", "url", "URL"):
        value = getter(key, None)
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() == "nan":
            continue
        cleaned = _safe_source_url(text)
        if cleaned:
            return cleaned
    return None


def _raw_news_item(
    tag: str,
    title: str,
    content: str,
    tm: str,
    *,
    fetched_at: str,
    source_url: str | None = None,
) -> dict:
    return {
        "tag": str(tag or "").strip(),
        "title": str(title or "").strip(),
        "content": str(content or "").strip(),
        "time": str(tm or "").strip(),
        "fetched_at": str(fetched_at or "").strip(),
        "source_url": _safe_source_url(source_url),
    }


def _news_observation(raw: dict) -> dict:
    tag = str(raw.get("tag") or "").strip()
    source = _NEWS_SOURCE_META.get(tag, {
        "source_id": tag or "unknown_news_source",
        "publisher_name": None,
        "collection_platform_name": tag or "未知采集端",
        "source_relation": "unknown",
        "access_path": "unknown",
        "evidence_level": "tier_3",
    })
    published_at = _published_at(str(raw.get("time") or ""))
    source_url = _safe_source_url(raw.get("source_url"))
    hash_payload = {
        "source_id": source["source_id"],
        "title": str(raw.get("title") or ""),
        "content": str(raw.get("content") or ""),
        "published_at": published_at,
    }
    degraded_reasons = []
    if published_at is None:
        degraded_reasons.append("published_at_unavailable")
    elif not _has_explicit_news_date(str(raw.get("time") or "")):
        degraded_reasons.append("published_date_inferred")
    if source_url is None:
        degraded_reasons.append("original_url_unavailable")
    return {
        **source,
        "published_at": published_at,
        "fetched_at": str(raw.get("fetched_at") or ""),
        "source_url": source_url,
        "content_hash": compute_content_hash(hash_payload),
        "degraded_reasons": degraded_reasons,
    }


def _event_evidence_level(observations: list[dict]) -> str:
    levels = [str(item.get("evidence_level") or "tier_3") for item in observations]
    return min(levels, key=lambda level: _NEWS_EVIDENCE_RANK.get(level, 99), default="tier_3")


@ttl_cache(NEWS_FEED_REFRESH_SECONDS)
def cn_news_aggregated(limit: int = 40) -> dict:
    """A股财经快讯聚合。TDX / 东财 / 同花顺 / 富途 / 新浪与
    财联社官网 v1 发布链并行；各采集端保留 observations，按事件去重合并。
    兼容字段 multi/confirms 只表示“采集端命中”；上游原始出处不明时，绝不宣称独立信源互证。
    绝不编造：全部照抄源文本；任一源失败/超时自吞不影响其余；全失败→items 空 + missing。"""
    fetched_at = now_cn().isoformat(timespec="seconds")
    out: dict = {
        "ok": True,
        "ts": ts_str(),
        "fetched_at": fetched_at,
        "missing": [],
        "items": [],
        "refresh_seconds": NEWS_FEED_REFRESH_SECONDS,
        "latest_event_time": None,
    }
    raw: list[dict] = []

    def _pull(fn_name, tcol, ccol, timecol, tag, head=80):
        found: list[dict] = []
        fn = getattr(ak, fn_name, None)
        if fn is None:
            return found
        df = safe(fn, label=f"快讯:{tag}", timeout=_NEWS_TIMEOUT)
        if df is None or df.empty:
            return found
        for _, r in df.head(head).iterrows():
            title = str(r.get(tcol, "") or "") if tcol else ""
            content = str(r.get(ccol, "") or "")
            tm = str(r.get(timecol, "") or "")
            found.append(_raw_news_item(
                tag, title, content, tm,
                fetched_at=fetched_at,
                source_url=_source_url(r),
            ))
        return found

    def _pull_tdx():
        feed = tdx_api.market_news(limit=max(80, min(int(limit) * 3, 200))) or {}
        source_fetched_at = _published_at(str(feed.get("ts") or "")) or fetched_at
        return [
            _raw_news_item(
                "tdx", str(item.get("title") or ""), str(item.get("content") or ""),
                str(item.get("time") or ""), fetched_at=source_fetched_at,
                source_url=_safe_source_url(item.get("source_url")),
            )
            for item in feed.get("items") or []
            if isinstance(item, dict)
        ]

    def _pull_cls():
        feed = cls_news.cached_cls_telegraph(
            max(80, min(int(limit) * 3, 100)),
            _CLS_LAST_GOOD_PATH,
        ) or {}
        return [
            _raw_news_item(
                "cls",
                str(item.get("title") or ""),
                str(item.get("content") or ""),
                str(item.get("published_at") or ""),
                fetched_at=str(feed.get("fetched_at") or fetched_at),
                source_url=_safe_source_url(item.get("source_url")),
            )
            for item in feed.get("items") or []
            if isinstance(item, dict)
        ]

    ak = get_ak()
    thunks = [_pull_tdx, _pull_cls]
    if ak is not None:
        thunks.extend([
            lambda: _pull("stock_info_global_em", "标题", "摘要", "发布时间", "em", 80),
            lambda: _pull("stock_info_global_ths", "标题", "内容", "发布时间", "ths", 40),
            lambda: _pull("stock_info_global_futu", "标题", "内容", "发布时间", "futu", 50),
            lambda: _pull("stock_info_global_sina", "", "内容", "时间", "sina", 40),
        ])
    for batch in _parallel(thunks):
        if isinstance(batch, list):
            raw.extend(batch)
    out["sources_tried"] = len(thunks)
    out["sources_live"] = len({str(item.get("tag") or "") for item in raw if item.get("tag")})

    by: dict = {}
    order: list[str] = []
    for raw_item in raw:
        tag = str(raw_item.get("tag") or "").strip()
        title = str(raw_item.get("title") or "").strip()
        content = str(raw_item.get("content") or "").strip()
        tm = str(raw_item.get("time") or "").strip()
        if not (title or content):
            continue
        k = _news_key(title, content)
        if not k:
            continue
        match = k if k in by else next(
            (
                old_key for old_key in reversed(order[-80:])
                if _same_news_event(
                    by[old_key],
                    {"title": title, "content": content, "time": tm},
                )
            ),
            None,
        )
        if match:
            if tag not in by[match]["_srcs"]:
                by[match]["_srcs"].add(tag)
                by[match]["observations"].append(_news_observation(raw_item))
            if not by[match]["time"] and tm:
                by[match]["time"] = tm
        else:
            by[k] = {
                "title": (title or content[:40]), "content": (content or title),
                "time": tm, "_srcs": {tag}, "observations": [_news_observation(raw_item)],
            }
            order.append(k)
    items = []
    for k in order:
        it = by[k]
        n = len(it["_srcs"])
        observations = list(it["observations"])
        known_origins = {
            str(obs.get("publisher_name"))
            for obs in observations
            if obs.get("source_relation") == "original_publisher" and obs.get("publisher_name")
        }
        origin_unknown = any(
            obs.get("source_relation") != "original_publisher"
            for obs in observations
        )
        event_payload = {
            "title": it["title"][:120], "content": it["content"][:400],
            "published_at": _published_at(it["time"]),
        }
        items.append({
            **event_payload,
            "time": it["time"],  # 旧客户端兼容字段
            "confirms": n,        # 旧字段：现只表示采集端命中数
            "multi": n >= 2,     # 旧字段：不等于独立信源互证
            "fetched_at": max((str(obs.get("fetched_at") or "") for obs in observations), default=fetched_at),
            "content_hash": compute_content_hash(event_payload),
            "evidence_level": _event_evidence_level(observations),
            "degraded_reasons": sorted({
                reason for obs in observations for reason in (obs.get("degraded_reasons") or [])
            }),
            "observations": observations,
            "corroboration": {
                "collection_endpoint_count": n,
                "independent_origin_count": len(known_origins),
                "independent_origin_confirmed": len(known_origins) >= 2,
                "origin_unknown": origin_unknown,
            },
        })
    items.sort(key=lambda x: _news_time_key(x.get("time", "")), reverse=True)
    out["items"] = items[:limit]
    if out["items"]:
        out["latest_event_time"] = out["items"][0].get("time") or None
        out["state"] = "live"
        out["used_last_good"] = False
        try:
            _save_cn_last_good(out)
        except OSError:
            # 快照落盘失败不能隐藏本轮已取得的真实快讯。
            pass
    else:
        reason = (
            "财经快讯（财联社、TDX 舆情与公开聚合源均不可用）"
            if ak is not None else "财经快讯（财联社与 TDX 舆情不可用，akshare 未安装）"
        )
        cached = _load_cn_last_good()
        if cached:
            replay = dict(cached)
            replay["state"] = "last_good"
            replay["used_last_good"] = True
            replay["replayed_at"] = fetched_at
            replay["live_attempted_at"] = fetched_at
            replay["sources_tried"] = len(thunks)
            replay["sources_live"] = 0
            replay["missing"] = list(dict.fromkeys([
                *(replay.get("missing") or []),
                f"{reason}；沿用 {replay.get('ts') or '最近'} 成功快照",
            ]))
            return replay
        out["state"] = "missing"
        out["used_last_good"] = False
        out["missing"].append(reason)
    return out


def _build_a_share_news(limit: int = 40) -> dict:
    """真实构建 A 股新闻页面响应；调用方负责缓存、快照与后台刷新。"""
    from .news_mapping import map_news_items  # noqa: PLC0415

    bounded = max(1, min(int(limit), 80))
    feed = cn_news_aggregated(limit=bounded) or {}
    mapped = map_news_items(list(feed.get("items") or []))
    summary = mapped.get("summary") if isinstance(mapped, dict) else {}
    mapped_items = list(mapped.get("items") or []) if isinstance(mapped, dict) else []
    observation_summary: dict = {"enabled": False, "state": "disabled", "tracked": 0}
    try:
        from . import news_observation  # noqa: PLC0415

        enriched = news_observation.enrich_items(mapped_items)
        mapped_items = list(enriched.get("items") or mapped_items)
        observation_summary = dict(enriched.get("summary") or observation_summary)
    except Exception as exc:  # noqa: BLE001 -- 账本故障不能隐藏新闻原文/实体映射
        observation_summary = {
            "enabled": True, "state": "unavailable", "tracked": 0,
            "missing": [f"事件观察账本暂不可用:{type(exc).__name__}"],
        }
    sources_tried = int(feed.get("sources_tried") or 0)
    sources_live = int(feed.get("sources_live") or 0)
    if observation_summary.get("state") == "unavailable":
        validation_state = "unavailable"
    elif not observation_summary.get("enabled"):
        validation_state = "disabled"
    elif observation_summary.get("observing"):
        validation_state = "observing"
    elif observation_summary.get("waiting") and not observation_summary.get("observed"):
        validation_state = "waiting"
    elif observation_summary.get("observed") and not observation_summary.get("waiting"):
        validation_state = "observed"
    else:
        validation_state = "mixed" if observation_summary.get("tracked") else "empty"
    return {
        "ok": True,
        "ts": ts_str(),
        "cn_fetched_at": feed.get("ts"),
        "fetched_at": feed.get("fetched_at"),
        "latest_event_time": feed.get("latest_event_time"),
        "refresh_seconds": NEWS_FEED_REFRESH_SECONDS,
        "sources_tried": sources_tried,
        "sources_live": sources_live,
        "missing": list(feed.get("missing") or []),
        "items": mapped_items,
        "mapping": summary if isinstance(summary, dict) else {},
        "observation": observation_summary,
        "pipeline": {
            "source_state": "complete" if sources_tried and sources_live == sources_tried else ("degraded" if sources_live else "unavailable"),
            "entity_mapping_state": str((summary or {}).get("state") or "unavailable"),
            "market_observation_state": str(observation_summary.get("state") or "unavailable"),
            "validation_state": validation_state,
        },
    }


@ttl_cache(NEWS_FEED_REFRESH_SECONDS)
def _refresh_a_share_news(limit: int = 40) -> dict:
    """单飞刷新完整响应并落客户隔离快照；30 秒内复用同一真实结果。"""
    payload = _build_a_share_news(limit)
    saved_at = None
    try:
        saved_at = _save_a_share_last_good(payload)
    except OSError as exc:
        log.warning("舆情事件完整快照保存失败，本轮真实结果仍返回: %s", str(exc)[:180])
    stamp = saved_at or now_cn().isoformat(timespec="seconds")
    return _with_a_share_snapshot(
        payload,
        served_from_snapshot=False,
        saved_at=stamp,
        age_seconds=0,
        refreshing=False,
    )


def _background_refresh_a_share_news(limit: int) -> None:
    global _A_SHARE_REFRESHING  # noqa: PLW0603
    try:
        _refresh_a_share_news(limit)
    except Exception as exc:  # noqa: BLE001 -- 后台失败必须保留已有完整快照
        log.warning("舆情事件后台刷新失败，继续沿用最近完整快照: %s", str(exc)[:180])
    finally:
        with _A_SHARE_REFRESH_LOCK:
            _A_SHARE_REFRESHING = False


def _start_a_share_refresh(limit: int) -> bool:
    """最多启动一个后台刷新线程；True 表示已有或刚启动刷新。"""
    global _A_SHARE_REFRESHING  # noqa: PLW0603
    with _A_SHARE_REFRESH_LOCK:
        if _A_SHARE_REFRESHING:
            return True
        _A_SHARE_REFRESHING = True
    try:
        threading.Thread(
            target=_background_refresh_a_share_news,
            args=(limit,),
            name="a-share-news-refresh",
            daemon=True,
        ).start()
        return True
    except RuntimeError:
        with _A_SHARE_REFRESH_LOCK:
            _A_SHARE_REFRESHING = False
        return False


def a_share_news(limit: int = 40) -> dict:
    """快照优先返回完整舆情页面；过期后单飞后台刷新，无快照时才同步冷加载。"""
    bounded = max(1, min(int(limit), 80))
    cached = _load_a_share_last_good()
    if cached is None:
        return _refresh_a_share_news(bounded)
    payload, saved_at, age_seconds = cached
    refreshing = age_seconds > NEWS_FEED_REFRESH_SECONDS and _start_a_share_refresh(bounded)
    return _with_a_share_snapshot(
        payload,
        served_from_snapshot=True,
        saved_at=saved_at,
        age_seconds=age_seconds,
        refreshing=refreshing,
    )


@ttl_cache(600)
def us_market() -> dict:
    """隔夜美股：三大指数 + 知名科技股（真实行情，拿不到就标记不全）。"""
    missing: list[str] = []
    out: dict = {"ok": True, "ts": ts_str(), "missing": missing, "indexes": [], "tech_stocks": []}
    # 腾讯公开行情直接给最新点位、昨收和涨跌幅，避免新浪指数历史接口内部 V8
    # 在后台线程并发时 abort 整个服务进程。
    index_quotes = tc.us_quotes([symbol for symbol, _ in _US_INDEX_TC])
    for symbol, name in _US_INDEX_TC:
        row = index_quotes.get(symbol) or {}
        if row.get("price") is not None:
            out["indexes"].append({
                "name": row.get("name") or name,
                "close": row.get("price"),
                "change_pct": row.get("change_pct"),
                "date": str(row.get("as_of") or "")[:10],
                "source": "腾讯财经",
            })
        else:
            missing.append(f"美股指数:{name}")

    ak = get_ak()

    # 科技股【腾讯优先】：腾讯 us<TICKER> 海外 CDN 可达，客户机东财(stock_us_famous_spot_em)
    # 走被封 push2 整片空白时仍能出数；腾讯失败再回退东财(参照 market.py index_quotes 接法)。
    tq = tc.us_quotes([s for s, _ in _US_TECH_TC])          # {symbol: {price,change_pct,...}} 或 {}
    if tq:
        for sym, name in _US_TECH_TC:
            d = tq.get(sym)
            if d and d.get("price") is not None:
                out["tech_stocks"].append(
                    {"name": d.get("name") or name, "price": d.get("price"),
                     "change_pct": d.get("change_pct")}
                )
    if not out["tech_stocks"] and ak is not None:
        df = safe(ak.stock_us_famous_spot_em, symbol="科技类", label="美股知名科技股", timeout=_NEWS_TIMEOUT)
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                out["tech_stocks"].append(
                    {
                        "name": str(r.get("名称", "")),
                        "price": to_float(r.get("最新价")),
                        "change_pct": to_float(r.get("涨跌幅")),
                    }
                )
    if not out["tech_stocks"]:
        missing.append("美股科技股行情")
    return out


@ttl_cache(600)
def hk_indexes() -> list[dict]:
    """港股主要指数（恒生/国企/恒生科技）。
    【腾讯优先】：腾讯 qt.gtimg.cn 海外 CDN 可达，客户机东财(stock_hk_index_spot_em)
    走被封 push2 整片空白时仍能出数；腾讯失败再回退东财(参照 market.py index_quotes 接法)。"""
    # 腾讯优先（强制直连，独立于东财）。close=点位、change_pct=%。
    tq = tc.hk_index_quotes([s for s, _ in _HK_INDEX_TC])   # {symbol: {price,change_pct,...}} 或 {}
    if tq:
        out = []
        for sym, name in _HK_INDEX_TC:
            d = tq.get(sym)
            if d and d.get("price") is not None:
                out.append({
                    "name": d.get("name") or name,
                    "close": d.get("price"),               # 港股指数点位
                    "change_pct": d.get("change_pct"),     # %
                })
        if out:
            return out[:6]
    # 兜底：东财 akshare 港股指数（被封机器上多半空，但保留双保险）。
    ak = get_ak()
    if ak is None:
        return []
    out = []
    for fn_name, want in (("stock_hk_index_spot_em", {"HSI": "恒生指数", "HSCEI": "国企指数", "HSTECH": "恒生科技"}),):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        df = safe(fn, label="港股指数", timeout=_NEWS_TIMEOUT)
        if df is None or df.empty:
            continue
        try:
            for _, r in df.iterrows():
                name = str(r.get("名称", r.get("name", "")))
                if any(k in name or k in str(r.get("代码", "")) for k in ("恒生", "国企", "科技")):
                    out.append({
                        "name": name,
                        "close": to_float(r.get("最新价", r.get("close"))),
                        "change_pct": to_float(r.get("涨跌幅", r.get("change_pct"))),
                    })
        except Exception:
            pass
        if out:
            break
    return out[:6]


@ttl_cache(NEWS_FEED_REFRESH_SECONDS)
def multi_market_news() -> dict:
    """多市场聚合：A股快讯 + 美/港行情 + 独立低频全球事件元数据。"""
    missing: list[str] = []
    out: dict = {"ok": True, "ts": ts_str(), "missing": missing}
    # 国内快讯为普通 JSON；美股/港股走腾讯公开行情。三块均不执行进程内 V8。
    cn = cn_news_aggregated(limit=40) or {}   # 多源聚合+交叉验证（东财/同花顺/富途/新浪），源名不外传
    us, hk, global_events = _parallel([us_market, hk_indexes, gdelt.global_events])
    us = us or {}
    hk = hk or []
    global_events = global_events or {
        "ok": False, "state": "unavailable", "items": [], "source": "GDELT DOC 2.0"
    }
    out["cn"] = cn.get("items", [])
    out["cn_fetched_at"] = cn.get("ts")
    out["latest_event_time"] = cn.get("latest_event_time")
    out["refresh_seconds"] = NEWS_FEED_REFRESH_SECONDS
    out["cn_missing"] = list(cn.get("missing") or [])
    out["cn_sources_tried"] = int(cn.get("sources_tried") or 0)
    out["cn_sources_live"] = int(cn.get("sources_live") or 0)
    if not out["cn"]:
        missing.append("A股快讯")
    out["us"] = {"indexes": us.get("indexes", []), "tech_stocks": us.get("tech_stocks", []), "news": []}
    if not us.get("indexes"):
        missing.append("美股行情")
    out["hk"] = {"indexes": hk}
    if not hk:
        missing.append("港股指数")
    out["global_events"] = {
        "state": global_events.get("state"),
        "source": global_events.get("source"),
        "items": list(global_events.get("items") or []),
        "note": global_events.get("note") or global_events.get("license_note"),
    }
    if global_events.get("state") == "unavailable":
        missing.append("全球事件增强")
    return out
