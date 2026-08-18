"""文件存储层：报告、知识库、持仓、情绪历史、按日存档。

目录结构（位于各客户自己的 data_dir 下，本地/云端互不串数据）：
  reports/YYYY-MM-DD/{rtype}.json
  archive/YYYY-MM-DD.json
  sentiment/history.json
  knowledge/items.json
  holdings.json
  settings.json
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from ..data.base import now_cn

KNOWLEDGE_CATEGORIES = [
    "交易原则", "亏损禁区", "擅长模式", "不擅长模式",
    "仓位规则", "经验教训", "选股偏好", "复盘笔记", "舆情留档",
]

# 推荐回验的错误类型枚举（白皮书吸收③）——**唯一数据源**：落库白名单与统计直方图共用；
# analysis.recommend 从这里 import（数据层持有枚举，分析层消费，避免两处硬编漂移）。
ERROR_TYPES = {"A": "模型可改进", "B": "数据问题", "C": "黑天鹅", "D": "执行偏差", "N": "无错"}

KNOWLEDGE_RULE_SCHEMA = "KnowledgeRule.v1"

_A_SHARE_CODE_RE = re.compile(
    r"^(?:00[0123]\d{3}|30[01]\d{3}|60[0135]\d{3}|68[89]\d{3}|[48]\d{5}|92\d{4})$"
)
KNOWLEDGE_STATUSES = {
    "draft", "approved", "rejected", "superseded", "legacy_unverified",
}


class Store:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        for sub in ("reports", "archive", "sentiment", "knowledge"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        # 推荐/知识都有“读-改-写 + 追加审计”组合操作，使用
        # RLock 才能让上层原子区间复用 _write_json 而不自锁。
        self._lock = threading.RLock()
        self._recover_knowledge_transaction()

    # ---------- 通用 ----------

    def _read_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, path: Path, obj) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(path.parent, 0o700)
            except OSError:
                pass
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(obj, ensure_ascii=False, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
            self._fsync_dir(path.parent)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _delete_durable(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return
        self._fsync_dir(path.parent)

    @staticmethod
    def today() -> str:
        return now_cn().strftime("%Y-%m-%d")

    @staticmethod
    def now_str() -> str:
        return now_cn().strftime("%Y-%m-%d %H:%M:%S")

    # ---------- 报告 ----------

    def save_report(self, rtype: str, title: str, content_md: str, date: str | None = None,
                    cockpit: dict | None = None, claim_validation: dict | None = None,
                    advice_governance: dict | None = None,
                    input_lineage: dict | None = None,
                    input_context: dict | None = None) -> dict:
        date = date or self.today()
        # 不只在 Engine 层“生成前检查”，存储层也必须保证当日首版不可覆盖。
        # 这样即使定时与手动请求竞态，历史事实仍然不会被改写。
        with self._lock:
            existing = self.get_report(rtype, date)
            if existing and existing.get("content_md"):
                return existing
            rec = {
                "schema_version": "daily-report-v2",
                "rtype": rtype,
                "date": date,
                "title": title,
                "content_md": content_md,
                "content_id": f"report:{rtype}:{date}",
                "content_hash": hashlib.sha256(str(content_md or "").encode("utf-8")).hexdigest(),
                "report_version": 1,
                "created_at": self.now_str(),
                "pushed": False,
            }
            # 报告时点锁定（T9 §0-1）：把生成当刻的驾驶舱风险闸+重点方向快照随报告存下，
            # 前端横幅只渲染此快照——盘前定格 08:50、尾盘定格 14:45，绝不再读实时（杜绝"上午一样下午一样"）。
            if cockpit:
                rec["cockpit"] = cockpit
            # 交易价格 claim 硬闸的定稿回执。Engine 只会在 status=passed 时调用
            # save_report；随正文一起落盘，便于审计「当时为什么允许成为正式报告」。
            if claim_validation:
                rec["claim_validation"] = dict(claim_validation)
            if advice_governance:
                rec["advice_governance"] = dict(advice_governance)
            if isinstance(input_lineage, dict):
                rec["input_lineage"] = dict(input_lineage)
            if isinstance(input_context, dict):
                canonical = json.dumps(
                    input_context,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                context_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                relative = Path("reports") / date / ".inputs" / f"{rtype}.json"
                self._write_json(self.root / relative, {
                    "schema_version": "report-input-v1",
                    "rtype": rtype,
                    "trade_day": date,
                    "content_hash": context_hash,
                    "context": input_context,
                })
                rec["input_context_hash"] = context_hash
                rec["input_snapshot_file"] = str(relative)
            self._write_json(self.root / "reports" / date / f"{rtype}.json", rec)
            return rec

    def get_report(self, rtype: str, date: str | None = None) -> dict | None:
        date = date or self.today()
        return self._read_json(self.root / "reports" / date / f"{rtype}.json", None)

    def mark_pushed(self, rtype: str, date: str | None = None) -> None:
        rec = self.get_report(rtype, date)
        if rec:
            rec["pushed"] = True
            self._write_json(self.root / "reports" / rec["date"] / f"{rtype}.json", rec)

    def set_report_eval(self, rtype: str, date: str, ev: dict) -> None:
        """写入研报数字质检结果（eval 字段）。只附加元信息，绝不改 content_md（当日定稿铁律）。"""
        rec = self.get_report(rtype, date)
        if rec:
            rec["eval"] = dict(ev or {})
            self._write_json(self.root / "reports" / rec["date"] / f"{rtype}.json", rec)

    def set_report_identity(self, rtype: str, date: str, identity: dict) -> dict | None:
        """给已定稿报告附加跨渠道身份，不改正文、校验回执或生成时快照。"""
        rec = self.get_report(rtype, date)
        if not rec:
            return None
        allowed = {"plan_id", "plan_version", "content_id"}
        for key in allowed:
            value = (identity or {}).get(key)
            if value not in (None, ""):
                rec[key] = value
        self._write_json(self.root / "reports" / rec["date"] / f"{rtype}.json", rec)
        return rec

    def reports_of(self, date: str) -> dict[str, dict]:
        folder = self.root / "reports" / date
        result = {}
        if folder.exists():
            for f in folder.glob("*.json"):
                rec = self._read_json(f, None)
                if rec:
                    result[rec.get("rtype", f.stem)] = rec
        return result

    # ---------- 对话会话（盘中问股：多会话 / 历史 / 分组，落各客户 data_dir）----------
    # 文件态：conversations/index.json（会话元数据列表）+ conversations/{id}.json（该会话消息）。
    # 消息只存文本——图片不持久化/不回放（图为单轮补充上下文，避免 base64 撑爆存储与历史窗口）。

    @staticmethod
    def _safe_cid(cid) -> str:
        cid = str(cid or "")
        return cid if cid and all(c in "0123456789abcdefABCDEF" for c in cid) else ""

    def _conv_dir(self) -> Path:
        d = self.root / "conversations"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _update_conv_index(self, fn) -> None:
        """读改写 index.json：全程持锁、直写 tmp（不调 _write_json 以免锁重入死锁）。"""
        with self._lock:
            path = self._conv_dir() / "index.json"
            idx = self._read_json(path, [])
            fn(idx)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)

    def list_conversations(self) -> list[dict]:
        idx = self._read_json(self._conv_dir() / "index.json", [])
        idx.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return idx

    def create_conversation(self, mode: str = "free", title: str = "", group: str = "") -> dict:
        cid = format(time.time_ns(), "x")
        now = self.now_str()
        meta = {"id": cid, "title": (title or "新对话")[:40], "group": str(group or ""),
                "mode": mode or "free", "created_at": now, "updated_at": now, "msg_count": 0}
        self._update_conv_index(lambda idx: idx.append(meta))
        self._write_json(self._conv_dir() / f"{cid}.json", {"id": cid, "messages": []})
        return meta

    def get_conversation(self, cid: str) -> dict | None:
        cid = self._safe_cid(cid)
        if not cid:
            return None
        meta = next((c for c in self._read_json(self._conv_dir() / "index.json", []) if c.get("id") == cid), None)
        if not meta:
            return None
        data = self._read_json(self._conv_dir() / f"{cid}.json", {"messages": []})
        return {"meta": meta, "messages": data.get("messages", [])}

    def add_messages(self, cid: str, msgs: list[dict]) -> bool:
        """追加消息（{role,content,has_image?,perspectives?,data_materials?}）。content 取文本、图片只标记 has_image 不入库。
        首条 user 消息会自动给默认标题的会话起名。"""
        cid = self._safe_cid(cid)
        if not cid:
            return False
        path = self._conv_dir() / f"{cid}.json"
        clean: list[dict] = []
        for m in (msgs or []):
            if m.get("role") not in ("user", "assistant"):
                continue
            row = {
                "role": m["role"],
                "content": str(m.get("content", ""))[:20000],
                "ts": m.get("ts") or self.now_str(),
                "has_image": bool(m.get("has_image")),
            }
            if m["role"] == "assistant" and isinstance(m.get("perspectives"), list):
                perspectives = []
                for item in m["perspectives"][:4]:
                    if not isinstance(item, dict):
                        continue
                    perspective_id = str(item.get("id") or "")[:40]
                    name = str(item.get("name") or "")[:40]
                    version = str(item.get("version") or "")[:24]
                    if perspective_id and name:
                        perspectives.append({
                            "id": perspective_id,
                            "name": name,
                            "version": version,
                        })
                if perspectives:
                    row["perspectives"] = perspectives
            if m["role"] == "assistant" and isinstance(m.get("data_materials"), dict):
                # 路由层已按白名单清洗；这里再用 JSON 往返断开可变引用，
                # 并限制为固定深度的材料报告，不接受额外消息正文。
                report = m["data_materials"]
                row["data_materials"] = {
                    "selected_ids": list(report.get("selected_ids") or [])[:4],
                    "max_selected": 4,
                    "model_used": bool(report.get("model_used")),
                    "required": list(report.get("required") or [])[:3],
                    "optional": list(report.get("optional") or [])[:6],
                }
            clean.append(row)
        if not clean:
            return False
        with self._lock:
            data = self._read_json(path, {"id": cid, "messages": []})
            data.setdefault("messages", []).extend(clean)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            mc = len(data["messages"])
        first_user = next((m["content"] for m in clean if m["role"] == "user"), "")

        def _upd(idx):
            for c in idx:
                if c.get("id") == cid:
                    c["msg_count"] = mc
                    c["updated_at"] = self.now_str()
                    if c.get("title") in ("", "新对话") and first_user:
                        c["title"] = first_user.strip().replace("\n", " ")[:18]
                    break
        self._update_conv_index(_upd)
        return True

    def update_conversation(self, cid: str, **fields) -> bool:
        """改标题/分组（title/group）。"""
        cid = self._safe_cid(cid)
        if not cid:
            return False

        def _upd(idx):
            for c in idx:
                if c.get("id") == cid:
                    if "title" in fields:
                        c["title"] = str(fields["title"])[:40]
                    if "group" in fields:
                        c["group"] = str(fields["group"])[:40]
                    c["updated_at"] = self.now_str()
                    break
        self._update_conv_index(_upd)
        return True

    def delete_conversation(self, cid: str) -> bool:
        cid = self._safe_cid(cid)
        if not cid:
            return False
        self._update_conv_index(lambda idx: idx.__setitem__(slice(None), [c for c in idx if c.get("id") != cid]))
        try:
            (self._conv_dir() / f"{cid}.json").unlink()
        except OSError:
            pass
        return True

    # ---------- 定时任务运行/推送状态（可靠性可见化）----------
    # 单文件 jobs_state.json：每个 job_id 记最近一次 运行/推送 的时间与成败，
    # 供 /api/status 与设置页展示"上次几点推成功/失败"，并支撑漏推补偿判定。

    def get_job_states(self) -> dict:
        return self._read_json(self.root / "jobs_state.json", {})

    def record_job(self, job_id: str, **fields) -> None:
        """合并更新某任务状态（如 last_run/last_ok/last_error/last_push_time/
        last_push_ok/last_push_error/last_push_date/last_finished/last_duration_ms）。
        仅覆盖传入字段，供 task-center-v1 展示最近结果与耗时。"""
        with self._lock:
            path = self.root / "jobs_state.json"
            states = self._read_json(path, {})
            cur = states.get(job_id, {})
            cur.update(fields)
            states[job_id] = cur
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)

    def reconcile_interrupted_jobs(self) -> list[str]:
        """进程启动时将上一进程遗留的 ``running`` 收口为 ``interrupted``。

        这是启动期专用变更；运行中的查询接口不得调用，避免把当前真正执行的
        任务误标为中断。不删历史字段，仅补齐可审计的终态与结束时间。
        """
        with self._lock:
            path = self.root / "jobs_state.json"
            states = self._read_json(path, {})
            if not isinstance(states, dict):
                return []
            interrupted: list[str] = []
            finished = self.now_str()
            for job_id, current in states.items():
                if not isinstance(current, dict) or current.get("last_status") != "running":
                    continue
                current["last_status"] = "interrupted"
                current["last_finished"] = finished
                current["last_error"] = "process_restarted_before_completion"
                interrupted.append(str(job_id))
            if not interrupted:
                return []
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            return interrupted

    # ---------- 情绪历史 ----------

    @property
    def _sentiment_path(self) -> Path:
        return self.root / "sentiment" / "history.json"

    def save_sentiment(self, snapshot: dict) -> None:
        history = self._read_json(self._sentiment_path, [])
        history = [h for h in history if h.get("date") != snapshot.get("date")]
        history.append(snapshot)
        history.sort(key=lambda h: h.get("date", ""))
        self._write_json(self._sentiment_path, history[-250:])

    def sentiment_history(self, days: int = 30) -> list[dict]:
        history = self._read_json(self._sentiment_path, [])
        return history[-days:]

    # ---------- 题材赚钱效应快照（市场主线多日累积） ----------

    @property
    def _sector_path(self) -> Path:
        return self.root / "sentiment" / "sectors.json"

    @staticmethod
    def _snapshot_completeness(rec: dict) -> tuple:
        """快照完整度评分，用于同日覆盖前比较。越大越完整。
        (有资金流的题材数, 题材总数) —— 降级快照(新浪兜底/资金流接口失败)main_flow 全 None、
        题材数也常更少，完整度更低，不应覆盖收盘后的完整快照。"""
        themes = rec.get("themes") or []
        flow_count = sum(1 for t in themes if t.get("main_flow") is not None)
        return (flow_count, len(themes))

    def save_sector_snapshot(self, date: str, themes: list[dict]) -> None:
        """按日存当日 top 题材赚钱效应快照，供市场主线计算持续性。

        同一 data_date 重复调用时按完整度比较：仅当新快照完整度 >= 已有同日快照才覆盖。
        否则东财链路抖动走新浪兜底/资金流接口失败的降级快照(main_flow 全 None)会把
        收盘后已落库的完整快照覆盖掉，污染主线历史(flow_days 归零、avg_money_score 失真)。"""
        new_rec = {"date": date, "themes": themes}
        history = self._read_json(self._sector_path, [])
        existing = next((h for h in history if h.get("date") == date), None)
        if existing is not None and (
            self._snapshot_completeness(new_rec) < self._snapshot_completeness(existing)
        ):
            # 本次为降级快照，保留更完整的旧快照，不覆盖
            return
        history = [h for h in history if h.get("date") != date]
        history.append(new_rec)
        history.sort(key=lambda h: h.get("date", ""))
        self._write_json(self._sector_path, history[-120:])

    def sector_history(self, days: int = 5) -> list[dict]:
        history = self._read_json(self._sector_path, [])
        return history[-days:]

    # ---------- DIY 研究工作流模板 ----------

    @property
    def _diy_path(self) -> Path:
        return self.root / "knowledge" / "diy_templates.json"

    def diy_templates(self) -> list[dict]:
        return self._read_json(self._diy_path, [])

    def add_diy_template(self, name: str, target_type: str, prompt_template: str) -> str:
        items = self.diy_templates()
        tid = f"d_{int(time.time() * 1000)}"
        items.append({
            "id": tid,
            "name": str(name).strip()[:60] or "未命名模板",
            "target_type": target_type if target_type in ("stock", "theme", "free") else "free",
            "prompt_template": str(prompt_template).strip()[:4000],
            "created_at": self.now_str(),
        })
        self._write_json(self._diy_path, items)
        return tid

    def get_diy_template(self, tid: str) -> dict | None:
        return next((t for t in self.diy_templates() if t.get("id") == tid), None)

    def delete_diy_template(self, tid: str) -> bool:
        items = self.diy_templates()
        kept = [t for t in items if t.get("id") != tid]
        if len(kept) == len(items):
            return False
        self._write_json(self._diy_path, kept)
        return True

    # ---------- 知识库 ----------

    @property
    def _knowledge_path(self) -> Path:
        return self.root / "knowledge" / "items.json"

    @property
    def _knowledge_audit_path(self) -> Path:
        return self.root / "knowledge" / "audit.jsonl"

    @property
    def _knowledge_intent_path(self) -> Path:
        return self.root / "knowledge" / "transaction.intent.json"

    @staticmethod
    def _string_list(value) -> list[str]:
        """将来源 ID 规范为去重字符串列表。

        旧数据的缺失不能被补成虚构来源，因此 None/畸形值都只会得到空列表。
        """
        if not isinstance(value, (list, tuple, set)):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = str(raw or "").strip()
            if item and item not in seen:
                seen.add(item)
                result.append(item[:200])
        return result

    @staticmethod
    def _sample_count(value) -> int:
        """样本数必须是真正的非负整数，禁止浮点截断或 NaN/inf。"""
        if isinstance(value, bool):
            raise ValueError("sample_count 必须是非负整数")
        if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
            raise ValueError("sample_count 必须是非负整数")
        if isinstance(value, str) and not value.strip().isdigit():
            raise ValueError("sample_count 必须是非负整数")
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("sample_count 必须是非负整数") from None
        if result < 0:
            raise ValueError("sample_count 必须是非负整数")
        return result

    def _normalise_knowledge_item(self, raw: dict) -> dict:
        """Read-time fail-closed migration to KnowledgeRule.v1.

        历史 items.json 没有审批状态与来源。读时只标记
        ``legacy_unverified``，不回写、不推测样本数，避免把“存在过”伪装成
        “已验证”。
        """
        item = dict(raw or {})
        had_status = item.get("status") in KNOWLEDGE_STATUSES
        status = item.get("status") if had_status else "legacy_unverified"
        source_report_ids = self._string_list(item.get("source_report_ids"))
        evidence_ids = self._string_list(item.get("evidence_ids"))
        try:
            sample_count = self._sample_count(item.get("sample_count", 0))
        except ValueError:
            sample_count = 0

        # 一条声称 approved 但缺少 v1 必要的来源/样本信息时，
        # 安全地降为 legacy_unverified，绝不让它进入 prompt。
        origin = str(item.get("origin") or ("legacy_import" if not had_status else "unknown"))[:80]
        archive_key = str(item.get("archive_key") or "")[:80]
        archived_event = item.get("archived_event")
        if not isinstance(archived_event, dict):
            archived_event = None
        has_source = bool(
            source_report_ids
            or evidence_ids
            or origin == "explicit_user_input"
            or (origin == "sentiment_event_archive" and archive_key)
        )
        if status == "approved" and (sample_count < 1 or not has_source):
            status = "legacy_unverified"

        item.update({
            "schema": KNOWLEDGE_RULE_SCHEMA,
            "id": str(item.get("id") or ""),
            "rule_id": str(item.get("rule_id") or item.get("id") or ""),
            "category": (
                item.get("category") if item.get("category") in KNOWLEDGE_CATEGORIES
                else "复盘笔记"
            ),
            "title": str(item.get("title") or "")[:120],
            "content": str(item.get("content") or "")[:8000],
            "status": status,
            "version": max(1, int(item.get("version") or 1))
            if str(item.get("version") or "1").isdigit() else 1,
            "origin": origin,
            "archive_key": archive_key or None,
            "archived_event": archived_event,
            "source_report_ids": source_report_ids,
            "evidence_ids": evidence_ids,
            "sample_count": sample_count,
            "created_at": str(item.get("created_at") or ""),
            "created_by": str(item.get("created_by") or ("legacy" if not had_status else "system")),
            "approved_at": item.get("approved_at"),
            "approved_by": item.get("approved_by"),
            "rejected_at": item.get("rejected_at"),
            "rejected_by": item.get("rejected_by"),
            "rejection_reason": item.get("rejection_reason"),
        })
        return item

    def knowledge_items(self) -> list[dict]:
        raw = self._read_json(self._knowledge_path, [])
        if not isinstance(raw, list):
            return []
        return [self._normalise_knowledge_item(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _knowledge_has_source(item: dict) -> bool:
        return bool(
            item.get("source_report_ids")
            or item.get("evidence_ids")
            or item.get("origin") == "explicit_user_input"
            or (
                item.get("origin") == "sentiment_event_archive"
                and item.get("archive_key")
            )
        )

    def _validate_knowledge_approval(self, item: dict) -> None:
        try:
            samples = self._sample_count(item.get("sample_count", 0))
        except ValueError:
            samples = 0
        if samples < 1:
            raise ValueError("批准失败：sample_count 必须大于等于 1")
        if not self._knowledge_has_source(item):
            raise ValueError("批准失败：必须绑定来源报告、证据或 explicit_user_input")
        missing: list[str] = []
        for report_id in item.get("source_report_ids") or []:
            parts = str(report_id).split(":")
            if len(parts) != 3 or parts[0] != "report" or not self.get_report(parts[1], parts[2]):
                missing.append(str(report_id))
        evidence_ids = item.get("evidence_ids") or []
        if evidence_ids:
            try:
                from ..data.evidence_store import EvidenceStore  # noqa: PLC0415

                evidence_store = EvidenceStore(self.root / "evidence" / "market_data.sqlite3")
                for evidence_id in evidence_ids:
                    if evidence_store.get_point(str(evidence_id)) is None:
                        missing.append(str(evidence_id))
            except Exception:  # noqa: BLE001 — 审批证据库不可用必须 fail closed
                missing.extend(str(value) for value in evidence_ids)
        if missing:
            raise ValueError("批准失败：来源/证据不存在：" + "、".join(dict.fromkeys(missing)))
        if item.get("origin") != "explicit_user_input":
            source_count = (
                len(set(item.get("source_report_ids") or []))
                + len(set(evidence_ids))
                + int(bool(
                    item.get("origin") == "sentiment_event_archive"
                    and item.get("archive_key")
                ))
            )
            if samples > source_count:
                raise ValueError("批准失败：sample_count 不得超过已核验来源数")

    def _append_knowledge_audit(self, event: dict) -> None:
        """只追加审计记录；不使用 items.json 的覆盖写语义。"""
        rec = {
            "event_id": f"ka_{time.time_ns():x}",
            "at": self.now_str(),
            **dict(event or {}),
        }
        path = self._knowledge_audit_path
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(path.parent, 0o700)
            except OSError:
                pass
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_dir(path.parent)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def _knowledge_transaction(self, func, *args, **kwargs):
        """以 durable intent 保证知识状态与审计跨进程崩溃仍一致。"""
        with self._lock:
            self._recover_knowledge_transaction()
            items_path = self._knowledge_path
            audit_path = self._knowledge_audit_path
            before_items = items_path.read_bytes() if items_path.exists() else None
            before_audit_size = audit_path.stat().st_size if audit_path.exists() else None
            intent = {
                "schema": "knowledge-transaction-intent.v1",
                "transaction_id": f"kt_{time.time_ns():x}",
                "phase": "prepared",
                "prepared_at": self.now_str(),
                "items_existed": before_items is not None,
                "items_before_b64": (
                    base64.b64encode(before_items).decode("ascii")
                    if before_items is not None else None
                ),
                "audit_existed": before_audit_size is not None,
                "audit_size_before": before_audit_size,
            }
            self._write_json(self._knowledge_intent_path, intent)
            try:
                result = func(*args, **kwargs)
                intent["phase"] = "committed"
                intent["committed_at"] = self.now_str()
                self._write_json(self._knowledge_intent_path, intent)
                self._delete_durable(self._knowledge_intent_path)
                return result
            except Exception:
                self._rollback_knowledge_intent(intent)
                self._delete_durable(self._knowledge_intent_path)
                raise

    def _rollback_knowledge_intent(self, intent: dict) -> None:
        items_path = self._knowledge_path
        audit_path = self._knowledge_audit_path
        if not bool(intent.get("items_existed")):
            self._delete_durable(items_path)
        else:
            encoded = intent.get("items_before_b64")
            if not isinstance(encoded, str):
                raise RuntimeError("知识事务 intent 缺少状态快照")
            before_items = base64.b64decode(encoded.encode("ascii"), validate=True)
            items_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = items_path.with_suffix(items_path.suffix + ".rollback")
            with tmp.open("wb") as handle:
                handle.write(before_items)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, items_path)
            self._fsync_dir(items_path.parent)

        if not bool(intent.get("audit_existed")):
            self._delete_durable(audit_path)
        elif audit_path.exists():
            before_size = int(intent.get("audit_size_before") or 0)
            with audit_path.open("r+b") as handle:
                handle.truncate(before_size)
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_dir(audit_path.parent)

    def _recover_knowledge_transaction(self) -> dict:
        """启动/下一次写入前恢复未完成 intent；committed 只清理标记。"""
        with self._lock:
            intent_path = self._knowledge_intent_path
            if not intent_path.exists():
                return {"recovered": False}
            intent = self._read_json(intent_path, {})
            if not isinstance(intent, dict) or intent.get("schema") != "knowledge-transaction-intent.v1":
                raise RuntimeError("知识事务 intent 损坏，拒绝静默继续")
            if intent.get("phase") != "committed":
                self._rollback_knowledge_intent(intent)
            self._delete_durable(intent_path)
            return {
                "recovered": True,
                "transaction_id": intent.get("transaction_id"),
                "phase": intent.get("phase"),
            }

    def add_knowledge(self, *args, **kwargs) -> str:
        return self._knowledge_transaction(self._add_knowledge_unlocked, *args, **kwargs)

    def _add_knowledge_unlocked(
        self,
        category: str,
        title: str,
        content: str,
        *,
        status: str = "approved",
        origin: str = "explicit_user_input",
        source_report_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        sample_count: int | None = None,
        created_by: str = "user",
        archive_key: str | None = None,
        archived_event: dict | None = None,
    ) -> str:
        """新增 KnowledgeRule.v1。

        三参数旧调用保持兼容：用户在 UI/API 明确点击保存，视为
        ``explicit_user_input``，默认已批准、样本数 1。自动流程必须显式传
        ``status="draft"``，不得借用这个默认值进入 prompt。
        """
        if status not in {"draft", "approved"}:
            raise ValueError("新知识状态只能是 draft 或 approved")
        title = str(title or "").strip()[:120]
        content = str(content or "").strip()[:8000]
        if not title or not content:
            raise ValueError("知识标题与内容不能为空")
        src_reports = self._string_list(source_report_ids)
        src_evidence = self._string_list(evidence_ids)
        if sample_count is None:
            # 只有明确的用户录入可以使用一次“用户声明样本”。
            # 其他来源按实际绑定 ID 数量计，空来源保持 0。
            sample_count = (
                1 if origin == "explicit_user_input"
                else len(set(src_reports + src_evidence))
            )
        samples = self._sample_count(sample_count)

        now = self.now_str()
        kid = f"k_{time.time_ns():x}"
        item = {
            "schema": KNOWLEDGE_RULE_SCHEMA,
            "id": kid,
            "rule_id": kid,
            "category": category if category in KNOWLEDGE_CATEGORIES else "复盘笔记",
            "title": title,
            "content": content,
            "status": status,
            "version": 1,
            "origin": str(origin or "unknown")[:80],
            "archive_key": str(archive_key or "")[:80] or None,
            "archived_event": dict(archived_event) if isinstance(archived_event, dict) else None,
            "source_report_ids": src_reports,
            "evidence_ids": src_evidence,
            "sample_count": samples,
            "created_at": now,
            "created_by": str(created_by or "system")[:80],
            "approved_at": now if status == "approved" else None,
            "approved_by": str(created_by or "user")[:80] if status == "approved" else None,
            "rejected_at": None,
            "rejected_by": None,
            "rejection_reason": None,
        }
        if status == "approved":
            self._validate_knowledge_approval(item)
        items = self.knowledge_items()
        items.append(item)
        self._write_json(self._knowledge_path, items)
        self._append_knowledge_audit({
            "action": "create",
            "knowledge_id": kid,
            "rule_id": kid,
            "version": 1,
            "actor": item["created_by"],
            "from_status": None,
            "to_status": status,
            "source_report_ids": src_reports,
            "evidence_ids": src_evidence,
            "sample_count": samples,
        })
        return kid

    @staticmethod
    def _sentiment_archive_snapshot(raw: dict) -> tuple[str, str, str, dict]:
        """把页面选中的事件收敛为有界、可读、可去重的留档快照。"""
        if not isinstance(raw, dict):
            raise ValueError("舆情事件必须是对象")

        def text(value, limit: int) -> str:
            return str(value or "").strip()[:limit]

        title = text(raw.get("title"), 120)
        body = text(raw.get("content"), 6000)
        if not title and not body:
            raise ValueError("舆情事件标题与正文不能同时为空")
        if not title:
            title = body[:80]

        published_at = text(raw.get("published_at") or raw.get("time"), 80)
        fetched_at = text(raw.get("fetched_at"), 80)
        source = text(raw.get("source"), 120)
        event_id = text(raw.get("event_id"), 160)
        content_hash = text(raw.get("content_hash"), 160)

        matches: list[dict] = []
        a_share = raw.get("a_share") if isinstance(raw.get("a_share"), dict) else {}
        for row in list(a_share.get("matches") or [])[:24]:
            if not isinstance(row, dict):
                continue
            matches.append({
                "kind": text(row.get("kind"), 24),
                "code": text(row.get("code"), 24),
                "name": text(row.get("name"), 80),
            })

        sources: list[dict] = []
        for row in list(raw.get("observations") or [])[:12]:
            if not isinstance(row, dict):
                continue
            source_url = text(row.get("source_url"), 1000)
            if not source_url.lower().startswith(("http://", "https://")):
                source_url = ""
            sources.append({
                "source_id": text(row.get("source_id"), 80),
                "publisher_name": text(row.get("publisher_name"), 120),
                "collection_platform_name": text(row.get("collection_platform_name"), 120),
                "published_at": text(row.get("published_at"), 80),
                "fetched_at": text(row.get("fetched_at"), 80),
                "source_url": source_url or None,
            })

        canonical = json.dumps({
            "title": title,
            "content": body,
            "published_at": published_at,
            "source": source,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        archive_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot = {
            "schema": "sentiment-event-archive.v1",
            "archive_key": archive_key,
            "event_id": event_id or None,
            "content_hash": content_hash or None,
            "title": title,
            "content": body,
            "published_at": published_at or None,
            "fetched_at": fetched_at or None,
            "source": source or None,
            "matches": matches,
            "sources": sources,
        }

        lines = [body or title]
        if published_at:
            lines.append(f"\n事件时间：{published_at}")
        if fetched_at:
            lines.append(f"抓取时间：{fetched_at}")
        if source:
            lines.append(f"聚合来源：{source}")
        if matches:
            labels = []
            for row in matches:
                label = row.get("name") or row.get("code") or ""
                if row.get("code") and row.get("name"):
                    label = f"{row['name']}（{row['code']}）"
                if label:
                    labels.append(f"{row.get('kind') or '关联'}：{label}")
            if labels:
                lines.append("A 股关联：" + "；".join(labels))
        if sources:
            lines.append("\n采集证据：")
            for row in sources:
                source_label = row.get("collection_platform_name") or row.get("publisher_name") or row.get("source_id") or "来源未提供"
                suffix = f" · {row['source_url']}" if row.get("source_url") else ""
                lines.append(f"- {source_label}{suffix}")
        lines.append("\n留档说明：来自舆情事件页面的人工留档；仅保存事件与映射事实，不判断利多利空或新闻因果。")
        return archive_key, title, "\n".join(lines)[:8000], snapshot

    def archive_sentiment_event(self, event: dict) -> dict:
        """幂等保存一条舆情事件为知识草稿，避免刷新列表覆盖用户选中的事实。"""
        return self._knowledge_transaction(self._archive_sentiment_event_unlocked, event)

    def _archive_sentiment_event_unlocked(self, event: dict) -> dict:
        archive_key, title, content, snapshot = self._sentiment_archive_snapshot(event)
        for item in self.knowledge_items():
            if (
                item.get("origin") == "sentiment_event_archive"
                and item.get("archive_key") == archive_key
            ):
                return {"id": item.get("id"), "created": False, "item": item}

        kid = self._add_knowledge_unlocked(
            "舆情留档",
            title,
            content,
            status="draft",
            origin="sentiment_event_archive",
            sample_count=1,
            created_by="user:web",
            archive_key=archive_key,
            archived_event=snapshot,
        )
        item = next((row for row in self.knowledge_items() if row.get("id") == kid), None)
        return {"id": kid, "created": True, "item": item}

    def review_queue(self) -> list[dict]:
        """待处理队列：自动 draft 与无法验证的历史条目。"""
        return [
            item for item in self.knowledge_items()
            if item.get("status") in {"draft", "legacy_unverified"}
        ]

    def approve(self, *args, **kwargs) -> dict:
        return self._knowledge_transaction(self._approve_unlocked, *args, **kwargs)

    def _approve_unlocked(
        self,
        kid: str,
        *,
        approved_by: str = "user",
        source_report_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        sample_count: int | None = None,
    ) -> dict:
        items = self.knowledge_items()
        idx = next((i for i, item in enumerate(items) if item.get("id") == kid), None)
        if idx is None:
            raise KeyError("知识条目不存在")
        current = dict(items[idx])
        if current.get("status") == "approved":
            return current
        if current.get("status") not in {"draft", "legacy_unverified"}:
            raise ValueError(f"状态 {current.get('status')} 不可批准")

        if source_report_ids is not None:
            current["source_report_ids"] = self._string_list(source_report_ids)
        if evidence_ids is not None:
            current["evidence_ids"] = self._string_list(evidence_ids)
        if sample_count is not None:
            current["sample_count"] = self._sample_count(sample_count)
        self._validate_knowledge_approval(current)
        previous_status = current["status"]
        current.update({
            "status": "approved",
            "approved_at": self.now_str(),
            "approved_by": str(approved_by or "user")[:80],
            "rejected_at": None,
            "rejected_by": None,
            "rejection_reason": None,
        })
        items[idx] = current
        self._write_json(self._knowledge_path, items)
        self._append_knowledge_audit({
            "action": "approve",
            "knowledge_id": kid,
            "rule_id": current.get("rule_id"),
            "version": current.get("version"),
            "actor": current["approved_by"],
            "from_status": previous_status,
            "to_status": "approved",
            "source_report_ids": current.get("source_report_ids", []),
            "evidence_ids": current.get("evidence_ids", []),
            "sample_count": current.get("sample_count", 0),
        })
        return current

    def reject(self, *args, **kwargs) -> dict:
        return self._knowledge_transaction(self._reject_unlocked, *args, **kwargs)

    def _reject_unlocked(self, kid: str, reason: str, *, rejected_by: str = "user") -> dict:
        reason = str(reason or "").strip()[:500]
        if not reason:
            raise ValueError("拒绝必须填写原因")
        items = self.knowledge_items()
        idx = next((i for i, item in enumerate(items) if item.get("id") == kid), None)
        if idx is None:
            raise KeyError("知识条目不存在")
        current = dict(items[idx])
        if current.get("status") == "rejected":
            return current
        if current.get("status") not in {"draft", "legacy_unverified"}:
            raise ValueError(f"状态 {current.get('status')} 不可拒绝")
        previous_status = current["status"]
        current.update({
            "status": "rejected",
            "rejected_at": self.now_str(),
            "rejected_by": str(rejected_by or "user")[:80],
            "rejection_reason": reason,
        })
        items[idx] = current
        self._write_json(self._knowledge_path, items)
        self._append_knowledge_audit({
            "action": "reject",
            "knowledge_id": kid,
            "rule_id": current.get("rule_id"),
            "version": current.get("version"),
            "actor": current["rejected_by"],
            "from_status": previous_status,
            "to_status": "rejected",
            "reason": reason,
        })
        return current

    def supersede(self, *args, **kwargs) -> dict:
        return self._knowledge_transaction(self._supersede_unlocked, *args, **kwargs)

    def _supersede_unlocked(
        self,
        kid: str,
        *,
        title: str | None = None,
        content: str | None = None,
        category: str | None = None,
        superseded_by: str = "user",
        origin: str = "explicit_user_input",
        source_report_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        sample_count: int | None = None,
        reason: str = "",
    ) -> dict:
        """以新版本原子替代旧规则；旧版内容/版本号永不改写。

        替代是用户明确操作，新版本必须同时满足批准门槛，从而不会
        出现“旧版已下线、新版还是 draft”的 prompt 空窗。
        """
        items = self.knowledge_items()
        idx = next((i for i, item in enumerate(items) if item.get("id") == kid), None)
        if idx is None:
            raise KeyError("知识条目不存在")
        old = dict(items[idx])
        if old.get("status") in {"rejected", "superseded"}:
            raise ValueError(f"状态 {old.get('status')} 不可替代")

        new_title = str(title if title is not None else old.get("title") or "").strip()[:120]
        new_content = str(content if content is not None else old.get("content") or "").strip()[:8000]
        if not new_title or not new_content:
            raise ValueError("替代版本的标题与内容不能为空")
        src_reports = self._string_list(source_report_ids)
        src_evidence = self._string_list(evidence_ids)
        if sample_count is None:
            sample_count = (
                1 if origin == "explicit_user_input"
                else len(set(src_reports + src_evidence))
            )
        samples = self._sample_count(sample_count)

        now = self.now_str()
        new_id = f"k_{time.time_ns():x}"
        replacement = {
            "schema": KNOWLEDGE_RULE_SCHEMA,
            "id": new_id,
            "rule_id": old.get("rule_id") or old.get("id"),
            "category": category if category in KNOWLEDGE_CATEGORIES else old.get("category", "复盘笔记"),
            "title": new_title,
            "content": new_content,
            "status": "approved",
            "version": int(old.get("version") or 1) + 1,
            "origin": str(origin or "unknown")[:80],
            "source_report_ids": src_reports,
            "evidence_ids": src_evidence,
            "sample_count": samples,
            "created_at": now,
            "created_by": str(superseded_by or "user")[:80],
            "approved_at": now,
            "approved_by": str(superseded_by or "user")[:80],
            "rejected_at": None,
            "rejected_by": None,
            "rejection_reason": None,
            "supersedes_id": old.get("id"),
        }
        self._validate_knowledge_approval(replacement)
        previous_status = old.get("status")
        old.update({
            "status": "superseded",
            "superseded_at": now,
            "superseded_by": str(superseded_by or "user")[:80],
            "superseded_reason": str(reason or "")[:500],
            "replacement_id": new_id,
        })
        items[idx] = old
        items.append(replacement)
        self._write_json(self._knowledge_path, items)
        self._append_knowledge_audit({
            "action": "supersede",
            "knowledge_id": old.get("id"),
            "replacement_id": new_id,
            "rule_id": replacement.get("rule_id"),
            "version": replacement.get("version"),
            "actor": replacement["created_by"],
            "from_status": previous_status,
            "to_status": "superseded",
            "replacement_status": "approved",
            "reason": str(reason or "")[:500],
        })
        return replacement

    def delete_knowledge(self, kid: str) -> bool:
        return self._knowledge_transaction(self._delete_knowledge_unlocked, kid)

    def _delete_knowledge_unlocked(self, kid: str) -> bool:
        items = self.knowledge_items()
        kept = [i for i in items if i.get("id") != kid]
        if len(kept) == len(items):
            return False
        self._write_json(self._knowledge_path, kept)
        self._append_knowledge_audit({
            "action": "delete",
            "knowledge_id": kid,
            "actor": "user",
        })
        return True

    def knowledge_digest(self, max_chars: int = 2500) -> str:
        """注入提示词用的知识库摘要：只允许已批准规则进入 prompt。"""
        items = [i for i in self.knowledge_items() if i.get("status") == "approved"]
        if not items:
            return ""
        by_cat: dict[str, list[str]] = {}
        for item in items:
            line = f"- {item.get('title', '')}：{item.get('content', '')[:200]}"
            by_cat.setdefault(item.get("category", "其他"), []).append(line)
        parts = []
        for cat in KNOWLEDGE_CATEGORIES:
            if cat in by_cat:
                parts.append(f"◆ {cat}：\n" + "\n".join(by_cat[cat][-6:]))
        return "\n".join(parts)[:max_chars]

    # ---------- 持仓 ----------

    @property
    def _holdings_path(self) -> Path:
        return self.root / "holdings.json"

    @property
    def _holdings_risk_path(self) -> Path:
        return self.root / "holdings_risk.json"

    @property
    def _holdings_risk_events_path(self) -> Path:
        return self.root / "holding_events" / "risk.v1.jsonl"

    def holdings(self) -> list[dict]:
        return self._read_json(self._holdings_path, [])

    @staticmethod
    def _normalise_holding_risk_plan(value: object, *, previous: dict | None = None) -> dict | None:
        """校验用户确认的风险计划事实；历史评估结果不进入 holdings.json。"""
        if value in (None, ""):
            return dict(previous) if isinstance(previous, dict) else None
        if not isinstance(value, dict):
            raise ValueError("持仓风险计划应为对象")
        period = str(value.get("period") or "").strip()
        if period not in {"ultra", "short", "swing"}:
            raise ValueError("持仓周期应为 ultra、short 或 swing")
        buy_date = str(value.get("buy_date") or "").strip()
        try:
            parsed_buy_date = datetime.strptime(buy_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("买入日期格式应为 YYYY-MM-DD") from None
        if parsed_buy_date.date() > now_cn().date():
            raise ValueError("买入日期不能晚于今天")

        def optional_price(key: str, label: str) -> float | None:
            raw = value.get(key)
            if raw in (None, ""):
                return None
            try:
                parsed = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{label}不是有效数字") from None
            if not math.isfinite(parsed) or parsed <= 0:
                raise ValueError(f"{label}必须大于 0")
            return parsed

        target_mode = str(value.get("target_mode") or "reminder").strip()
        if target_mode not in {"reminder", "ordinary_trigger"}:
            raise ValueError("目标价模式应为 reminder 或 ordinary_trigger")
        previous_version = int((previous or {}).get("version") or 0)
        try:
            version = int(value.get("version") or previous_version or 1)
        except (TypeError, ValueError):
            version = max(1, previous_version)
        try:
            confirmed = int(float(value.get("confirmed_reduction_pct") or 0))
        except (TypeError, ValueError):
            raise ValueError("已确认降低比例不是有效数字") from None
        if confirmed not in {0, 25, 33, 50, 67, 100}:
            raise ValueError("已确认降低比例应为 0、25、33、50、67 或 100")
        created_at = str(value.get("created_at") or (previous or {}).get("created_at") or now_cn().isoformat(timespec="seconds"))
        return {
            "schema_version": "holding-risk-plan-v1",
            "version": max(1, version),
            "period": period,
            "buy_date": buy_date,
            "customer_risk_price": optional_price("customer_risk_price", "客户风险线"),
            "customer_target_price": optional_price("customer_target_price", "客户目标价"),
            "target_mode": target_mode,
            "confirmed_reduction_pct": confirmed,
            "created_at": created_at[:32],
            "updated_at": now_cn().isoformat(timespec="seconds"),
        }

    @property
    def _portfolio_meta_path(self) -> Path:
        return self.root / "portfolio_meta.json"

    def portfolio_meta(self) -> dict:
        value = self._read_json(self._portfolio_meta_path, {})
        return value if isinstance(value, dict) else {}

    def save_holdings(self, holdings: list[dict], total_asset: float | None = None) -> None:
        # 畸形条目抛 ValueError 拒绝整批保存（路由层转 400），
        # 静默跳过会让用户以为已保存、实际丢条目。
        if not isinstance(holdings, list):
            raise ValueError("持仓必须是列表")
        if total_asset not in (None, ""):
            try:
                total_asset = float(total_asset)
            except (TypeError, ValueError):
                raise ValueError("账户总资产不是有效数字") from None
            if not math.isfinite(total_asset) or total_asset <= 0:
                raise ValueError("账户总资产必须大于 0")
        else:
            total_asset = None
        existing_rows = self.holdings()
        existing_by_code = {
            str(row.get("code") or "").strip().zfill(6): row
            for row in existing_rows if isinstance(row, dict) and row.get("code")
        }
        cleaned = []
        seen_codes: set[str] = set()
        for h in holdings[:50]:
            if not isinstance(h, dict):
                raise ValueError("持仓条目应为对象")
            code = str(h.get("code", "")).strip()
            if not code:
                continue
            normalised_code = code.zfill(6)
            if normalised_code in seen_codes:
                raise ValueError(f"持仓代码 {normalised_code} 重复")
            try:
                cost = float(h.get("cost") or 0)
                shares = float(h.get("shares") or 0)
            except (TypeError, ValueError):
                raise ValueError(f"持仓 {code} 的成本/数量不是有效数字") from None
            if not math.isfinite(cost) or not math.isfinite(shares):
                raise ValueError(f"持仓 {normalised_code} 的成本/数量必须是有限数字")
            if cost <= 0:
                raise ValueError(f"持仓 {normalised_code} 的成本必须大于 0")
            if shares <= 0:
                raise ValueError(f"持仓 {normalised_code} 的数量必须大于 0")
            weight_pct = h.get("weight_pct")
            if weight_pct not in (None, ""):
                try:
                    weight_pct = float(weight_pct)
                except (TypeError, ValueError):
                    raise ValueError(f"持仓 {normalised_code} 的仓位权重不是有效数字") from None
                if not math.isfinite(weight_pct) or not 0 <= weight_pct <= 100:
                    raise ValueError(f"持仓 {normalised_code} 的仓位权重应为 0–100%")
            else:
                weight_pct = None
            market_value = h.get("market_value")
            if market_value in (None, ""):
                market_value = cost * shares
            try:
                market_value = float(market_value)
            except (TypeError, ValueError):
                market_value = cost * shares
            if not math.isfinite(market_value) or market_value <= 0:
                raise ValueError(f"持仓 {normalised_code} 的市值必须是正的有限数字")
            if weight_pct is None and total_asset is not None:
                weight_pct = market_value / total_asset * 100
                if weight_pct > 100:
                    raise ValueError(f"持仓 {normalised_code} 市值超过账户总资产")
            seen_codes.add(normalised_code)
            previous_row = existing_by_code.get(normalised_code) or {}
            previous_plan = previous_row.get("risk_plan") if isinstance(previous_row, dict) else None
            if isinstance(previous_plan, dict):
                previous_cost = float(previous_row.get("cost") or 0)
                previous_shares = float(previous_row.get("shares") or 0)
                if abs(cost - previous_cost) > 1e-9 or abs(shares - previous_shares) > 1e-9:
                    raise ValueError(f"持仓 {normalised_code} 已建立风险计划，成本或数量变更请使用补仓/重规划")
                risk_plan = dict(previous_plan)
            else:
                risk_plan = self._normalise_holding_risk_plan(h.get("risk_plan"), previous=None)
            cleaned_row = {
                    "code": normalised_code,
                    "name": str(h.get("name", ""))[:30],
                    "cost": cost,
                    "shares": shares,
                    "industry": str(h.get("industry") or h.get("sector") or "")[:80],
                    "weight_pct": weight_pct,
                    "market_value": market_value,
                    "note": str(h.get("note", ""))[:200],
            }
            if risk_plan is not None:
                cleaned_row["risk_plan"] = risk_plan
            cleaned.append(cleaned_row)
        if total_asset is not None:
            total_market_value = sum(float(row.get("market_value") or 0) for row in cleaned)
            if total_market_value > total_asset * 1.000001:
                raise ValueError("持仓总市值不得超过账户总资产")
        self._write_json(self._holdings_path, cleaned)
        removed_codes = set(existing_by_code) - seen_codes
        for removed_code in sorted(removed_codes):
            previous_plan = existing_by_code[removed_code].get("risk_plan") if isinstance(existing_by_code[removed_code], dict) else None
            self.append_holding_risk_event({
                "type": "holding_ended", "code": removed_code,
                "plan_version": int((previous_plan or {}).get("version") or 0),
                "reason": "user_removed_from_holdings",
            })
        if total_asset is not None:
            self._write_json(self._portfolio_meta_path, {
                "total_asset": total_asset,
                "updated_at": self.now_str(),
            })

    def holdings_risk_states(self) -> dict[str, dict]:
        value = self._read_json(self._holdings_risk_path, {})
        if not isinstance(value, dict):
            return {}
        return {str(code): dict(state) for code, state in value.items() if isinstance(state, dict)}

    def save_holding_risk_state(self, code: str, state: dict) -> None:
        normalised = str(code or "").strip().split(".", 1)[0].zfill(6)
        if not re.fullmatch(r"\d{6}", normalised) or not isinstance(state, dict):
            raise ValueError("持仓风险状态不合法")
        with self._lock:
            states = self.holdings_risk_states()
            states[normalised] = dict(state)
            self._write_json(self._holdings_risk_path, states)

    def delete_holding_risk_state(self, code: str) -> None:
        normalised = str(code or "").strip().split(".", 1)[0].zfill(6)
        with self._lock:
            states = self.holdings_risk_states()
            if states.pop(normalised, None) is not None:
                self._write_json(self._holdings_risk_path, states)

    def append_holding_risk_event(self, event: dict) -> dict:
        rec = {
            "event_id": f"hre_{time.time_ns():x}",
            "schema_version": "holding-risk-event-v1",
            "at": now_cn().isoformat(timespec="seconds"),
            **dict(event or {}),
        }
        path = self._holdings_risk_events_path
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_dir(path.parent)
        return rec

    def reset_holding_risk_plan(self, code: str, plan: dict, *, cost: object = None,
                                shares: object = None, reason: str = "manual_replan") -> dict:
        """显式重置计划：版本递增，并清空只能随计划上移的派生状态。"""
        normalised = str(code or "").strip().split(".", 1)[0].zfill(6)
        with self._lock:
            rows = self.holdings()
            row = next((item for item in rows if str(item.get("code") or "").zfill(6) == normalised), None)
            if row is None:
                raise ValueError("持仓不存在")
            previous = row.get("risk_plan") if isinstance(row.get("risk_plan"), dict) else {}
            next_plan = dict(plan or {})
            next_plan["version"] = int(previous.get("version") or 0) + 1
            next_plan["confirmed_reduction_pct"] = 0
            row["risk_plan"] = self._normalise_holding_risk_plan(next_plan, previous=None)
            if cost not in (None, ""):
                parsed_cost = float(cost)
                if not math.isfinite(parsed_cost) or parsed_cost <= 0:
                    raise ValueError("新综合成本必须大于 0")
                row["cost"] = parsed_cost
            if shares not in (None, ""):
                parsed_shares = float(shares)
                if not math.isfinite(parsed_shares) or parsed_shares <= 0:
                    raise ValueError("新持仓数量必须大于 0")
                row["shares"] = parsed_shares
            row["market_value"] = float(row["cost"]) * float(row["shares"])
            row["weight_pct"] = None
            self._write_json(self._holdings_path, rows)
            self.delete_holding_risk_state(normalised)
            self.append_holding_risk_event({
                "type": "plan_reset", "code": normalised,
                "plan_version": row["risk_plan"]["version"], "reason": str(reason or "manual_replan")[:80],
            })
            return dict(row)

    def confirm_holding_reduction(self, code: str, reduction_pct: object) -> dict:
        normalised = str(code or "").strip().split(".", 1)[0].zfill(6)
        try:
            confirmed = int(float(reduction_pct))
        except (TypeError, ValueError):
            raise ValueError("累计降低比例不是有效数字") from None
        if confirmed not in {0, 25, 33, 50, 67, 100}:
            raise ValueError("累计降低比例应为 0、25、33、50、67 或 100")
        with self._lock:
            rows = self.holdings()
            row = next((item for item in rows if str(item.get("code") or "").zfill(6) == normalised), None)
            if row is None:
                raise ValueError("持仓不存在")
            plan = row.get("risk_plan") if isinstance(row.get("risk_plan"), dict) else None
            if plan is None:
                raise ValueError("该持仓尚未建立风险计划")
            previous = int(plan.get("confirmed_reduction_pct") or 0)
            if confirmed < previous:
                raise ValueError("累计降低比例不能小于已确认比例")
            plan["confirmed_reduction_pct"] = confirmed
            plan["updated_at"] = now_cn().isoformat(timespec="seconds")
            ended = confirmed == 100
            if ended:
                rows = [item for item in rows if item is not row]
            self._write_json(self._holdings_path, rows)
            self.append_holding_risk_event({
                "type": "reduction_confirmed", "code": normalised,
                "plan_version": int(plan.get("version") or 1),
                "from_pct": previous, "to_pct": confirmed, "ended": ended,
            })
            if ended:
                self.delete_holding_risk_state(normalised)
            return {"holding": None if ended else dict(row), "ended": ended, "confirmed_reduction_pct": confirmed}

    # ---------- 持仓处理倾向·每日快照（策略自证：昨日建议 vs 今日实际）----------
    # 每个交易日收盘按规则给各持仓确定性处理倾向 + 收盘价，落库快照（按日覆盖、幂等）。
    # 次日用真实价对账"昨日建议兑现没有"——数字全程走规则/真实价、AI 不参与，长期沉淀命中率。
    @property
    def _holdings_advice_path(self) -> Path:
        return self.root / "holdings_advice.json"

    def holdings_advice_all(self) -> dict:
        d = self._read_json(self._holdings_advice_path, {})
        return d if isinstance(d, dict) else {}

    def save_holdings_advice(self, date: str, items: list[dict]) -> None:
        """落库某交易日的持仓处理倾向快照（按日覆盖；只保留最近 120 个日期键防膨胀）。"""
        data = self.holdings_advice_all()
        data[str(date)] = {"generated_at": self.now_str(), "items": list(items or [])}
        if len(data) > 120:
            for k in sorted(data.keys())[:-120]:
                data.pop(k, None)
        self._write_json(self._holdings_advice_path, data)

    def holdings_advice(self, date: str) -> dict | None:
        return self.holdings_advice_all().get(str(date))

    def holdings_advice_prev(self, before_date: str) -> dict | None:
        """读严格早于 before_date 的最近一个快照 → {date, generated_at, items} 或 None。"""
        data = self.holdings_advice_all()
        prior = sorted(d for d in data.keys() if str(d) < str(before_date))
        if not prior:
            return None
        d = prior[-1]
        snap = data.get(d) or {}
        return {"date": d, "generated_at": snap.get("generated_at"), "items": snap.get("items", [])}

    # ---------- 存档 ----------

    @staticmethod
    def _archive_record_day(record: dict, *fields: str) -> str:
        """只从已落库时间字段取日期，不用行情或当前日期猜测。"""
        for field in fields:
            value = str(record.get(field) or "").strip()
            if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
                return value[:10]
        return ""

    @classmethod
    def _archive_business_record(cls, record: dict, date: str) -> bool:
        """判断一条建议事件是否属于该日快照。

        trade_day 是业务归属，created_at 是事件真实落库时点。补归档
        时不得把后来才追加的状态事件倒灌进过去。
        """
        event_day = cls._archive_record_day(
            record, "created_at", "recommendation_time", "rec_time",
        )
        if event_day and event_day > date:
            return False
        trade_day = str(record.get("trade_day") or "").strip()
        return trade_day == date or event_day == date

    def _archive_advice_review(self, date: str, snapshot_at: str) -> dict:
        """冻结当日建议链：候选 -> 正式 -> 触发/执行证据 -> 回验。

        这里只读 append-only 账本和旧表的当时记录；不请求实时行情，
        不重跑回验，不从自由文本猜测触发或成交。
        """
        # 局部 import 避免 knowledge.store <-> analysis.recommend 的模块环。
        from ..analysis.advice_proposal import proposal_ledger_for
        from ..analysis.recommend import event_store_for

        all_proposal_events = proposal_ledger_for(self).events()
        latest_candidates: dict[str, dict] = {}
        for event in all_proposal_events:
            if not isinstance(event, dict) or not self._archive_business_record(event, date):
                continue
            proposal_id = str(event.get("proposal_id") or "").strip()
            if proposal_id:
                latest_candidates[proposal_id] = {
                    **json.loads(json.dumps(event, ensure_ascii=False)),
                    "archive_stage": "candidate",
                    "archive_status": str(event.get("state") or "proposed"),
                }
        candidates = list(latest_candidates.values())

        all_recommendation_events = [
            row for row in event_store_for(self).events() if isinstance(row, dict)
        ]
        recommendation_by_event = {
            str(row.get("event_id")): row
            for row in all_recommendation_events if row.get("event_id")
        }
        recommendation_by_legacy = {
            str(row.get("legacy_rec_id")): row
            for row in all_recommendation_events if row.get("legacy_rec_id")
        }
        formal: list[dict] = []
        included_event_ids: set[str] = set()
        included_legacy_ids: set[str] = set()
        for event in all_recommendation_events:
            if not self._archive_business_record(event, date):
                continue
            item = json.loads(json.dumps(event, ensure_ascii=False))
            item.update({"archive_stage": "formal", "archive_status": "formal"})
            formal.append(item)
            if event.get("event_id"):
                included_event_ids.add(str(event["event_id"]))
            if event.get("legacy_rec_id"):
                included_legacy_ids.add(str(event["legacy_rec_id"]))

        # 旧档案只有 recommendations.json：保留原始字段并明确标成旧格式，
        # 不伪造 event_id / proposal_id / 证据 lineage。
        legacy_rows = self.recommendations()
        legacy_by_id = {
            str(row.get("rec_id")): row
            for row in legacy_rows if isinstance(row, dict) and row.get("rec_id")
        }
        for row in legacy_rows:
            if not isinstance(row, dict) or not self._archive_business_record(row, date):
                continue
            event_id = str(row.get("event_id") or "")
            rec_id = str(row.get("rec_id") or "")
            if event_id in included_event_ids or rec_id in included_legacy_ids:
                continue
            item = json.loads(json.dumps(row, ensure_ascii=False))
            item.update({
                "archive_stage": "formal",
                "archive_status": "formal_legacy",
                "lineage_status": "legacy_unlinked",
            })
            formal.append(item)

        executions: list[dict] = []
        for item in formal:
            trigger = item.get("entry_trigger")
            trigger = trigger if isinstance(trigger, dict) else {}
            trigger_state = str(trigger.get("state") or "unknown")
            if trigger_state not in {
                "triggered", "not_triggered", "not_applicable", "unknown",
            }:
                trigger_state = "unknown"
            executions.append({
                "archive_stage": "execution",
                "archive_status": trigger_state,
                "execution_basis": "entry_trigger_snapshot",
                "broker_receipt_bound": False,
                "recommendation_event_id": item.get("event_id"),
                "rec_id": item.get("legacy_rec_id") or item.get("rec_id"),
                "proposal_id": item.get("proposal_id"),
                "symbol": item.get("symbol") or item.get("code"),
                "name": item.get("name"),
                "direction": item.get("direction") or item.get("rec_type"),
                "entry_condition": item.get("entry_condition") or item.get("buy_condition"),
                "trigger": json.loads(json.dumps(trigger, ensure_ascii=False)),
            })

        reviews: list[dict] = []
        for review in self.rec_reviews():
            if not isinstance(review, dict):
                continue
            if self._archive_record_day(review, "review_time", "created_at") != date:
                continue
            event_id = str(review.get("recommendation_event_id") or "")
            rec_id = str(review.get("rec_id") or "")
            linked = recommendation_by_event.get(event_id) or recommendation_by_legacy.get(rec_id)
            legacy_linked = legacy_by_id.get(rec_id)
            item = json.loads(json.dumps(review, ensure_ascii=False))
            hit_status = str(review.get("hit_status") or "")
            evaluation_state = str(review.get("evaluation_state") or "")
            archive_status = (
                evaluation_state
                if hit_status in {"", "pending"}
                and evaluation_state in {"incomparable", "untriggered"}
                else hit_status or evaluation_state or "pending"
            )
            item.update({
                "archive_stage": "review",
                "archive_status": archive_status,
                "symbol": (linked or {}).get("symbol") or (legacy_linked or {}).get("code"),
                "name": (linked or {}).get("name") or (legacy_linked or {}).get("name"),
                "direction": (
                    (linked or {}).get("direction")
                    or (legacy_linked or {}).get("direction")
                    or (legacy_linked or {}).get("rec_type")
                ),
                "lineage_status": (
                    "linked" if linked or legacy_linked else "review_source_unavailable"
                ),
            })
            reviews.append(item)

        def counts(rows: list[dict], field: str) -> dict[str, int]:
            result: dict[str, int] = {}
            for row in rows:
                status = str(row.get(field) or "unknown")
                result[status] = result.get(status, 0) + 1
            return result

        return {
            "schema_version": "daily-advice-archive.v1",
            "snapshot_at": snapshot_at,
            "historical_read_only": True,
            "realtime_recalculation": False,
            "execution_scope": "entry_trigger_only_no_broker_execution",
            "candidates": candidates,
            "formal_recommendations": formal,
            "executions": executions,
            "reviews": reviews,
            "summary": {
                "candidate": {"total": len(candidates), "by_status": counts(candidates, "archive_status")},
                "formal": {"total": len(formal), "by_status": counts(formal, "archive_status")},
                "execution": {"total": len(executions), "by_status": counts(executions, "archive_status")},
                "review": {"total": len(reviews), "by_status": counts(reviews, "archive_status")},
            },
        }

    def archive_day(self, date: str | None = None, sentiment: dict | None = None) -> dict:
        date = date or self.today()
        archive_path = self.root / "archive" / f"{date}.json"
        with self._lock:
            existing = self._read_json(archive_path, None)
            if isinstance(existing, dict):
                # 日档案是当时事实；重试必须幂等，不用今天的账本/行情
                # 覆写旧档案。旧 schema 也原样返回，由前端显式标记“未留存”。
                return existing
            if archive_path.exists():
                raise RuntimeError("历史日档案无法读取，已拒绝用当前数据重算覆写")
            reports = self.reports_of(date)
            # 当日新增知识（含用户「存入知识库」的产业链/单公司分析）——让 spec 的「持仓、产业链分析记录」
            # 也能按日回溯（此前只存了 6 类研报）。产业链分析按需保存到知识库即随当日归档落盘。
            knowledge_today = [
                {"category": i.get("category"), "title": i.get("title"),
                 "content": i.get("content"), "created_at": i.get("created_at")}
                for i in self.knowledge_items()
                if str(i.get("created_at", "")).startswith(date)
            ]
            archived_at = self.now_str()
            bundle = {
                "schema_version": "daily-archive-v2",
                "ok": True,
                "date": date,
                "reports": {
                    k: {
                        key: v.get(key)
                        for key in (
                            "schema_version", "rtype", "date", "title", "content_md",
                            "content_id", "content_hash", "report_version", "plan_id",
                            "plan_version", "created_at", "pushed", "input_lineage",
                            "input_context_hash", "input_snapshot_file",
                            "advice_governance",
                        )
                        if key in v
                    }
                    for k, v in reports.items()
                },
                "holdings": self.holdings(),          # 持仓快照（当日收盘时点）
                "knowledge_today": knowledge_today,   # 当日新增知识（含保存的产业链/公司分析）
                "sentiment": sentiment or {},
                "advice_review": self._archive_advice_review(date, archived_at),
                "archived_at": archived_at,
            }
            self._write_json(archive_path, bundle)
            return bundle

    def archive_dates(self) -> list[str]:
        folder = self.root / "archive"
        return sorted((f.stem for f in folder.glob("*.json")), reverse=True)

    def get_archive(self, date: str) -> dict | None:
        return self._read_json(self.root / "archive" / f"{date}.json", None)

    # ---------- 观察池（母文档 §21：想买还没买、等价格触发再买）----------
    # 存 watchlist.json。条目只存"事实+用户录入"：代码/名称/方向/理由/理想买入区间/
    # 买入触发条件/不买条件/放弃条件+放弃价/风险等级/备注。
    # 铁律：这里不存现价、不存评级——现价取自实时行情、A–E 评级是路由层基于"真实现价 vs
    # 理想区间"的确定性计算，绝不把任何推算数字落库冒充事实。区间/放弃价 float() 失败或≤0
    # 抛 ValueError（路由层转 400），宁缺勿造（股价不可能为零/负）。

    @property
    def _watchlist_path(self) -> Path:
        return self.root / "watchlist.json"

    @staticmethod
    def _opt_float(value, field: str):
        """可选价格字段：空(None/"")→None；有值则必须能 float() 且为正数，否则抛 ValueError。
        绝不静默吞掉非法输入（会让用户以为存进了一个价位、实则丢失或存了无意义的≤0价）。"""
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field} 不是有效数字") from None
        if f <= 0:
            raise ValueError(f"{field} 应为正数")
        return f

    def watchlist(self) -> list[dict]:
        return self._read_json(self._watchlist_path, [])

    @staticmethod
    def _watchlist_record(item: dict, wid: str) -> dict:
        code = str(item.get("code", "")).strip()
        if not code:
            raise ValueError("股票代码不能为空")
        try:
            risk = int(item.get("risk_level") or 3)
        except (TypeError, ValueError):
            raise ValueError("风险等级应为整数") from None
        risk = min(5, max(1, risk))
        return {
            "id": wid,
            "code": code.zfill(6),
            "name": str(item.get("name", ""))[:30],
            "market": str(item.get("market", "") or "a股")[:20],
            "sector": str(item.get("sector", ""))[:30],
            "add_reason": str(item.get("add_reason", ""))[:500],
            "target_low": Store._opt_float(item.get("target_low"), "理想买入下沿"),
            "target_high": Store._opt_float(item.get("target_high"), "理想买入上沿"),
            "trigger_condition": str(item.get("trigger_condition", ""))[:500],
            "no_buy_condition": str(item.get("no_buy_condition", ""))[:500],
            "abandon_condition": str(item.get("abandon_condition", ""))[:300],
            "abandon_price": Store._opt_float(item.get("abandon_price"), "放弃价"),
            "risk_level": risk,
            "note": str(item.get("note", ""))[:300],
            "created_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @property
    def _watchlist_zones_path(self) -> Path:
        return self.root / "watchlist_zones.json"

    def watchlist_zones(self) -> dict[str, dict]:
        """当前派生区间快照；不是用户输入，也不覆盖观察池事实。"""
        payload = self._read_json(self._watchlist_zones_path, {})
        return payload if isinstance(payload, dict) else {}

    def save_watchlist_zones(self, zones: dict[str, dict]) -> None:
        clean = {
            str(code).strip().zfill(6): row
            for code, row in (zones or {}).items()
            if isinstance(row, dict)
        }
        self._write_json(self._watchlist_zones_path, clean)

    def upsert_watchlist_zone(self, code: str, zone: dict) -> None:
        with self._lock:
            zones = self.watchlist_zones()
            zones[str(code).strip().zfill(6)] = zone
            self._write_json(self._watchlist_zones_path, zones)

    def delete_watchlist_zone(self, code: str) -> None:
        normalized = str(code).strip().zfill(6)
        with self._lock:
            zones = self.watchlist_zones()
            if normalized in zones:
                zones.pop(normalized, None)
                self._write_json(self._watchlist_zones_path, zones)

    def add_watch(self, item: dict) -> str:
        if not isinstance(item, dict):
            raise ValueError("观察池条目应为对象")
        wid = f"w_{time.time_ns()}"
        record = self._watchlist_record(item, wid)
        with self._lock:
            items = self.watchlist()
            items.append(record)
            self._write_json(self._watchlist_path, items)
        return wid

    def import_watchlist(self, candidates: list[dict]) -> dict:
        """一次性追加观察项；已有代码、文件内重复及非 A 股代码均跳过。"""
        if not isinstance(candidates, list):
            raise ValueError("导入条目应为数组")
        if not candidates or len(candidates) > 200:
            raise ValueError("单次导入应包含 1—200 条记录")

        added_codes: list[str] = []
        duplicate_codes: list[str] = []
        invalid: list[dict] = []
        with self._lock:
            items = self.watchlist()
            known_codes = {
                str(row.get("code") or "").strip().zfill(6)
                for row in items
                if isinstance(row, dict) and str(row.get("code") or "").strip()
            }
            for index, candidate in enumerate(candidates):
                if not isinstance(candidate, dict):
                    invalid.append({"index": index, "code": "", "reason": "条目不是对象"})
                    continue
                code = str(candidate.get("code") or "").strip()
                if not _A_SHARE_CODE_RE.fullmatch(code):
                    invalid.append({"index": index, "code": code, "reason": "不是可识别的 6 位 A 股代码"})
                    continue
                if code in known_codes:
                    duplicate_codes.append(code)
                    continue
                payload = {
                    "code": code,
                    "name": candidate.get("name", ""),
                    "sector": candidate.get("sector", ""),
                    "market": "a股",
                }
                record = self._watchlist_record(payload, f"w_{time.time_ns()}_{index}")
                items.append(record)
                known_codes.add(code)
                added_codes.append(code)
            if added_codes:
                self._write_json(self._watchlist_path, items)
        return {
            "added_codes": added_codes,
            "duplicate_codes": duplicate_codes,
            "invalid": invalid,
        }

    def update_watch(self, wid: str, fields: dict) -> bool:
        if not isinstance(fields, dict):
            raise ValueError("更新字段应为对象")
        items = self.watchlist()
        hit = next((i for i in items if i.get("id") == wid), None)
        if hit is None:
            return False
        # 只允许更新业务字段；id/created_at 不可改。code 用 zfill6；区间/放弃价走 _opt_float 校验。
        if "code" in fields:
            code = str(fields.get("code", "")).strip()
            if not code:
                raise ValueError("股票代码不能为空")
            hit["code"] = code.zfill(6)
        for k, lim in (("name", 30), ("market", 20), ("sector", 30), ("add_reason", 500),
                       ("trigger_condition", 500), ("no_buy_condition", 500),
                       ("abandon_condition", 300), ("note", 300)):
            if k in fields:
                hit[k] = str(fields.get(k, ""))[:lim]
        if "target_low" in fields:
            hit["target_low"] = self._opt_float(fields.get("target_low"), "理想买入下沿")
        if "target_high" in fields:
            hit["target_high"] = self._opt_float(fields.get("target_high"), "理想买入上沿")
        if "abandon_price" in fields:
            hit["abandon_price"] = self._opt_float(fields.get("abandon_price"), "放弃价")
        if "risk_level" in fields:
            try:
                hit["risk_level"] = min(5, max(1, int(fields.get("risk_level") or 3)))
            except (TypeError, ValueError):
                raise ValueError("风险等级应为整数") from None
        self._write_json(self._watchlist_path, items)
        return True

    def delete_watch(self, wid: str) -> bool:
        items = self.watchlist()
        kept = [i for i in items if i.get("id") != wid]
        if len(kept) == len(items):
            return False
        self._write_json(self._watchlist_path, kept)
        return True

    # ---------- ETF 观察（与 A 股观察池隔离，不生成双周期区间） ----------

    @property
    def _etf_watchlist_path(self) -> Path:
        return self.root / "etf_watchlist.json"

    @property
    def _etf_snapshots_path(self) -> Path:
        return self.root / "etf_watch_snapshots.json"

    def etf_watchlist(self) -> list[dict]:
        payload = self._read_json(self._etf_watchlist_path, [])
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _etf_watch_record(item: dict, wid: str, *, created_at: str | None = None) -> dict:
        code = str(item.get("code") or "").strip().split(".", 1)[0]
        if not re.fullmatch(r"(?:5\d{5}|1[56]\d{4})", code):
            raise ValueError("ETF代码应为沪深交易型基金代码")
        return {
            "id": wid,
            "code": code,
            "name": str(item.get("name") or "")[:60],
            "group": str(item.get("group") or "")[:30],
            "note": str(item.get("note") or "")[:300],
            "created_at": created_at or now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def add_etf_watch(self, item: dict) -> str:
        if not isinstance(item, dict):
            raise ValueError("ETF观察条目应为对象")
        wid = f"etf_{time.time_ns()}"
        record = self._etf_watch_record(item, wid)
        with self._lock:
            items = self.etf_watchlist()
            if any(str(row.get("code") or "") == record["code"] for row in items if isinstance(row, dict)):
                raise ValueError("该ETF已在观察列表中")
            items.append(record)
            self._write_json(self._etf_watchlist_path, items)
        return wid

    def import_etf_watch(self, candidates: list[dict]) -> dict:
        if not isinstance(candidates, list) or not candidates or len(candidates) > 50:
            raise ValueError("单次添加应包含1—50只ETF")
        added_codes: list[str] = []
        duplicate_codes: list[str] = []
        with self._lock:
            items = self.etf_watchlist()
            known = {str(row.get("code") or "") for row in items if isinstance(row, dict)}
            for index, candidate in enumerate(candidates):
                record = self._etf_watch_record(candidate, f"etf_{time.time_ns()}_{index}")
                if record["code"] in known:
                    duplicate_codes.append(record["code"])
                    continue
                items.append(record)
                known.add(record["code"])
                added_codes.append(record["code"])
            if added_codes:
                self._write_json(self._etf_watchlist_path, items)
        return {"added_codes": added_codes, "duplicate_codes": duplicate_codes}

    def update_etf_watch(self, wid: str, fields: dict) -> bool:
        if not isinstance(fields, dict):
            raise ValueError("更新字段应为对象")
        with self._lock:
            items = self.etf_watchlist()
            hit = next((row for row in items if isinstance(row, dict) and row.get("id") == wid), None)
            if hit is None:
                return False
            merged = {**hit, **{key: fields[key] for key in ("name", "group", "note") if key in fields}}
            updated = self._etf_watch_record(merged, wid, created_at=str(hit.get("created_at") or ""))
            hit.clear()
            hit.update(updated)
            self._write_json(self._etf_watchlist_path, items)
        return True

    def delete_etf_watch(self, wid: str) -> bool:
        with self._lock:
            items = self.etf_watchlist()
            kept = [row for row in items if not isinstance(row, dict) or row.get("id") != wid]
            if len(kept) == len(items):
                return False
            self._write_json(self._etf_watchlist_path, kept)
        return True

    def etf_watch_snapshots(self) -> dict[str, dict]:
        payload = self._read_json(self._etf_snapshots_path, {})
        return payload if isinstance(payload, dict) else {}

    def upsert_etf_watch_snapshot(self, code: str, snapshot: dict) -> None:
        normalized = str(code or "").strip().split(".", 1)[0]
        if not re.fullmatch(r"(?:5\d{5}|1[56]\d{4})", normalized):
            raise ValueError("ETF代码应为沪深交易型基金代码")
        if not isinstance(snapshot, dict):
            raise ValueError("ETF资料快照应为对象")
        with self._lock:
            snapshots = self.etf_watch_snapshots()
            snapshots[normalized] = dict(snapshot)
            self._write_json(self._etf_snapshots_path, snapshots)

    # ---------- 推荐留痕与成功率（母文档 §29；append-only，历史判断固定不改写）----------
    # 推荐落库即固定（status=active），后续只 append 验证、绝不改写本记录。

    _REC_TEXT_FIELDS = (
        "source", "rec_type", "rec_reason", "buy_condition", "sell_condition",
        "stop_loss_condition", "take_profit_condition", "market_state",
        "sector_state", "capital_state", "risk_level",
    )

    @property
    def _recommendations_path(self) -> Path:
        return self.root / "recommendations.json"

    @property
    def _rec_reviews_path(self) -> Path:
        return self.root / "rec_reviews.json"

    @property
    def _rec_reviews_log_path(self) -> Path:
        return self.root / "recommendation_events" / "reviews.v1.jsonl"

    def recommendations(self) -> list[dict]:
        return self._read_json(self._recommendations_path, [])

    def add_rec(self, item: dict) -> str:
        """新增一条推荐留痕，返回 rec_id（前缀 r_）。rec_price 必须是有效正数（真实成交/盘口价或
        用户录入）；float() 失败或 ≤0 抛 ValueError。落库即固定为历史判断，后续只 append 验证。"""
        if not isinstance(item, dict):
            raise ValueError("推荐应为对象")
        code = str(item.get("code", "")).strip()
        if not code:
            raise ValueError("缺少股票代码")
        try:
            rec_price = float(item.get("rec_price"))
        except (TypeError, ValueError):
            raise ValueError("推荐价不是有效数字") from None
        if rec_price <= 0:
            raise ValueError("推荐价应为正数（真实成交/盘口价或用户录入）")
        requested_rid = str(item.get("_rec_id") or "").strip()[:120]
        if requested_rid and not requested_rid.startswith("r_"):
            raise ValueError("预分配 rec_id 格式不合法")
        rid = requested_rid or f"r_{time.time_ns():x}"
        rec = {
            "rec_id": rid,
            "code": code.zfill(6),
            "name": str(item.get("name", ""))[:30],
            "market": str(item.get("market", "") or "A股")[:10],
            "rec_price": rec_price,
            "rec_time": (str(item.get("rec_time", "")).strip()[:19] or self.now_str()),
            "expected_holding_days": str(item.get("expected_holding_days", ""))[:10],
            "status": "active",
            "score_eligible": bool(item.get("_server_validated") and item.get("_score_eligible")),
            "provenance_status": (
                "verified" if bool(item.get("_server_validated") and item.get("_score_eligible"))
                else "unverified"
            ),
            "created_at": self.now_str(),
        }
        # 数值可选字段 verify_days（推荐自带验证窗口，如量化选股次日买入=5日）：之前只收白名单文本字段，
        # 数值 verify_days 被丢 → recommend.verify_one 读不到、退回默认1日窗口（T+1 修复曾被此静默架空）。
        _vd = item.get("verify_days")
        if _vd not in (None, "", 0):
            try:
                rec["verify_days"] = int(_vd)
            except (TypeError, ValueError):
                pass
        for f in self._REC_TEXT_FIELDS:
            rec[f] = str(item.get(f, ""))[:500]
        with self._lock:
            items = self.recommendations()
            existing = next((row for row in items if row.get("rec_id") == rid), None)
            if existing is not None:
                if (
                    str(existing.get("code") or "") != rec["code"]
                    or float(existing.get("rec_price") or 0) != rec_price
                ):
                    raise ValueError("rec_id 已存在且内容不一致")
                return rid
            items.append(rec)
            self._write_json(self._recommendations_path, items[-1000:])
        return rid

    def rec_reviews(self) -> list[dict]:
        legacy = self._read_json(self._rec_reviews_path, [])
        rows = list(legacy) if isinstance(legacy, list) else []
        path = self._rec_reviews_log_path
        if path.exists():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
                except (TypeError, ValueError):
                    continue
        return rows

    def save_rec_review(self, rec_id: str, review: dict) -> dict:
        """追加一条验证记录（§35.5）。**append-only：绝不覆盖任何旧验证**
        —— 历史判断固定、不事后改写、不重新问一次换套说法（母文档 §29 铁律）。"""
        rid = str(rec_id).strip()
        if not rid:
            raise ValueError("缺少 rec_id")
        rv = dict(review or {})
        # error_type 白名单（白皮书吸收③）：非法值直接丢弃该键（不落库脏枚举，也不因此拒绝
        # 整条验证记录）。枚举唯一来源=本模块 ERROR_TYPES（analysis.recommend 从这里 import，
        # 单一数据源防两处硬编长期漂移——07-06 审计裁定）。
        if rv.get("error_type") is not None and rv.get("error_type") not in ERROR_TYPES:
            rv.pop("error_type", None)
        rv["rec_id"] = rid
        rv.setdefault("review_time", self.now_str())
        review_day = str(rv.get("review_time") or self.now_str())[:10]
        status = str(rv.get("hit_status") or "pending")
        window = str(rv.get("window") or "legacy")[:12]
        rv.setdefault("schema_version", "2.0" if window != "legacy" else "1.0")
        rv.setdefault("event_type", "recommendation_reviewed")
        rv["review_id"] = str(rv.get("review_id") or "rr_" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"hermes-review:{rid}:{window}:{review_day}:{status}",
        ).hex)
        with self._lock:
            existing = next(
                (row for row in self.rec_reviews() if row.get("review_id") == rv["review_id"]),
                None,
            )
            if existing is not None:
                return existing
            path = self._rec_reviews_log_path
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(rv, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return rv

    def update_rec_status(self, rec_id: str, status: str) -> bool:
        """更新推荐状态（active/closed）。仅改状态/时间戳，不动历史判断字段。"""
        if status not in ("active", "closed"):
            raise ValueError("状态应为 active/closed")
        with self._lock:
            items = self.recommendations()
            hit = False
            for it in items:
                if it.get("rec_id") == rec_id:
                    it["status"] = status
                    it["updated_at"] = self.now_str()
                    hit = True
            if hit:
                self._write_json(self._recommendations_path, items)
            return hit
