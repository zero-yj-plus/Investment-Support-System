"""日报内容版本与来源锁。

页面、文字推送和 PDF 只有在同时指向一个交易日、一个内容哈希和
一组上游定稿时，才能称为“同一份报告”。这里只产生确定性元数据，
不参与任何市场判断。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


REPORT_SCHEMA_VERSION = "daily-report-v2"
LINEAGE_SCHEMA_VERSION = "daily-report-lineage-v1"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def report_content_hash(content_md: str) -> str:
    return hashlib.sha256(str(content_md or "").encode("utf-8")).hexdigest()


def report_ref(rec: dict | None) -> dict | None:
    """把报告记录收敛成可跨页面/推送/PDF 比对的最小引用。"""
    if not isinstance(rec, dict) or not rec.get("content_md"):
        return None
    rtype = str(rec.get("rtype") or "unknown")
    trade_day = str(rec.get("date") or "")
    content_hash = str(rec.get("content_hash") or report_content_hash(rec["content_md"]))
    version = rec.get("report_version")
    if version in (None, ""):
        version = rec.get("plan_version") or 1
    return {
        "rtype": rtype,
        "trade_day": trade_day,
        "content_id": str(rec.get("content_id") or f"report:{rtype}:{trade_day}"),
        "version": version,
        "content_hash": content_hash,
        "created_at": rec.get("created_at"),
    }


def build_lineage(
    *,
    trade_day: str,
    sources: Iterable[dict | None] = (),
    evidence: Iterable[dict | None] = (),
) -> dict:
    source_rows = [dict(row) for row in sources if isinstance(row, dict)]
    evidence_rows = [dict(row) for row in evidence if isinstance(row, dict)]
    body = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "trade_day": str(trade_day),
        "sources": source_rows,
        "evidence": evidence_rows,
    }
    body["lineage_hash"] = canonical_hash(body)
    return body
