"""推荐留痕与成功率核验（母文档 §29）。

闭环定位：所有推荐(盘前/尾盘/问股/趋势票/量化/产业链)落库即固定历史判断，
今日/到期走势用**真实现价**回验是否兑现，append-only 沉淀长期成功率。

═══════════════════ 最高铁律（不可破） ═══════════════════
1. 收益率/盈亏**只能由真实现价 vs 推荐价算**：
   final_return = (current_price - rec_price) / rec_price * 100  (单位 %)
   current_price 必须来自 stock.stock_detail 的真实行情；缺失 → hit_status="pending"
   并在 missing 里标注，**绝不编一个收益数字**。
2. max_return / max_drawdown 需要推荐期内**逐日历史**才能算；本系统无逐日存储 →
   一律置 None 并标 missing，**绝不用单点现价冒充区间极值**（那会给交易员错觉）。
3. hit/miss 只按显式 direction 与真实 final_return 确定性判定：买入类上涨命中，
   回避/减仓类下跌命中；方向不明则 degraded/pending。不调 AI、不主观。
4. 统计指标(胜率/盈亏比等)全部来自已落库的真实 final_return；样本 <5 时照算但标
   "样本不足，仅供参考"(missing)，不放大不掩盖。
本模块只做**确定性计算 + 缺失标注**，不生成任何数字，不改写任何历史记录。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..data.base import now_cn, trade_dates


# ── 统一推荐事件（Phase 0）──
#
# 旧 recommendations.json 会为了控制体积保留最近 1000 条，且 status 会在回验后更新；
# 因此它不能充当「历史判断不可改写」的完整事件账本。新账本使用 JSONL，每条推荐
# 只追加一行，不提供 update/delete 能力。旧表仍继续双写，保持已有 API/页面兼容。

RECOMMENDATION_EVENT_SCHEMA_VERSION = "1.0"
RECOMMENDATION_REVIEW_SCHEMA_VERSION = "2.0"
MIN_STABLE_SAMPLE = 30
REVIEW_WINDOWS = (
    {"window": "T0", "trade_day_offset": 0, "status": "planned"},
    {"window": "T1", "trade_day_offset": 1, "status": "planned"},
    {"window": "T2", "trade_day_offset": 2, "status": "planned"},
    {"window": "T5", "trade_day_offset": 5, "status": "planned"},
)
_EVENT_APPEND_LOCK = threading.Lock()
_RECOMMENDATION_TX_LOCK = threading.RLock()
_VERIFY_LOCKS: dict[tuple[int, int], object] = {}


def _text(value, limit: int = 500) -> str | None:
    value = str(value or "").strip()
    return value[:limit] if value else None


def _optional_number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _optional_positive_number(value):
    number = _optional_number(value)
    return number if number is not None and number > 0 else None


def _string_list(value, *, limit: int = 100) -> list[str]:
    """把显式传入的证据/失效条件收敛为去重列表。

    不从推荐文案推断证据；只保存调用方实际给出的值。
    """
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for raw in values:
        text = _text(raw, limit)
        if text and text not in result:
            result.append(text)
    return result


def _first_present(item: dict, *keys: str):
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _json_copy(value):
    """复制调用方显式给出的结构化字段。

    risk_decision 可能是字符串，也可能是风险闸输出的对象；留痕层不对
    它做语义推断，只确保落库值可 JSON 序列化且不与输入共享容器。
    """
    if value in (None, ""):
        return None
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return _text(value, 1000)


def _target_prices(item: dict) -> list[float]:
    raw_target = item.get("target")
    values = list(raw_target) if isinstance(raw_target, (list, tuple)) else [raw_target]
    values.extend((item.get("target_price"), item.get("target_low"), item.get("target_high")))
    prices: list[float] = []
    for value in values:
        number = _optional_positive_number(value)
        if number is not None and number not in prices:
            prices.append(number)
    return prices


def _reference_price_evidence(item: dict, price: float) -> dict:
    """建立价格证据回执，但不把 API 录入时间冒充为行情观测时间。

    旧客户端只传 rec_price 时，价格数值会被保存，但 provenance_status 明确是
    ``unverified``；只有调用方同时给出可追溯的证据 ID，或数据源+观测时间，才标为
    ``traceable``。
    """
    nested = item.get("reference_price_evidence")
    nested = nested if isinstance(nested, dict) else {}
    evidence_id = _text(
        nested.get("evidence_id") or item.get("price_evidence_id")
        or item.get("reference_price_evidence_id"),
        120,
    )
    source_id = _text(
        nested.get("source_id") or item.get("reference_price_source")
        or item.get("price_source"),
        120,
    )
    observed_at = _text(
        nested.get("observed_at") or item.get("reference_price_time")
        or item.get("price_observed_at"),
        40,
    )
    traceable = bool(evidence_id or (source_id and observed_at))
    return {
        "value": price,
        "currency": _text(nested.get("currency") or item.get("currency"), 12),
        "evidence_id": evidence_id,
        "source_id": source_id,
        "observed_at": observed_at,
        "provenance_status": "traceable" if traceable else "unverified",
    }


def _entry_trigger(item: dict, entry_condition: str | None, price_evidence: dict) -> dict:
    """冻结入场触发口径；不从自由文本猜比较符或阈值。"""
    raw = item.get("entry_trigger")
    raw = raw if isinstance(raw, dict) else {}
    state = str(raw.get("state") or item.get("trigger_status") or "").strip().casefold()
    if state not in {"triggered", "not_triggered", "not_applicable", "unknown"}:
        state = ""
    rule = raw.get("rule") if isinstance(raw.get("rule"), dict) else None
    if not state:
        # 没有条件的“按当前参考价立即执行”建议，可由创建时价格证据充当触发证据；
        # 有自由文本条件时则只能 unknown，不能事后看涨跌反推“当时触发过”。
        state = "unknown" if entry_condition else "triggered"
    evidence_id = _text(raw.get("evidence_id"), 120)
    if state == "triggered" and not evidence_id:
        evidence_id = _text(price_evidence.get("evidence_id"), 120)
    return {
        "state": state,
        "time": _text(raw.get("time") or raw.get("triggered_at"), 40),
        "price": _optional_positive_number(raw.get("price")),
        "evidence_id": evidence_id,
        "rule": _json_copy(rule),
        "reason": _text(raw.get("reason"), 300),
    }


@dataclass(frozen=True)
class RecommendationEvent:
    """统一推荐创建事件。

    所有业务字段都来自调用方显式输入；未给出的止损、目标、仓位、状态和置信度
    保存为 None，绝不由留痕层推测。
    """

    payload: dict

    @classmethod
    def from_input(cls, item: dict, *, legacy_rec_id: str | None = None) -> "RecommendationEvent":
        if not isinstance(item, dict):
            raise ValueError("推荐应为对象")
        code = str(item.get("code") or item.get("symbol") or "").strip()
        if not code:
            raise ValueError("缺少股票代码")
        # 与旧 add_rec 保持一致：纯数字代码补齐 6 位；市场后缀则原样保留。
        symbol = code.zfill(6) if code.isdigit() else code[:32]

        price = _optional_positive_number(
            item.get("reference_price") if item.get("reference_price") not in (None, "")
            else item.get("rec_price")
        )
        if price is None:
            raise ValueError("推荐价应为正数（真实成交/盘口价或用户录入）")

        now = now_cn()
        event_time = now.isoformat(timespec="seconds")
        supplied_time = _text(item.get("rec_time"), 40)
        client_request_id = _text(item.get("client_request_id"), 120)
        event_id = (
            "re_" + uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hermes-recommendation:{client_request_id}",
            ).hex
            if client_request_id
            else f"re_{int(now.timestamp() * 1000)}_{uuid.uuid4().hex[:10]}"
        )
        invalidation: list[str] = []
        for value in (
            item.get("invalidation"), item.get("no_buy_condition"),
            item.get("abandon_condition"),
        ):
            for entry in _string_list(value, limit=500):
                if entry not in invalidation:
                    invalidation.append(entry)

        price_evidence = _reference_price_evidence(item, price)
        entry_condition = _text(_first_present(item, "entry_condition", "buy_condition"), 500)
        event = {
            "schema_version": RECOMMENDATION_EVENT_SCHEMA_VERSION,
            "event_type": "recommendation_created",
            "event_id": event_id,
            "client_request_id": client_request_id,
            "proposal_id": _text(item.get("proposal_id"), 120),
            "created_at": event_time,
            "recommendation_time": supplied_time or event_time,
            # 旧 API 没有交易日概念；不用创建日冒充交易日。
            "trade_day": _text(item.get("trade_day"), 10),
            "legacy_rec_id": _text(legacy_rec_id, 80),
            "source": _text(item.get("source"), 80) or "generic_api",
            "source_artifact": _json_copy(item.get("source_artifact")),
            "symbol": symbol,
            "name": _text(item.get("name"), 60),
            "market": _text(item.get("market"), 30),
            "direction": _text(_first_present(item, "direction", "rec_type"), 40),
            "entry_condition": entry_condition,
            "reference_price": price,
            "reference_price_evidence": price_evidence,
            "entry_trigger": _entry_trigger(item, entry_condition, price_evidence),
            "stop": {
                "price": _optional_positive_number(
                    _first_present(item, "stop", "stop_price", "stop_loss_price")
                ),
                "condition": _text(item.get("stop_loss_condition"), 500),
            },
            "target": {
                "prices": _target_prices(item),
                "condition": _text(item.get("take_profit_condition"), 500),
            },
            "position_cap_pct": _optional_number(
                _first_present(item, "position_cap_pct", "position_pct", "position")
            ),
            "position_instruction": _text(item.get("position"), 120),
            "regime": _text(_first_present(item, "regime", "market_state"), 120),
            # 保留原数值与显式 scale，不擅自把 68 换算成 0.68。
            "confidence": _optional_number(item.get("confidence")),
            "confidence_scale": _text(item.get("confidence_scale"), 30),
            "proposed_confidence": _optional_number(item.get("proposed_confidence")),
            "proposed_position_cap_pct": _optional_number(item.get("proposed_position_cap_pct")),
            "decision_authority": _text(item.get("decision_authority"), 80),
            "risk_decision": _json_copy(item.get("risk_decision")),
            "evidence_ids": _string_list(item.get("evidence_ids"), limit=120),
            "provenance_status": (
                "verified" if bool(item.get("_server_validated") and item.get("_score_eligible"))
                else "unverified"
            ),
            "score_eligible": bool(item.get("_server_validated") and item.get("_score_eligible")),
            "invalidation": invalidation,
            "review_windows": [dict(window) for window in REVIEW_WINDOWS],
        }
        return cls(event)

    def with_legacy_rec_id(self, rec_id: str) -> "RecommendationEvent":
        updated = dict(self.payload)
        updated["legacy_rec_id"] = _text(rec_id, 80)
        return RecommendationEvent(updated)

    def as_dict(self) -> dict:
        # JSON round-trip 保证返回值不与 frozen dataclass 内部共享嵌套容器。
        return json.loads(json.dumps(self.payload, ensure_ascii=False))


class RecommendationEventStore:
    """JSONL append-only 推荐事件账本；只有 append/read，没有覆盖或删除方法。"""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "recommendation_events" / "events.v1.jsonl"

    def append(self, event: RecommendationEvent | dict) -> dict:
        record = event.as_dict() if isinstance(event, RecommendationEvent) else dict(event)
        if record.get("schema_version") != RECOMMENDATION_EVENT_SCHEMA_VERSION:
            raise ValueError("推荐事件 schema_version 不受支持")
        if record.get("event_type") != "recommendation_created":
            raise ValueError("推荐事件 event_type 不受支持")
        if not record.get("event_id"):
            raise ValueError("推荐事件缺少 event_id")
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _EVENT_APPEND_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 恢复机制可能重放同一 intent。同 event_id 的完全相同事件
            # 视为幂等成功；内容不同则拒绝，防止 ID 碰撞篡改旧记录。
            if self.path.exists():
                for existing in self.events():
                    if existing.get("event_id") != record["event_id"]:
                        continue
                    if existing != record:
                        raise ValueError("event_id 已存在且内容不一致")
                    return existing
            # O_APPEND 保证不会通过本接口覆盖旧事件；fsync 让 API 成功回执前先落盘。
            fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                remaining = memoryview(encoded.encode("utf-8"))
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("推荐事件追加写入失败")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        return record

    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    # 不因单行意外损坏就隐藏其它可读的历史事件。
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows


def event_store_for(store) -> RecommendationEventStore:
    root = getattr(store, "root", None)
    if root is None:
        raise ValueError("推荐存储缺少 root 目录")
    return RecommendationEventStore(root)


def _legacy_item_for_event(item: dict, event: RecommendationEvent, rec_id: str) -> dict:
    """构造可幂等重放的旧表物化记录。"""
    legacy_item = dict(item)
    legacy_item["_rec_id"] = rec_id
    # 新 schema 命名是 reference_price/symbol/direction/entry_condition；先补齐
    # 旧表仍在读的别名，保持现有页面/API 兼容。
    if legacy_item.get("rec_price") in (None, ""):
        legacy_item["rec_price"] = event.payload["reference_price"]
    if not str(legacy_item.get("code") or "").strip():
        symbol = str(event.payload["symbol"])
        base_code = symbol.split(".", 1)[0]
        legacy_item["code"] = base_code if len(base_code) == 6 and base_code.isdigit() else symbol
    if legacy_item.get("rec_type") in (None, "") and event.payload.get("direction"):
        legacy_item["rec_type"] = event.payload["direction"]
    if legacy_item.get("buy_condition") in (None, "") and event.payload.get("entry_condition"):
        legacy_item["buy_condition"] = event.payload["entry_condition"]
    risk_decision = event.payload.get("risk_decision")
    if legacy_item.get("risk_level") in (None, "") and risk_decision not in (None, ""):
        if isinstance(risk_decision, dict):
            legacy_item["risk_level"] = _first_present(
                risk_decision, "decision", "action", "level", "status"
            ) or json.dumps(risk_decision, ensure_ascii=False, separators=(",", ":"))
        else:
            legacy_item["risk_level"] = risk_decision
    return legacy_item


def _intent_path(store, event_id: str) -> Path:
    return Path(store.root) / "recommendation_events" / "pending" / f"{event_id}.json"


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _write_intent(store, payload: dict) -> Path:
    """在任何双写前持久化 intent，崩溃后可完成未完一侧。"""
    event_id = str((payload.get("event") or {}).get("event_id") or "")
    if not event_id.startswith("re_"):
        raise ValueError("推荐 intent 缺少合法 event_id")
    path = _intent_path(store, event_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("推荐事务 intent 损坏") from exc
        if existing != payload:
            raise ValueError("event_id 已存在且 intent 内容不一致")
        return path
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("推荐 intent 写入失败")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    _fsync_dir(path.parent)
    return path


def _finish_intent(store, intent: dict, path: Path) -> dict:
    legacy_item = dict(intent.get("legacy_item") or {})
    event = dict(intent.get("event") or {})
    rec_id = str(intent.get("rec_id") or "")
    if not rec_id or legacy_item.get("_rec_id") != rec_id:
        raise ValueError("推荐 intent 的 rec_id 不一致")
    saved_rec_id = store.add_rec(legacy_item)
    if saved_rec_id != rec_id:
        raise RuntimeError("推荐旧表物化 ID 不一致")
    saved = event_store_for(store).append(event)

    # Store.add_rec 为了兼容旧版只接收少量白名单字段。双写后若仍丢掉
    # direction/evidence/risk_decision，回验会把“回避”当成“买入”判定。因此只对
    # 刚刚写入的 rec_id 追加新 schema 快照；不删除、不改名任何旧字段。
    _attach_event_fields_to_legacy(store, rec_id, saved)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    _fsync_dir(path.parent)
    return {"rec_id": rec_id, "event_id": saved["event_id"], "event": saved}


def recover_pending_recommendations(store) -> dict:
    """幂等重放上次中断的双写事务。"""
    pending = Path(store.root) / "recommendation_events" / "pending"
    recovered = 0
    failed: list[str] = []
    with _RECOMMENDATION_TX_LOCK:
        for path in sorted(pending.glob("re_*.json")) if pending.exists() else []:
            try:
                intent = json.loads(path.read_text(encoding="utf-8"))
                _finish_intent(store, intent, path)
                recovered += 1
            except Exception:  # noqa: BLE001 — 保留 intent 供下次重放
                failed.append(path.stem)
    return {"recovered": recovered, "failed": failed}


def _same_business_event(left: dict, right: dict) -> bool:
    keys = ("client_request_id", "source", "symbol", "direction", "reference_price")
    return all(left.get(key) == right.get(key) for key in keys)


def record_recommendation(store, item: dict) -> dict:
    """统一推荐入口：持久化 intent + 旧表 + append-only 事件账本。

    API 成功回执只会在两侧都完成后返回。任一侧中途故障时 intent
    保留，下次调用可幂等重放，不会重复事件或旧记录。
    """
    with _RECOMMENDATION_TX_LOCK:
        recovery = recover_pending_recommendations(store)
        if recovery["failed"]:
            raise RuntimeError("存在无法恢复的推荐事务")

        event = RecommendationEvent.from_input(item)
        ledger = event_store_for(store)
        for existing in ledger.events():
            if existing.get("event_id") != event.payload["event_id"]:
                continue
            if not _same_business_event(existing, event.payload):
                raise ValueError("client_request_id 已用于不同推荐")
            return {
                "rec_id": existing.get("legacy_rec_id"),
                "event_id": existing["event_id"],
                "event": existing,
                "idempotent_replay": True,
            }

        rec_id = f"r_{event.payload['event_id'][3:]}"
        linked_event = event.with_legacy_rec_id(rec_id)
        legacy_item = _legacy_item_for_event(item, linked_event, rec_id)
        intent = {
            "schema_version": "recommendation-dual-write-intent.v1",
            "rec_id": rec_id,
            "legacy_item": legacy_item,
            "event": linked_event.as_dict(),
        }
        path = _write_intent(store, intent)
        return _finish_intent(store, intent, path)


_LEGACY_EVENT_FIELDS = (
    "schema_version", "event_id", "client_request_id", "proposal_id", "trade_day", "direction", "entry_condition",
    "source_artifact",
    "reference_price_evidence", "entry_trigger", "stop", "target", "position_cap_pct",
    "position_instruction", "regime", "confidence", "confidence_scale",
    "proposed_confidence", "proposed_position_cap_pct", "decision_authority",
    "risk_decision", "evidence_ids", "invalidation", "review_windows",
    "provenance_status", "score_eligible",
)


def _attach_event_fields_to_legacy(store, rec_id: str, event: dict) -> None:
    """在旧记录上附加新决策字段，而不改变旧字段契约。

    这个兼容桥仅针对当前 Store 文件实现；注入的测试/外部 store 没有对应
    读写能力时安全跳过，新 JSONL 事件仍是权威记录。
    """
    reader = getattr(store, "recommendations", None)
    writer = getattr(store, "_write_json", None)
    path = getattr(store, "_recommendations_path", None)
    if not callable(reader) or not callable(writer) or path is None:
        return
    rows = reader()
    if not isinstance(rows, list):
        return
    changed = False
    for row in reversed(rows):
        if isinstance(row, dict) and row.get("rec_id") == rec_id:
            for field in _LEGACY_EVENT_FIELDS:
                row[field] = _json_copy(event.get(field))
            changed = True
            break
    if changed:
        writer(path, rows)

# ── 验证周期（交易日）。到期后 pending→hit/miss 才计入统计；未到期始终 pending。
#    数值参照母文档 §29.2：盘前→当日收盘、尾盘→T+1/T+2、趋势票→5/10/20、产业链→20/60…
#    这里取每类的「首个判定窗口」作为是否到期的下限（保守：到了第一档就允许判定）。
_VERIFY_WINDOW_DAYS = {
    "盘前": 0,    # 盘前推荐 → T0 收盘后验证
    "尾盘": 1,    # 尾盘推荐 → T+1/T+2
    "问股": 0,    # 盘中问股 → T0 收盘后可首验
    "趋势票": 5,  # 趋势票 → 5/10/20 日，5 日为首个判定窗口
    "产业链": 20, # 产业链 → 20/60 日
    "风险提示": 1, # 风险提示 → 1/3/5 日
    "持仓": 0,    # 持仓建议 → T0 收盘后可首验
    "量化": 5,    # 量化选股 → 次日买入(T+1)、5 日窗口
    "量化选股": 5, # 兼容 source 全称（quant_pick 写的是"量化选股"，防名不匹配退回默认1日）
}
_DEFAULT_WINDOW_DAYS = 1


def _to_float(v):
    """安全转 float；非数/缺失返回 None（绝不臆造）。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # NaN / inf 视为无效（行情兜底偶发）
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _trade_days_since(rec_time: str) -> int | None:
    """推荐日之后已经完成的交易日数量；同一交易日为 T0。

    优先使用交易所日历；日历不可用时只跳过周末。解析失败返回 None，
    调用方保持 pending，不用自然日提前结算周末/节假日建议。
    """
    if not rec_time:
        return None
    s = str(rec_time).strip().replace("T", " ")   # 兼容 ISO 'T' 分隔(API 直调方常见)，防解析失败永久 pending
    fmt = "%Y-%m-%d %H:%M:%S" if len(s) > 10 else "%Y-%m-%d"
    try:
        from datetime import datetime, timedelta
        start = datetime.strptime(s[:19] if len(s) > 10 else s, fmt).date()
    except (ValueError, TypeError):
        return None
    today = now_cn().date()
    if start > today:
        return None
    calendar = trade_dates()
    if calendar:
        return sum(1 for day in calendar if start.isoformat() < day <= today.isoformat())
    count = 0
    cur = start + timedelta(days=1)
    while cur <= today:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count


def _window_matured(rec_time: str, window: int) -> tuple[bool, int | None]:
    elapsed = _trade_days_since(rec_time)
    if elapsed is None:
        return False, None
    required = max(0, window)
    if elapsed < required:
        return False, elapsed
    if elapsed == required:
        # T0/T1/T2/T5 都是“目标交易日收盘”窗口，不是“进入目标日”就到期。
        # 特别防止 T1 次日 09:31 用盘中价把历史判断永久 closed。
        return now_cn().strftime("%H:%M") >= "15:00", elapsed
    # 已跨过目标交易日，该窗口的 15:00 已经发生。
    return True, elapsed


_UPSIDE_DIRECTIONS = {
    "buy", "add", "long", "bullish",
    "买入", "加仓", "看多", "做多",
}
_DOWNSIDE_DIRECTIONS = {
    "avoid", "reduce", "exit", "sell", "short", "bearish",
    "回避", "减仓", "退出", "卖出", "看空", "做空", "清仓",
}


def _direction_group(rec: dict) -> tuple[str | None, str]:
    """将显式推荐方向收敛为上行/下行验证口径。

    仅读 direction，并用旧字段 rec_type 做兼容回退。watch/hold/中性等不代表
    可确定的涨跌命题，必须降级为 pending，不得默认按“买入”计分。
    """
    raw = _first_present(rec, "direction", "rec_type")
    text = str(raw or "").strip()
    key = text.casefold().replace("_", "").replace("-", "").replace(" ", "")
    if key in _UPSIDE_DIRECTIONS:
        return "upside", text
    if key in _DOWNSIDE_DIRECTIONS:
        return "downside", text
    return None, text


def _window_spec(window) -> tuple[str, int]:
    if isinstance(window, dict):
        label = str(window.get("window") or "").upper()
        try:
            offset = int(window.get("trade_day_offset"))
        except (TypeError, ValueError):
            offset = -1
    elif isinstance(window, str) and window.upper() in {"T0", "T1", "T2", "T5"}:
        label = window.upper()
        offset = int(label[1:])
    elif isinstance(window, int):
        offset = max(0, window)
        label = f"T{offset}"
    else:
        label, offset = "", -1
    if offset < 0:
        raise ValueError("回验窗口应为 T0/T1/T2/T5 或非负交易日偏移")
    return label or f"T{offset}", offset


def _recommendation_day(rec: dict) -> str | None:
    explicit = str(rec.get("trade_day") or "").strip()[:10]
    if len(explicit) == 10:
        return explicit
    value = str(rec.get("rec_time") or rec.get("recommendation_time") or "").strip()
    return value[:10] if len(value) >= 10 else None


def _target_trade_day(rec: dict, offset: int) -> str | None:
    """确定窗口目标交易日；交易所日历不可用时只跳过周末。"""
    from datetime import datetime, timedelta

    start = _recommendation_day(rec)
    if not start:
        return None
    calendar = sorted(day for day in trade_dates() if day >= start)
    if start in calendar:
        pos = calendar.index(start) + offset
        return calendar[pos] if pos < len(calendar) else None
    try:
        current = datetime.strptime(start, "%Y-%m-%d").date()
    except ValueError:
        return None
    count = 0
    while current.weekday() >= 5:
        current += timedelta(days=1)
    while count < offset:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return current.isoformat()


def _target_matured(target_day: str | None) -> bool:
    if not target_day:
        return False
    today = now_cn().strftime("%Y-%m-%d")
    if target_day < today:
        return True
    return target_day == today and now_cn().strftime("%H:%M") >= "15:00"


def _normalise_path(price_path, start_day: str | None, target_day: str | None) -> list[dict]:
    rows: list[dict] = []
    for raw in price_path or []:
        if not isinstance(raw, dict):
            continue
        day = str(raw.get("d") or raw.get("date") or raw.get("trade_day") or "")[:10]
        if not day or (start_day and day < start_day) or (target_day and day > target_day):
            continue
        row = {
            "d": day,
            "o": _to_float(raw.get("o") if raw.get("o") is not None else raw.get("open")),
            "h": _to_float(raw.get("h") if raw.get("h") is not None else raw.get("high")),
            "l": _to_float(raw.get("l") if raw.get("l") is not None else raw.get("low")),
            "c": _to_float(raw.get("c") if raw.get("c") is not None else raw.get("close")),
            "source": raw.get("source"),
            "as_of": raw.get("as_of") or day,
            "adjustment": raw.get("adjustment"),
        }
        rows.append(row)
    return sorted({row["d"]: row for row in rows}.values(), key=lambda row: row["d"])


def _expected_path_days(start_day: str | None, target_day: str | None, offset: int) -> list[str]:
    if not start_day or not target_day:
        return []
    calendar = sorted(day for day in trade_dates() if start_day <= day <= target_day)
    if calendar and calendar[0] == start_day and calendar[-1] == target_day:
        return calendar
    # 缺少完整交易所日历时只要求 offset+1 个观测，避免声称具体休市日。
    return [f"bar-{idx}" for idx in range(offset + 1)]


def _trigger_from_record(rec: dict, path: list[dict]) -> dict:
    trigger = rec.get("entry_trigger") if isinstance(rec.get("entry_trigger"), dict) else {}
    state = str(trigger.get("state") or "").strip().casefold()
    condition = str(rec.get("entry_condition") or rec.get("buy_condition") or "").strip()
    if not state:
        state = "unknown" if condition else "triggered"
    result = {
        "state": state if state in {"triggered", "not_triggered", "not_applicable", "unknown"} else "unknown",
        "time": trigger.get("time"),
        "price": _to_float(trigger.get("price")),
        "evidence_id": trigger.get("evidence_id"),
        "reason": trigger.get("reason"),
    }
    rule = trigger.get("rule") if isinstance(trigger.get("rule"), dict) else None
    if not rule or result["state"] in {"triggered", "not_triggered", "not_applicable"}:
        return result
    operator = str(rule.get("operator") or "").casefold()
    low = _to_float(rule.get("low") if rule.get("low") is not None else rule.get("value"))
    high = _to_float(rule.get("high") if rule.get("high") is not None else rule.get("value"))
    for row in path:
        day_low, day_high = row.get("l"), row.get("h")
        hit = (
            operator in {"lte", "touch_below"} and low is not None and day_low is not None and day_low <= low
        ) or (
            operator in {"gte", "touch_above"} and high is not None and day_high is not None and day_high >= high
        ) or (
            operator in {"between", "range"} and low is not None and high is not None
            and day_low is not None and day_high is not None and day_low <= high and day_high >= low
        )
        if hit:
            result.update({
                "state": "triggered", "time": row["d"],
                "price": low if operator in {"lte", "touch_below"} else high,
                "evidence_id": f"daily-bar:{row['d']}",
                "reason": "结构化入场条件在日线区间内触发",
            })
            return result
    if operator in {"lte", "touch_below", "gte", "touch_above", "between", "range"}:
        result.update({"state": "not_triggered", "reason": "结构化入场条件在窗口内未触发"})
    return result


def verify_one(
    rec: dict,
    current_price,
    *,
    window=None,
    price_path: list[dict] | None = None,
    target_trade_day: str | None = None,
    observation_source: str | None = None,
    benchmark: dict | None = None,
    slippage_bps: float | None = None,
) -> dict:
    """对单条推荐做一次回验，返回一条 review（供 store.save_rec_review append-only 落库）。

    参数
      rec           : 推荐记录（含 rec_price / rec_time / source / sell/stop 等）。
      current_price : 由路由层用 stock.stock_detail(code) 取到的**真实现价**（元）。
                      取不到请传 None —— 本函数会置 hit_status="pending" 并标 missing，
                      绝不在缺价时编造收益（铁律）。

    返回 review（字段对齐母文档 §35.5）：
      review_time, review_type, current_price, max_return, max_drawdown,
      final_return, hit_status(hit/miss/pending), error_reason, continue_follow,
      missing(list)。
    """
    missing: list[str] = []
    now_str = now_cn().strftime("%Y-%m-%d %H:%M:%S")
    source = str(rec.get("source", "")).strip()
    direction_group, direction = _direction_group(rec)

    rec_price = _to_float(rec.get("rec_price"))
    cur = _to_float(current_price)

    # window=None 仅保留旧函数调用兼容；正式 run_verify 始终逐个传 T0/T1/T2/T5。
    if window is None:
        try:
            raw_window = rec.get("verify_days")
            offset = int(raw_window) if raw_window not in (None, "") else _VERIFY_WINDOW_DAYS.get(source, _DEFAULT_WINDOW_DAYS)
        except (TypeError, ValueError):
            offset = _VERIFY_WINDOW_DAYS.get(source, _DEFAULT_WINDOW_DAYS)
        window_label = f"T{offset}"
    else:
        window_label, offset = _window_spec(window)
    target_day = target_trade_day or _target_trade_day(rec, offset)
    matured = _target_matured(target_day)
    start_day = _recommendation_day(rec)
    path = _normalise_path(price_path, start_day, target_day)
    target_bar = next((row for row in reversed(path) if row.get("d") == target_day), None)
    if target_bar and target_bar.get("c") is not None:
        cur = target_bar["c"]
    trigger = _trigger_from_record(rec, path)
    expected_days = _expected_path_days(start_day, target_day, offset)
    observed_days = {row["d"] for row in path if row.get("c") is not None}
    path_complete = bool(expected_days) and len(observed_days) >= len(expected_days)

    review: dict = {
        "review_time": now_str,
        "schema_version": RECOMMENDATION_REVIEW_SCHEMA_VERSION,
        "event_type": "recommendation_reviewed",
        "recommendation_event_id": rec.get("event_id"),
        "review_type": source or "通用",
        "window": window_label,
        "trade_day_offset": offset,
        "target_trade_day": target_day,
        "evaluation_state": "waiting",
        "pending_reason": "window_not_due",
        "terminal": False,
        "trigger": trigger,
        "observation": {
            "trade_day": target_day,
            "price_basis": "target_close",
            "price": cur,
            "observed_at": (target_bar or {}).get("as_of"),
            "source": observation_source or (target_bar or {}).get("source"),
            "evidence_id": f"daily-close:{rec.get('code') or rec.get('symbol')}:{target_day}" if target_bar else None,
        },
        "coverage": {
            "bars_expected": len(expected_days) or (offset + 1),
            "bars_observed": len(observed_days),
            "path_complete": path_complete,
        },
        "current_price": cur,           # 真实现价；缺失即 None，前端显示「—」
        "max_return": None,             # 铁律：无逐日历史 → 不算区间极值
        "max_drawdown": None,           # 同上
        "final_return": None,
        # final_return 始终是标的原始涨跌；directional_return 才是按推荐方向
        # 换算的结果收益。这样“回避后下跌”能命中，又不篡改实际股价收益。
        "directional_return": None,
        "direction": direction or None,
        "direction_group": direction_group or "unknown",
        "quality_status": "ok" if direction_group else "degraded",
        "hit_status": "pending",
        "verdict": None,
        "error_reason": "",
        "continue_follow": True,
        "ai_reflection": "",            # §35.5 字段占位；确定性回验不编反思，留人工/AI 复盘回填
        "missing": missing,
    }

    if direction_group is None:
        missing.append(
            "推荐方向缺失或无法识别（需 buy/add/candidate 或 "
            "avoid/reduce/exit），回验降级为 pending"
        )
        review["error_reason"] = "推荐方向不可判定，未计入胜率"

    if not matured:
        if target_day is None:
            review["pending_reason"] = "target_trade_day_unknown"
            missing.append("推荐交易日或交易所日历不足，无法确定目标交易日")
        return review

    # 自由文本入场条件没有确定性比较规则时，窗口到期只能判“证据不足”；
    # 不能看见后来上涨后倒推条件曾触发。
    if trigger["state"] == "unknown":
        review.update({
            "evaluation_state": "incomparable",
            "pending_reason": "trigger_unknown",
            "terminal": True,
            "verdict": "insufficient_evidence",
            "quality_status": "degraded",
            "continue_follow": False,
            "error_reason": "入场条件是自由文本或缺少触发证据，不能判定是否执行",
        })
        missing.append("入场触发不可验证：未从自由文本事后猜测触发状态")
        return review

    if trigger["state"] == "not_triggered":
        review.update({
            "evaluation_state": "untriggered",
            "pending_reason": None,
            "terminal": True,
            "verdict": "not_triggered",
            "continue_follow": False,
            "error_reason": "结构化入场条件在该窗口内未触发，不计输赢",
        })
        return review

    # —— 现价缺失：绝不编收益，标 pending + missing ——
    if cur is None:
        review["evaluation_state"] = "incomparable"
        review["pending_reason"] = "target_close_missing"
        missing.append("目标交易日收盘价取不到，窗口暂不可比（未用补跑时现价代替）")
        return review
    # —— 推荐价非法/缺失：无法计算相对收益 ——
    if rec_price is None or rec_price <= 0:
        missing.append("推荐价缺失或非法，收益率无法计算（pending，未编造）")
        return review

    # —— 真实收益率：唯一来源是 (现价-推荐价)/推荐价 ——
    final_return = round((cur - rec_price) / rec_price * 100.0, 2)
    review["final_return"] = final_return
    if direction_group == "upside":
        review["directional_return"] = final_return
    elif direction_group == "downside":
        directional_return = round(-final_return, 2)
        review["directional_return"] = 0.0 if directional_return == 0 else directional_return

    if path_complete and path:
        highs = [row["h"] for row in path if row.get("h") is not None]
        lows = [row["l"] for row in path if row.get("l") is not None]
        if highs and lows:
            raw_mfe = round((max(highs) - rec_price) / rec_price * 100.0, 2)
            raw_mae = round((min(lows) - rec_price) / rec_price * 100.0, 2)
            if direction_group == "downside":
                review["max_return"], review["max_drawdown"] = round(-raw_mae, 2), round(-raw_mfe, 2)
            else:
                review["max_return"], review["max_drawdown"] = raw_mfe, raw_mae
    else:
        missing.append("推荐期日线覆盖不完整，MFE/MAE 置空（未用单点冒充路径）")

    benchmark = benchmark if isinstance(benchmark, dict) else {}
    benchmark_start = _to_float(benchmark.get("start"))
    benchmark_end = _to_float(benchmark.get("end"))
    benchmark_return = None
    if benchmark_start is not None and benchmark_start > 0 and benchmark_end is not None:
        benchmark_return = round((benchmark_end - benchmark_start) / benchmark_start * 100.0, 2)
    else:
        missing.append("未冻结与建议同起止时点的基准，基准/超额收益置空")
    review["benchmark"] = {
        "symbol": benchmark.get("symbol") or "sh000300",
        "start": benchmark_start,
        "end": benchmark_end,
        "return_pct": benchmark_return,
        "excess_pct": round(final_return - benchmark_return, 2) if benchmark_return is not None else None,
        "evidence_ids": list(benchmark.get("evidence_ids") or []),
    }
    slip = _to_float(slippage_bps)
    review["slippage"] = {
        "basis_points": slip,
        "source": "execution_receipt" if slip is not None else None,
    }
    if slip is None:
        missing.append("未绑定真实成交回执，滑点置空")

    if direction_group is None:
        return review

    review.update({
        "evaluation_state": "evaluated", "pending_reason": None,
        "terminal": True, "continue_follow": False,
    })
    directional_return = review["directional_return"]
    if directional_return > 0:
        review["hit_status"] = "hit"
        review["verdict"] = "hit"
        review["error_type"] = "N"   # 命中=确定性归因「无错」；miss 不自动拍类型，留待复盘归因（诚实）
    elif directional_return == 0:
        review["hit_status"] = "partial"
        review["verdict"] = "partial_hit"
        review["error_reason"] = "目标日方向收益为 0，记部分命中，不计作胜或负"
    else:
        review["hit_status"] = "miss"
        review["verdict"] = "invalidated"
        # 失效原因只做客观归类，不臆测主观动机；更细原因留给人工/AI 复盘字段(ai_reflection)
        sell_cond = str(rec.get("sell_condition", "")).strip()
        stop_cond = str(rec.get("stop_loss_condition", "")).strip()
        if direction_group == "upside" and stop_cond and final_return <= -abs(_pct_threshold(stop_cond)):
            review["error_reason"] = "触及止损位（现价已跌破推荐止损区间）"
        elif direction_group == "downside":
            review["error_reason"] = "到期价格未下跌（未兑现回避/减仓/退出方向）"
        elif sell_cond:
            review["error_reason"] = "未达预期方向（到期收益为负，对照卖出/不买条件）"
        else:
            review["error_reason"] = "到期收益为负（未兑现推荐方向）"
    return review


def _pct_threshold(cond: str) -> float:
    """从止损条件文本里抠出百分比阈值（如「跌破8%止损」→8.0）。
    抠不到返回 0 → 上层不会据此判定为「触及止损」，只归为普通 miss（保守，不臆造阈值）。"""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(cond or ""))
    if m:
        try:
            return float(m.group(1))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


# 错误类型枚举（白皮书吸收③ 2026-07-05：让"为什么错"可聚合成直方图，而非散文式反思）。
# hit 由 verify_one 确定性置 "N"；miss 的归因是主观判断 → 留给人工/AI 复盘补填，绝不自动拍。
# 唯一数据源在 knowledge/store.py（落库白名单同一份），此处仅 re-export 供统计/前端取标签。
from ..knowledge.store import ERROR_TYPES  # noqa: E402


def stats(recs: list[dict], reviews: list[dict]) -> dict:
    """按独立窗口汇总；未触发/不可比不进入输赢分母。"""
    missing: list[str] = []

    # 只有经服务端证据与风险闸确认的推荐才能进入胜率。
    eligible_ids = {
        str(rec.get("rec_id")) for rec in (recs or [])
        if rec.get("score_eligible") is True and rec.get("rec_id")
    }
    excluded = sum(1 for rec in (recs or []) if str(rec.get("rec_id") or "") not in eligible_ids)

    # 每条 rec_id + window 的最新 review。旧记录没有窗口时归入 legacy，
    # 不会再把一个来源默认窗口冒充为完整 T0/T1/T2/T5。
    latest: dict[str, dict] = {}
    for rv in reviews or []:
        rid = rv.get("rec_id")
        if not rid or str(rid) not in eligible_ids:
            continue
        key = f"{rid}:{rv.get('window') or 'legacy'}"
        prev = latest.get(key)
        if prev is None or str(rv.get("review_time", "")) >= str(prev.get("review_time", "")):
            latest[key] = rv

    total_recs = len(recs or [])
    judged = [rv for rv in latest.values() if rv.get("verdict") in ("hit", "partial_hit", "invalidated")
              or (not rv.get("verdict") and rv.get("hit_status") in ("hit", "miss"))]
    untriggered = sum(1 for rv in latest.values() if rv.get("evaluation_state") == "untriggered")
    incomparable = sum(1 for rv in latest.values() if rv.get("evaluation_state") == "incomparable")
    terminal_keys = {
        key for key, rv in latest.items()
        if rv.get("window") in {"T0", "T1", "T2", "T5"}
        and (rv.get("terminal") is True or rv.get("evaluation_state") in {"evaluated", "untriggered"})
    }
    planned_total = len(eligible_ids) * len(REVIEW_WINDOWS)
    waiting = max(0, planned_total - len(terminal_keys))

    sample = len(judged)
    hits = [rv for rv in judged if rv.get("verdict") == "hit" or (not rv.get("verdict") and rv.get("hit_status") == "hit")]
    partials = [rv for rv in judged if rv.get("verdict") == "partial_hit"]
    misses = [rv for rv in judged if rv.get("verdict") == "invalidated" or (not rv.get("verdict") and rv.get("hit_status") == "miss")]

    def _mean(items):
        # 新回验使用方向调整收益；历史回验没有该字段时保持旧口径。
        vals = [
            _to_float(rv.get("directional_return"))
            if rv.get("directional_return") is not None
            else _to_float(rv.get("final_return"))
            for rv in items
        ]
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    observed_win_rate = round(len(hits) / sample * 100.0, 1) if sample else None
    avg_win = _mean(hits)
    avg_loss = _mean(misses)
    # 盈亏比：需平均收益(>0)与平均亏损(<0，取绝对值)同时存在
    profit_factor = None
    if avg_win is not None and avg_loss not in (None, 0):
        denom = abs(avg_loss)
        if denom > 0:
            profit_factor = round(abs(avg_win) / denom, 2)

    enough = sample >= MIN_STABLE_SAMPLE
    win_rate = observed_win_rate if enough else None
    if not enough:
        missing.append(
            f"可评分窗口仅 {sample} 个（<{MIN_STABLE_SAMPLE}），不展示稳定胜率；"
            "原始观察比例仅供审计"
        )
    if waiting:
        missing.append(f"{waiting} 个窗口仍在等待目标交易日或收盘数据，不计入胜率")
    if untriggered:
        missing.append(f"{untriggered} 个窗口入场条件未触发，不计输赢")
    if incomparable:
        missing.append(f"{incomparable} 个窗口因触发/价格证据不足不可比")
    if excluded:
        missing.append(f"{excluded} 条推荐缺少服务端证据/风险闸回执，已排除出胜率")

    # 错误类型直方图（白皮书吸收③）：只统计已判定样本；miss 未归因的单列计数，绝不猜类型。
    err_hist: dict[str, int] = {}
    unattributed = 0
    for rv in judged:
        et = rv.get("error_type")
        if et in ERROR_TYPES:
            err_hist[et] = err_hist.get(et, 0) + 1
        elif rv.get("hit_status") == "miss":
            unattributed += 1
    if unattributed:
        missing.append(f"{unattributed} 条 miss 未做错误归因（复盘补填 error_type 后可聚合）")

    per_window: dict[str, dict] = {}
    for label in ("T0", "T1", "T2", "T5"):
        rows = [rv for rv in judged if rv.get("window") == label]
        wins = sum(1 for rv in rows if rv.get("verdict") == "hit")
        per_window[label] = {
            "sample": len(rows),
            "wins": wins,
            "partials": sum(1 for rv in rows if rv.get("verdict") == "partial_hit"),
            "losses": sum(1 for rv in rows if rv.get("verdict") == "invalidated"),
            "observed_win_rate": round(wins / len(rows) * 100.0, 1) if rows else None,
            "win_rate": round(wins / len(rows) * 100.0, 1) if len(rows) >= MIN_STABLE_SAMPLE else None,
        }

    return {
        "schema_version": "recommendation-stats.v2",
        "win_rate_unit": "percent",
        "error_types": err_hist,          # {A/B/C/D/N: 条数}，中文标签见 error_type_labels
        "error_type_labels": ERROR_TYPES,
        "unattributed_miss": unattributed,
        "total_recs": total_recs,        # 推荐总条数（含未到期）
        "eligible_total": len(eligible_ids),
        "planned_windows": planned_total,
        "sample": sample,
        "evaluated": sample,
        "triggered": sample,
        "pending": waiting,
        "waiting": waiting,
        "untriggered": untriggered,
        "incomparable": incomparable,
        "excluded": excluded,
        "win_count": len(hits),
        "partial_count": len(partials),
        "loss_count": len(misses),
        "win_rate": win_rate,
        "observed_win_rate": observed_win_rate,
        "avg_win": avg_win,              # 平均收益 %（hit 均值）
        "avg_loss": avg_loss,            # 平均亏损 %（miss 均值，负数）
        "profit_factor": profit_factor,  # 盈亏比 |avg_win/avg_loss|
        "enough_sample": enough,         # False → 前端标「样本不足，仅供参考」
        "minimum_stable_sample": MIN_STABLE_SAMPLE,
        "per_window": per_window,
        "missing": missing,
    }


async def run_verify(store) -> dict:
    import asyncio  # noqa: PLC0415

    key = (id(store), id(asyncio.get_running_loop()))
    lock = _VERIFY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _VERIFY_LOCKS[key] = lock
    async with lock:
        return await _run_verify_locked(store)


async def _run_verify_locked(store) -> dict:
    """用目标交易日历史收盘逐一冻结 T0/T1/T2/T5，不用补跑时现价改判旧窗口。"""
    import asyncio  # noqa: PLC0415
    from ..data import tencent_source  # noqa: PLC0415 — 延迟导入避免循环依赖

    reviews = store.rec_reviews()
    terminal = {
        (str(rv.get("rec_id")), str(rv.get("window")))
        for rv in reviews
        if rv.get("rec_id") and rv.get("window")
        and (rv.get("terminal") is True or rv.get("evaluation_state") in {"evaluated", "untriggered"})
    }
    recs = [r for r in store.recommendations()
            if r.get("status") == "active"
            and r.get("score_eligible") is True]
    verified = 0
    closed = 0
    pending = 0
    incomparable = 0
    untriggered = 0
    for rec in recs:
        code = str(rec.get("code", "")).strip()
        due_windows: list[tuple[str, int, str | None]] = []
        for spec in REVIEW_WINDOWS:
            label, offset = _window_spec(spec)
            if (str(rec.get("rec_id")), label) in terminal:
                continue
            target_day = _target_trade_day(rec, offset)
            if not _target_matured(target_day):
                pending += 1
                continue
            due_windows.append((label, offset, target_day))
        if not due_windows:
            continue

        bars: list[dict] = []
        if len(code) == 6 and code.isdigit():
            try:
                bars = await asyncio.to_thread(
                    tencent_source.stock_daily_bars, code, 80, adjust="none",
                ) or []
            except Exception:  # noqa: BLE001 — 缺失进入不可比，不拿实时价补旧窗口
                bars = []

        for label, _offset, target_day in due_windows:
            review = verify_one(
                rec, None, window=label, price_path=bars,
                target_trade_day=target_day, observation_source="腾讯财经·未复权日K",
            )
            saved = store.save_rec_review(rec.get("rec_id"), review)
            if saved:
                verified += 1
            if review.get("evaluation_state") == "incomparable":
                incomparable += 1
            elif review.get("evaluation_state") == "untriggered":
                untriggered += 1
            elif not review.get("terminal"):
                pending += 1

        latest_reviews = store.rec_reviews()
        completed = {
            str(rv.get("window")) for rv in latest_reviews
            if str(rv.get("rec_id")) == str(rec.get("rec_id"))
            and rv.get("terminal") is True
        }
        if {spec["window"] for spec in REVIEW_WINDOWS}.issubset(completed):
            store.update_rec_status(rec.get("rec_id"), "closed")
            closed += 1
    return {
        "verified": verified,
        "closed": closed,
        "pending": pending,
        "untriggered": untriggered,
        "incomparable": incomparable,
        "windows": [spec["window"] for spec in REVIEW_WINDOWS],
        "price_basis": "target_close",
    }
